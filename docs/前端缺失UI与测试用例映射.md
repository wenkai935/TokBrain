# 前端缺失 UI 清单与测试用例映射

> 生成依据：对 `frontend/app` `components` `lib` 做**只读全树扫描** + 阅读 `tests/ui-contract.test.mjs` 源码。
> 本次未修改任何代码或测试；本回合另按指令删除了「外部批量导入令牌」（见附录）。

## 一、确认方式与关键事实

- 扫描方式：`grep -rlE` 全树 + 逐条阅读测试断言源码，零写入。
- **测试 `page` 变量只拼接了 `app/page.tsx + components/Library.tsx + components/Settings.tsx` 三个文件**（`tests/ui-contract.test.mjs:21-24`）。组件拆分后 `ImportWorkspace / ParseWorkspace / CookieVault` 不在其内。
- 因此测试失败有**两类根因**：
  1. **真删**：标记在全树 0 文件命中（下表 17 项）。
  2. **作用域 / 结构不匹配**：标记仍存在，但位于未被 `page` 扫描的组件，或断言要求更严格的精确组合。

## 二、确认缺失的 UI（共 17 项，按功能归组）

**① 链接粘贴导入（整段移除）**
- `import-textarea` 文本域
- 占位提示「每行粘贴一个抖音作品链接」
- 「添加抖音作品链接」按钮
- 说明「只进行预检，不会自动入库」
- `createImportBatch({ text: submittedText })` 调用（0 调用点）

**② 外部数据包导入 / ZIP（整段移除）**
- 「导入外部视频数据包」按钮
- 「选择文件夹」按钮
- `api.createPackageImportBatch` 调用（0 调用点）

**③ 本地已下载视频导入（入口移除）**
- 「导入已经下载好的视频」文案（常量 `LOCAL_VIDEO_LIMIT = 10` 仍在 `ImportWorkspace.tsx`）

**④ 解析 Cookie 入口（入口文案移除）**
- 「可选解析 Cookie」文案（`saveF2Cookie` 函数本体仍在 `CookieVault.tsx`）

**⑤ 批量导入进度 / 状态 UI（移除 + 死 CSS）**
- `batch-progress` / `worker-grid` / `import-overview` 三个 className 在组件 0 引用（globals.css 样式尚在）
- `duplicateOnly` 标志
- 「已去掉 N 条重复链接」文案
- `item.error_code && <code>` 错误码渲染

**⑥ ImportWorkspace 接线（移除）**
- `<ImportWorkspace hidden={tab !== "import"}>` 的 hidden 接线

## 三、缺失 UI → 测试用例映射表

| # | 缺失标记 | 关键断言（节选） | 对应测试用例 | 行号 |
|---|----------|------------------|--------------|------|
| 1 | `import-textarea` | `className="import-textarea"` | batch input, progress, three workers, cancellation, and confirmation are visible | 111 / 112 |
| 2 | 每行粘贴一个抖音作品链接 | `每行粘贴一个抖音作品链接` | 同上 | 111 / 113 |
| 3 | `batch-progress` | `className="batch-progress"` | 同上 | 111 / 114 |
| 4 | `worker-grid` | `className="worker-grid"` | 同上 | 111 / 116 |
| 5 | 添加抖音作品链接 | `添加抖音作品链接` | Douyin links are restored to manual preview and confirmation | 134 / 135 |
| 6 | 只进行预检，不会自动入库 | `只进行预检，不会自动入库` | 同上 | 134 / 137 |
| 7 | `createImportBatch({text})` 调用 | `createImportBatch({ text: submittedText })` | 同上 | 134 / 139 |
| 8 | 导入外部视频数据包 | `导入外部视频数据包` | folder and ZIP packages upload, recover, and enter the normal confirm flow | 147 / 148 |
| 9 | 选择文件夹 | `选择文件夹` | 同上 | 147 / 149 |
| 10 | `api.createPackageImportBatch` | `api.createPackageImportBatch` | 同上 | 147 / 153 |
| 11 | `import-overview` | `className="import-overview"` | import overview and processing queue keep all active tasks visible | 278 / 279 |
| 12 | `duplicateOnly` | `const duplicateOnly =` | duplicate links are removed automatically while unique works continue previewing | 186 / 189 |
| 13 | 已去掉 N 条重复链接 | `已去掉 \${created.duplicate_count} 条重复链接` | 同上 | 186 / 190 |
| 14 | `item.error_code && <code>` | `item.error_code && <code>` | result rows expose stable errors, upload fallback, and long-text wrapping | 200 / 201 |
| 15 | 可选解析 Cookie | `可选解析 Cookie` | settings show fixed read-only safety values and local health | 242 / 243 |
| 16 | 导入已经下载好的视频 | `导入已经下载好的视频` | local video import supports bounded two-worker uploads and refresh recovery | 293 / 294 |
| 17 | `<ImportWorkspace hidden=` | `hidden={tab !== "import"}` | local video consent and preflight survive navigation within the browser tab | 307 / 311 |

