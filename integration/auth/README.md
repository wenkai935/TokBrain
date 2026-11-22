# xiaoqi-auth —— 小七全局统一认证中心（阶段1实现）

> 落地 2026-11-19《统一认证中心方案》，并按 2026-11-22 要求扩展为**全局**边界：脚本调用、命令执行、服务通讯统一走它。
> 轻量自建、零外部依赖、分批接入。状态：**本地实现与测试完成（46 项通过），未部署到 NAS**。

## 一句话

给每个调用方一个身份（`client_id` + secret），用 client credentials 换 **15 分钟短期 JWT**，票据按目标资源（`aud`）收窄权限（`scope`），下游验签 + 校验，每次签发与校验都留审计。**一个边界管住所有自动化调用**：脚本、命令、服务通讯。本地 stdio MCP 保持环境变量（规范推荐）。**不碰语音"认人"**（那是 fail-closed 证据校验的设计选择）。

## 为什么不是 MCP OAuth

MCP 官方 2026-07-28 规范原文：stdio 传输「**SHOULD NOT follow this specification，从环境变量取凭据**」。小七的 MCP 全是 Hermes 拉起的 stdio 子进程，走的就是环境变量这条路——缺的不是"客户端授权"，而是**服务间互认**，所以自建轻量中心即可，不需要浏览器授权/PKCE/网关集群。

## 组件

| 文件 | 作用 |
|---|---|
| `auth_server.py` | 认证中心：`/token` 签发、`/verify` 验签、`/jwks` 说明、`/audit` 查询、`/health` |
| `auth_client.py` | 调用方 SDK：取 token（带缓存+提前续期）、委托验签 |
| `music_auth_guard.py` | 音乐桥写端点的薄校验中间件（GET 放行、POST 验 token） |
| `config.example.json` | **全局**服务注册表（8 个 client / 6 个 aud / 分级 scope，含说明），secret 占位 |
| `gen_config.py` | 生成真 `config.json`（随机 secret、chmod 600） |
| `ha_proxy.py` | **HA 反向代理示范**：验票 + scope 门禁后才带 HA token 转发，业务 server 不再持有 HA token |
| `music_auth_guard.py` | 音乐桥写端点薄校验（GET 放行、POST 验票） |
| `music_server.py.auth-fix` | 音乐桥 server.py 鉴权版（基于容器真身 `bf3b46e0`，+2 处） |
| `home_mcp.py.auth-fix` | Hermes music 工具带 token 版（基于 music-fix，加 `_music_token` 缓存） |
| `test_auth.py` / `test_music_guard.py` / `test_ha_proxy.py` | 端到端测试（25 + 8 + 13 = 46 项） |
| `deploy-auth.sh` / `rollback-auth.sh` | 部署 / 回滚脚本 |
| `全局接入清单.md` | **分批接入路线图**：每条调用路径的现状/目标/改造要点/优先级/明确不纳入项 |

## 设计要点

- **HS256 对称密钥**：单家庭、服务都在同一可信 NAS，够用且零依赖（不需要 pyjwt/cryptography）。将来跨网段再换 RS256 + mTLS。
- **短期 token（900s）**：`jti` 便于审计与吊销；过期必须重取。
- **按 aud 收窄 scope**：一个 client 可能有多个 aud 的权限，但换票时**只给它该 aud 的 scope**——给音乐桥的票里不会出现 ha:light:write（纵深防御，已测）。
- **fail-closed**：auth 中心不可达时，资源方**拒绝**（不让"认证挂了"变成"谁都能调"）。
- **HA 反代持 key**：HA_TOKEN 只活在反代一处，业务 server 拿票据换转发，不再持有 HA 令牌。
- **兼容开关**：home_mcp 的 `XIAOQI_AUTH_URL` 没配就不带 token，兼容认证中心未部署的现状。

## 全局权限模型

8 个 client（hermes-voice / hermes-home / home-rules / home-history / life-agent / file-bridge / pc-bridge / scripts-runner）、6 个 aud（music-bridge / ha-proxy / tv-helper / xiaozhi-gateway / command-exec / file-service）、按域分级的 scope。完整映射见 `config.example.json` 与 `全局接入清单.md`。

| 层 | 已接 | 待接 |
|---|---|---|
| 资源 | music-bridge、ha-proxy（示范） | tv-helper、xiaozhi-gateway、command-exec、file-service |
| 调用方 | hermes-voice（音乐）、hermes-home（音乐） | home-rules/home-history（HA）、life-agent（播报）、pc-bridge、scripts-runner 等 |

## 本地测试（46 项全过）

```
python3 test_auth.py          # 25：签发/验签/过期/篡改/越权/scope按aud收窄/缓存/审计
python3 test_music_guard.py   # 8：GET放行/无票拒/合法通过/scope不足/fail-closed
python3 test_ha_proxy.py      # 13：路径白名单/scope门禁/HA token注入/审计
```

## 部署（未执行，按规矩先审阅）

1. 本地跑 `gen_config.py` 生成 `config.json`（含真 secret，chmod 600，不入库）。
2. 同步整个目录到 NAS `/vol1/1000/xiaoqi-auth/`。
3. 在 NAS 跑 `deploy-auth.sh`：备份 → 部署 auth systemd 服务 → 改 home_mcp + gateway env → 改音乐桥 server.py/guard/compose → 重建音乐桥 + 重启 gateway → 验证。
4. 验证点：`/music/status` 200（GET 放行）、POST `/music/command` 无 token 401、`/audit` 有记录。
5. 出问题跑 `rollback-auth.sh [STAMP]`（先关音乐桥鉴权，再回滚 home_mcp，再停 auth）。

**注意**：部署会让语音/音乐中断约 10-20 秒（重建容器 + 重启 gateway）。

## 未接的部分（分批，见全局接入清单）

- **批次 A**（高价值低风险）：电视 helper、HA 只读/写入 MCP 改走反代、语音播报——清单里给了每个的 client/scope/改造要点。
- **批次 B**（服务间治理）：小智→Hermes key 迁移（与 `8k8652pd` 轮换一起）、音箱身份（侵入固件，最后做）、定时任务。
- **批次 C**（高风险按需）：pc_control 命令执行、run_shell 逐条鉴权、运维脚本。
- **明确不纳入**：本地 stdio MCP 启动（规范推荐环境变量）、语音认人（设计选择）、HA 自身 token 机制（保留在反代内）。
- **阶段2 mTLS / Authentik**：按需，跨网段时再上。

## 与开源/密钥轮换的衔接

发现两个真密钥（和风天气 `a861d0d5…`、Hermes 网关 `8k8652pd…`），轮换记录见 `D:/code/xiaoqi/开源准备/开源前检查清单.md`。注意：`8k8652pd…` 是小智调 Hermes 的 key，与本认证中心的 client secret 是**两套东西**——前者是小智→Hermes 的既有鉴权，后者是 Hermes→音乐桥的新鉴权，不要混。

## Verdict

全局认证中心的**配置框架 + 两个示范（音乐桥、HA 反代）**代码与测试就绪（46 项），未部署。其余服务按 `全局接入清单.md` 分批接入。本地 stdio MCP 与语音认人保持现状——前者是规范推荐，后者是设计选择。
