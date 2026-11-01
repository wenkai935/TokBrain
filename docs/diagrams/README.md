# 架构图索引

用 [Archify](https://github.com/tt-a1i/archify) 生成的自包含 HTML 图，双击即可打开
（内置查看器：主题切换、缩放、搜索、聚焦、链路追踪、演示模式、PNG/SVG 导出）。

改图就改同名的 `.json`（Archify IR），然后重新生成，不要手改 HTML。

```bash
# 主链路图（showcase 档）
node <archify>/bin/archify.mjs validate workflow docs/diagrams/tokbrain-flow.workflow.json --quality showcase
node <archify>/bin/archify.mjs deliver  workflow docs/diagrams/tokbrain-flow.workflow.json docs/diagrams/tokbrain-flow.html --quality showcase

# 边界图（standard 档：内容优先，画布更宽）
node <archify>/bin/archify.mjs validate architecture docs/diagrams/tokbrain-boundary.architecture.json --quality standard
node <archify>/bin/archify.mjs deliver  architecture docs/diagrams/tokbrain-boundary.architecture.json docs/diagrams/tokbrain-boundary.html --quality standard

# 三条入料路径总览 + F2 解析细节（standard 档）
node <archify>/bin/archify.mjs deliver architecture docs/diagrams/tokbrain-intake-paths.architecture.json docs/diagrams/tokbrain-intake-paths.html --quality standard
node <archify>/bin/archify.mjs deliver architecture docs/diagrams/tokbrain-f2-resolve.architecture.json docs/diagrams/tokbrain-f2-resolve.html --quality standard
```

| 图 | 类型 | 讲什么 | 什么时候看 |
|---|---|---|---|
| [tokbrain-flow.html](tokbrain-flow.html) | workflow | 内在功能链路：入口 → 导入 → 预检 → 确认 → 入队解析 → 落库 → 检索，附运维节点 | 想搞清楚「一条视频怎么进知识库」「为什么确认了还没解析」 |
| [tokbrain-boundary.html](tokbrain-boundary.html) | architecture | 进程与信任边界：三条 MCP 通道、唯一写路径、回环范围、NAS 只读侧 | 想搞清楚「谁能写库」「新开了哪个端口」「PC 关机时什么还能用」 |
| [tokbrain-intake-paths.html](tokbrain-intake-paths.html) | architecture | 三条入料路径：手机 → 飞书表 → 桥 → 预检 → F2 → 入库；网页采集（pull / dom-collect）；HTTP 入站旁路 | 想搞清楚「视频从哪进来」「平台采集的条目为什么按平台走不同的落点」 |
| [tokbrain-f2-resolve.html](tokbrain-f2-resolve.html) | architecture | 预检展开：建批 → 归一化 → 入队 → 3 worker → 取 cookie → F2 解析 → 落 Work | 想搞清楚「F2 到底在哪一步被调用」「限流和熔断卡在哪」 |

两张图都带分场景视图（图上方切换），比如「主链路 / 外部 Agent 通道 / 异常与重试 / 运维链路」。

## 图里刻意表达的三件事

1. **闸门只有一处**：`预检 ≠ 入库`。`tb_batch_confirm` 只建 `pending` 作品，
   必须再 `tb_library_ingest` 才真正解析。这是最容易被误判的环节。
2. **单 worker 串行**：`ingest` 只有一个 worker，批量必排队；进度只能轮询，
   没有 SSE。`tb_job_wait` 超过 90 秒要续调，不是失败。
3. **唯一写路径**：所有写操作最终都打到 `127.0.0.1:8001`，控制面不碰数据库。
   所以业务规则只有一份。

## 校验状态

两张图都是 **0 错误**、9 项 artifact 检查通过。

| 图 | 质量档 | 说明 |
|---|---|---|
| `tokbrain-flow.html` | **showcase** | 4 张说明卡片 + 12 节点 13 边，composition 0 error / 0 warning |
| `tokbrain-boundary.html` | **standard** | 4 张说明卡片 + 节点文案写全；showcase 档会因画布偏宽被判 `desktop-readability` 不达标，按"内容完整优先"选 standard |
| `tokbrain-intake-paths.html` | **standard** | 12 节点 11 边、3 个分区框；画布 1700px，showcase 档卡在 `desktop-readability` |
| `tokbrain-f2-resolve.html` | **standard** | 10 节点 9 边、蛇形折返（第一行建批链，第二行解析链），连线全正交无穿节点 |

取舍原则：**信息完整 > 塞进单一视口**。图偏宽时用查看器的缩放/演示模式看，
而不是删掉节点与说明。`visual-check` 的 `containment` 会报纵向溢出（6 条泳道 +
卡片，页面高约 1600px），这是预期的——`readability` 是通过的（最小投影字号 7.6px ≥ 6px），
强行压缩泳道会让 `job → issues` 这类回边无路可走。