> 说明：部分用例含多个缺失标记（如用例 111 同时命中 #1/#2/#3/#4），删除其中任一即可触发该用例失败。

## 四、仍存在但测试仍红的标记（非"缺失"类）

| 标记 | 实际位置 | 为何对应测试仍红 |
|------|----------|------------------|
| `risk-notice` | `Settings.tsx` + `globals.css` | 用例 97 要求 `className="risk-notice" role="alert"` 精确组合，当前缺少 `role="alert"` |
| `TERMINAL_BATCH_STATES` | `app/page.tsx` | 用例 166 的轮询调用 `setTimeout(async…)` / `api.importBatch` 随链接批量功能删除而缺失 |
| `retainedBatches` | `app/page.tsx` + `ParseWorkspace.tsx` | 用例 173 的链接粘贴态 `submittedImportTexts` / `fullySuccessful` / `setImportText(…)` 已被删 |
| `补件` | 4 个文件 | 用例 494 / 514 因补件视图结构变化而红（标记本身存在） |

> 另有 3 个失败用例与"UI 删除"无直接关系：用例 233（断言"已移除"项仍残留）、用例 410（contractGuards 逻辑）、用例 514（补件视图结构）。如需精确归因可单独排查。

## 五、当前失败用例清单（共 15 个）

1. risk notice uses semantic danger tokens in light and dark themes
2. batch input, progress, three workers, cancellation, and confirmation are visible
3. Douyin links are restored to manual preview and confirmation
4. folder and ZIP packages upload, recover, and enter the normal confirm flow
5. batch polling is bounded and stops at terminal states
6. starting a new preview retains prior confirmable results and clears only successful input
7. duplicate links are removed automatically while unique works continue previewing
8. result rows expose stable errors, upload fallback, and long-text wrapping
9. removed collection automation and account entry points are absent
10. settings show fixed read-only safety values and local health
11. import overview and processing queue keep all active tasks visible
12. local video import supports bounded two-worker uploads and refresh recovery
13. local video consent and preflight survive navigation within the browser tab
14. critical API contract guards reject malformed local responses
15. supplement view explains material gaps and uploads complete local media

## 六、要让测试变绿需要的改动（仅说明，未执行）

- 修订 `tests/ui-contract.test.mjs` 中上述断言：删除/改写指向已移除 UI 的匹配（#1–#17），并把"作用域不匹配"的断言改为扫描正确文件或放宽精确组合。
- 属于"仍残留但应移除"（用例 233）的项需另行确认是否要补删。

---

## 附录：本回合新增删除——外部批量导入令牌

按指令，已从**前端设置页**删除「外部批量导入令牌」相关代码：

- `components/Settings.tsx`：移除 `integration-token-card` 整段 UI、`IntegrationTokenStatus` 类型导入、5 个相关 `useState`、读取令牌的 `useEffect`、以及 `createIntegrationToken` / `copyIntegrationToken` / `revokeIntegrationToken` 三个处理函数。
- `app/globals.css`：移除全部 `.integration-token-*` 样式（含 `@media` 内的两条规则）。
- `lib/api.ts`：移除 `integrationToken()` / `createIntegrationToken()` / `revokeIntegrationToken()` 三个方法及其类型导入。
- `lib/contracts.ts` 中的 `IntegrationTokenStatus` / `IntegrationTokenCreated` 类型**保留**（导出的无害类型，暂未引用，可后续清理）。

影响：

- **测试 315「settings manage one-time external import tokens」将随之变红**（其断言 `createIntegrationToken` / `revokeIntegrationToken` / `/api/settings/integration-token` 已无对应实现），这与前述"故意删 UI、测试待后续统一修订"的取向一致。
- 已全树校验 `integrationToken` 相关引用除 `contracts.ts` 定义外无残留，无悬空引用。

---

## 七、前端 UI 变更记录（2026-11-02 会话，按文档结构落地）

> 以下三块均为**前端改动**，后端未动（老张明确"后端不需要你来操作，你只改动前端"）。
> 代码改动文件：`components/Settings.tsx`、`components/ParseWorkspace.tsx`、`components/ImportWorkspace.tsx`、`app/page.tsx`、`app/v3.css`、`app/globals.css`、`lib/api.ts`。
> 验收：`tsc --noEmit` 全部通过；各改动组件 lint 0 新问题（`page.tsx` / `ImportWorkspace.tsx` 的 7 个历史 `react-hooks` error 为本次会话前既有，未动）。

### A. 设置页按文档结构重构

依据 `E:/新建文件夹 (2)/新建 文本文档 (3).txt` 的字段结构（原单卡「媒体、AI 与费用」拆成两卡 + 子标题）：

