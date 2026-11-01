# TokBrain → Hermes NAS 运行手册

> **时效声明（2026-10-30 校订）**：对应应用 v1.0.0 / schema v11。本文描述的是 Hermes 专用的 **stdio 只读副本**，与仓库里另一套「本机 8010 网络 MCP」不是同一组件（见文末对照）。

## 当前部署

- 数据库：`/vol1/1000/tokbrain/data/douyin_rag.db`，只读权限。
- MCP：`/vol1/1000/tokbrain/bin/tokbrain_hermes_stdio.py`，由 Hermes 按 stdio 启动，不监听网络端口。
- Hermes 配置：`/vol1/1000/hermes-state/config.yaml` 的 `mcp_servers.tokbrain`。
- 首次配置备份：`/vol1/1000/hermes-state/backups/config-before-tokbrain-*.yaml`。
- 副本验收基线：**14 个已入库作品 / 305 个知识片段**（2026-10-30 部署时核对）。

## Hermes 调用规则

1. 仅当问题可能涉及用户收藏的视频或已保存资料时调用 TokBrain。
2. 先调用 `tokbrain_search(query, limit=3)`；`snippet` 足够时立即回答。
3. 缺少关键结论时只对最佳命中调用一次 `tokbrain_get_work`。
4. 只有用户追问细节或原话时才调用 `tokbrain_get_transcript`，范围取命中时间前后各 30 秒，默认 4 段、最多 8 段。
5. 每个问题最多三次 TokBrain 调用，不得用相同参数重复查询。
6. 只能依据工具证据回答；无命中要明确说明。回答附《标题》和时间戳，用户需要时再给 URL。

## 更新 NAS 知识副本

在项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy_tokbrain_snapshot.ps1
```

脚本使用 SQLite backup API 生成一致快照，执行完整性检查，上传为 `.new`，在 NAS 再次检查后原子替换；旧副本保存为 `douyin_rag.db.bak`。

快照替换后，Hermes 侧要重启 `hermes-household` 才会重新打开新的副本文件；不重启会继续用旧快照作答。

## 两套 MCP 的对照

| | Hermes stdio（本文） | 本机网络 MCP | 控制面（8011） |
|---|---|---|---|
| 脚本 | `mcp_server/tokbrain_hermes_stdio.py` | `mcp_server/tokbrain_mcp.py` | `mcp_server/mcp_control.py` |
| 运行位置 | NAS | 本机 Windows | 本机 Windows |
| 传输 | stdio，被 Hermes 拉起 | HTTP，默认 `127.0.0.1:8010` | HTTP，`192.168.10.100:8011` |
| 工具名 | `tokbrain_search` / `tokbrain_get_work` / `tokbrain_get_transcript` / `tokbrain_list_collections` / `tokbrain_stats` | `kb_search` / `kb_get_work` / `kb_get_transcript` / `kb_list_collections` / `kb_stats` | `tb_*`（33 个） |
| 数据来源 | NAS 上的只读快照（`mode=ro&immutable=1`） | 本机 `data/douyin_rag.db` | 本机 `127.0.0.1:8001` 的 REST |
| 鉴权 | 无（不开放端口） | 非回环地址**必须**配 `TOKBRAIN_MCP_TOKEN`，否则启动失败 | 显式开关 + 非回环**必须**配 `TOKBRAIN_CONTROL_TOKEN`（≥32 位） |
| 能力 | 只读 | 只读 | **读写**：导入 / 触发入库 / 重试 / 删除 / 合集 / 服务启停 |

前两套仍然只读。要让 Hermes 真正驱动全链路，注册第三套（见
`mcp_server/hermes-control-config.example.yaml` 与
《[控制面运行手册](tokbrain-control-plane.md)》）。

分工原则：**离线快照回答历史问题，控制面处理实时操作**。PC 关机时只有 stdio 那套还能用，
所以不要因为加了控制面就把 stdio 摘掉。

## 验证

```bash
python3 /vol1/1000/tokbrain/bin/probe_tokbrain_mcp.py
python3 /vol1/1000/tokbrain/bin/probe_hermes_tokbrain.py
curl --max-time 5 -fsS http://127.0.0.1:9119/api/status
```

## 回滚

```bash
cp -p /vol1/1000/tokbrain/data/douyin_rag.db.bak /vol1/1000/tokbrain/data/douyin_rag.db
sudo systemctl restart hermes-household
```

配置回滚时，把 `config-before-tokbrain-*.yaml` 中需要的版本复制回 `config.yaml`，再重启 `hermes-household`。不要同时运行两个可写 TokBrain 数据库，也不要通过 SMB/NFS 远程直接打开 SQLite。