- **模型配置**卡：子标题「当前模型」（视频图文总结模型下拉 + 语音转写模型「默认百炼（xxx）」）、「密钥与凭据」（百炼 API Key 独立保存 + 账单 AccessKey 独立保存 + Cookie 粘贴字段保留）。
- **多模型配置**（ModelProfileManager 原样复用）。
- **解析配置与费用**卡：子标题「解析设置」（灵敏度 / 初选上限 / 保留画面 / 最小间隔）、「费用」（每日媒体时长 / 月度警戒线 / 每日 tokens / 本月账单 / 官方核对）。
- 按老张确认口径：① 删除快速对话模型 / 深度对话模型 / 默认回答格式 / 视频 AI 总结提示词（UI + `save()` 引用 + 三个相关函数/state 一并移除）；② 模型 API、账单查询各自独立保存按钮，状态药丸明确显示「未配置」（灰）/「已加密保存」（青）；③ 视频图文总结模型保留可下拉切换（原行为）；④ 每日 tokens 按万进制渲染。
- ⚠️ tokens 默认值冲突：后端 `daily_llm_token_limit` 默认 `500_000`（50 万），非文档所写 `5,0000`。按真实值渲染为 `50,0000（万进制）`，未擅改默认值。

### B. 解析页改造（全选 + 跨页选择 + 批量按钮）

- **全选复选框**（列表顶部）：三态（空 / 部分 / 全选），作用范围 = 仅当前页。
- **顶部统计卡 → 跨页选择器**：点「重复 / 中断 / 失败 / 需补件 / 解析中 / 可确认」任意一张 → 跨页全选该状态所有项；再次点击同一张 → 取消并复位颜色（颜色变化交互）。「重复」特殊：全选后保留一条未选。激活态按状态语义着色（重复紫、中断琥珀、失败玫红、需补件蓝、解析中青、可确认绿）。
- **右下角批量按钮**（玫红 / 蓝，与橙色确认按钮区分）：「删除所选项」「重试所选项」，按 selection 里"可删 / 可重试"子集实时计数并禁用。
- 已移除旧「重试失败导入」链接。
- 后端依赖：`api.ts` 新增 `deleteImportItems()`（用现有单条 `removeImportItem` 循环兜底，现已可用）、`retryImportItems()` 调用 `POST /api/import-items/retry`（**该端点需老张后端补**，未加前点"重试所选项"会报错，属预期）。

### C. 导入进度板块改造（默认展开 / 真删除 / 恢复按钮）

文件：`components/ImportWorkspace.tsx` 的 `ImportProgress` 组件、`lib/api.ts`。

| 确认项 | 落地做法 |
|---|---|
| ① 默认展开 | 删除 `collapsed` 状态与 `setCollapsed`，header 去掉 `onClick` 折叠与动态箭头（保留静态 ▾），板块常驻展开。 |
| ② 「清除已完成缓存」走真删除 | `clearCompleted()` 改为 `await perform("clear-completed-jobs", …)` → 调 `api.clearCompletedJobs()`（`DELETE /api/jobs/completed`）→ `onRefresh()` 刷新。不再塞 `dismissed` 隐藏集。 |
| ③ 非成功条目保留 | `completedCount` 改为只数 `succeeded` + `partial`（新增 `SUCCESS_JOB_STATES` 常量）；failed / cancelled / await_fix 继续显示，不被清掉。× 按钮改为只对非成功终态项出现。 |
| ④ 恢复非成功项目按钮 | 逐条 × 隐藏非成功项（仍走 `dismissed` 前端隐藏）后，右下角出现「恢复非成功项目（N）」按钮（flex 右对齐），点击 `setDismissed(new Set())` 全部恢复。 |

- 后端依赖：`api.ts` 新增 `clearCompletedJobs()` → `DELETE /api/jobs/completed`。**该端点目前后端 `app/routers_v2/jobs.py` 尚不存在**（仅有 GET 列表 / POST retry / GET 单条 / POST cancel）。点"清除已完成缓存"在后端补好前会 404，属预期——前端调用已正确接好。
- `lib/contracts.ts` 无需改动（`Job` 类型已含 `state` 字段）。

### D. 累计测试影响（供后续统一修订参考）

- 删除外部批量令牌 → 测试 315 变红（断言方法已无实现）。
- 设置页删 4 字段 + 拆卡 → 相关断言（如用例 242「可选解析 Cookie」仍存但结构变）需复核。
- 解析页 / 导入进度板块的结构性变化 → 原有 `ui-contract` 相关断言需同步修订（详见第二节 #1–#17 缺失清单，多数属"老张有意删 UI、测试待统一修订"范畴）。
- 上述测试改动**本次均未执行**，仅记录于此，待老张统一决策。
