import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

import { removeConfirmedImportItems } from "../lib/importBatches.ts";
import {
  IMPORT_BATCH_STORAGE_KEY,
  PACKAGE_IMPORT_STORAGE_KEY,
  rememberPackageBatch,
  storedPackageBatches,
} from "../lib/importBatchStorage.ts";
import {
  parseImportBatch,
  parseJobs,
  parseProbe,
  parseRuntimeSettings,
} from "../lib/contractGuards.ts";
import { didAnyJobReachTerminal, operationIncludesJob } from "../lib/jobPolling.ts";
import { mergeWorksPage } from "../lib/libraryPagination.ts";

const pageShell = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
const importWorkspaceComponent = await readFile(new URL("../components/ImportWorkspace.tsx", import.meta.url), "utf8");
const parseWorkspaceComponent = await readFile(new URL("../components/ParseWorkspace.tsx", import.meta.url), "utf8");
const libraryComponent = await readFile(new URL("../components/Library.tsx", import.meta.url), "utf8");
const settingsComponent = await readFile(new URL("../components/Settings.tsx", import.meta.url), "utf8");
const page = [pageShell, importWorkspaceComponent, libraryComponent, settingsComponent].join("\n");
const api = await readFile(new URL("../lib/api.ts", import.meta.url), "utf8");
const contracts = await readFile(new URL("../lib/contracts.ts", import.meta.url), "utf8");
const libraryReturn = await readFile(new URL("../lib/libraryReturn.ts", import.meta.url), "utf8");
const batchStorage = await readFile(new URL("../lib/importBatchStorage.ts", import.meta.url), "utf8");
const assets = await readFile(new URL("../lib/assets.ts", import.meta.url), "utf8");
const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
const themeRegistry = await readFile(new URL("../themes/registry.ts", import.meta.url), "utf8");
const themeProvider = await readFile(new URL("../themes/ThemeProvider.tsx", import.meta.url), "utf8");
const themeBaseStyles = await readFile(new URL("../themes/styles/base.css", import.meta.url), "utf8");
const packageManifest = JSON.parse(
  await readFile(new URL("../package.json", import.meta.url), "utf8"),
);

const expectedThemes = [
  "sunset-cloudsea",
  "eastern-mist",
  "ocean-dawn",
];

test("website branding is TokBrain and import is the primary workspace", async () => {
  const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  assert.match(layout, /title: "TokBrain"/);
  assert.match(page, /type Tab = "import" \| "parse" \| "library" \| "settings"/);
  assert.match(page, /<strong>TokBrain<\/strong>/);
  assert.match(page, /tab === "import"/);
});

test("page delegates library and settings UI to dependency-safe components", () => {
  assert.match(pageShell, /import \{ Library \} from "\.\.\/components\/Library"/);
  assert.match(pageShell, /import \{ Settings, ThemePicker \} from "\.\.\/components\/Settings"/);
  assert.doesNotMatch(pageShell, /function Library\(/);
  assert.doesNotMatch(pageShell, /function Settings\(/);
  assert.doesNotMatch(pageShell, /function ThemePicker\(/);
  assert.match(libraryComponent, /export function Library\(/);
  assert.match(settingsComponent, /export function Settings\(/);
  assert.match(settingsComponent, /export function ThemePicker\(/);
  assert.doesNotMatch(libraryComponent, /from "\.\.\/app\/page"/);
  assert.doesNotMatch(settingsComponent, /from "\.\.\/app\/page"/);
});

test("frontend development and production servers bind only to localhost", () => {
  assert.match(packageManifest.scripts.dev, /--hostname 127\.0\.0\.1/);
  assert.match(packageManifest.scripts.start, /--hostname 127\.0\.0\.1/);
  assert.equal(
    packageManifest.scripts.typecheck,
    "tsc --noEmit --incremental false --noUnusedLocals --noUnusedParameters",
  );
});

test("theme registry exposes three unique locally persisted themes", () => {
  const ids = [...themeRegistry.matchAll(/^\s+id: "([^"]+)",$/gm)].map((match) => match[1]);
  assert.deepEqual(ids, expectedThemes);
  assert.equal(new Set(ids).size, 3);
  assert.match(themeRegistry, /DEFAULT_THEME_ID: ThemeId = "sunset-cloudsea"/);
  assert.match(themeProvider, /window\.localStorage\.setItem\(THEME_STORAGE_KEY, next\)/);
  assert.match(themeProvider, /document\.documentElement\.dataset\.theme = definition\.id/);
});

test("each theme has isolated CSS and a local background asset", async () => {
  for (const id of expectedThemes) {
    const css = await readFile(new URL(`../themes/styles/${id}.css`, import.meta.url), "utf8");
    assert.match(css, new RegExp(`:root\\[data-theme="${id}"\\]`));
    if (id !== "classic-night") await access(new URL(`../public/themes/${id}.webp`, import.meta.url));
  }
});

test("layout imports all theme styles directly for Windows non-ASCII paths", async () => {
  const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  assert.match(layout, /themes\/styles\/base\.css/);
  for (const id of expectedThemes) assert.match(layout, new RegExp(`themes/styles/${id}\\.css`));
});

test("risk notice uses semantic danger tokens in light and dark themes", () => {
  assert.match(page, /className="risk-notice" role="alert"/);
  for (const token of ["--danger-text", "--danger-border", "--danger-surface"]) {
    assert.match(styles, new RegExp(token));
    assert.match(themeBaseStyles, new RegExp(token));
  }
  assert.match(themeBaseStyles, /html\[data-theme-tone="dark"\]/);
  assert.match(styles, /\.risk-notice\s*\{[^}]*var\(--danger-border\)[^}]*var\(--danger-surface\)/s);
  assert.match(styles, /\.app-shell>main\s*\{\s*min-width:0;\s*width:100%;/);
  assert.match(styles, /\.risk-notice\s*\{[^}]*overflow-wrap:anywhere/s);
});

test("network import collects previews and syncs only selected works", () => {
  assert.match(importWorkspaceComponent, /key: "douyin", label: "抖音"/);
  assert.match(importWorkspaceComponent, /api\.domCollect\(scope\)/);
  assert.match(importWorkspaceComponent, /api\.domCollectStatus\(taskId\)/);
  assert.match(importWorkspaceComponent, /api\.collectedVideos\(scope\)/);
  assert.match(importWorkspaceComponent, /api\.syncSelected\(scope, ids\)/);
  assert.match(importWorkspaceComponent, /勾选视频后点「同步当前 N 条」才入库/);
});

test("parse workspace separates queue states and protects resolving items", () => {
  for (const label of ["等待中", "排队中", "解析中", "可确认"]) {
    assert.match(parseWorkspaceComponent, new RegExp(`label: "${label}"`));
  }
  assert.match(parseWorkspaceComponent, /NON_SELECTABLE_STATES = new Set\(\["resolving"\]\)/);
  assert.match(parseWorkspaceComponent, /disabled=\{count === 0 \|\| !group\.selectable\}/);
  assert.match(parseWorkspaceComponent, /删除所选项（\{deletableSelected\.length\}）/);
  assert.match(parseWorkspaceComponent, /重试所选项（\{retryableSelected\.length\}）/);
});

test("import progress clears successful jobs and can restore hidden failures", () => {
  assert.match(importWorkspaceComponent, /SUCCESS_JOB_STATES = new Set\(\["succeeded", "partial"\]\)/);
  assert.match(importWorkspaceComponent, /api\.clearCompletedJobs\(\)/);
  assert.match(importWorkspaceComponent, /清除已完成（\{completedCount\}）/);
  assert.match(importWorkspaceComponent, /恢复非成功项目（\{dismissed\.size\}）/);
});

test("small screens collapse worker, result, and safety layouts", () => {
  assert.match(styles, /@media\(max-width:760px\)[\s\S]*\.batch-stats,.worker-grid,.safety-grid/);
  assert.match(styles, /@media\(max-width:460px\)[\s\S]*\.import-result/);
});

test("API client exposes import, local assets, and unified jobs", () => {
  assert.match(api, /createImportBatch/);
  assert.match(api, /\/api\/import-batches/);
  assert.match(api, /cancelImportBatch/);
  assert.match(api, /confirmImportBatch/);
  assert.match(api, /\/api\/import-items\/\$\{itemId\}\/assets/);
  assert.match(
    api,
    /jobs\(\): Promise<Contract\.Job\[\]> \{[\s\S]*?this\.request<unknown>\("\/api\/jobs"\)/,
  );
  assert.match(api, /cancelJob/);
  assert.match(api, /createCollection/);
  assert.match(api, /addWorksToCollection/);
});

test("removed collection automation and account entry points are absent", () => {
  assert.doesNotMatch(importWorkspaceComponent, /refreshCollections/);
  assert.doesNotMatch(page, /继续初始化/);
  assert.doesNotMatch(page, /完整核对/);
  assert.doesNotMatch(api, /\/api\/auth\//);
  assert.doesNotMatch(api, /adapter-health/);
});

test("settings show current models, encrypted credentials, and local health", () => {
  assert.match(page, /百炼模型 API Key/);
  assert.match(page, /账单 AccessKey ID/);
  assert.match(page, /settings\.processing_model_options\.map/);
  assert.match(page, /name="asr_model_readonly"/);
  assert.doesNotMatch(page, /settings\.import_worker_count/);
  assert.doesNotMatch(page, /settings\.import_network_concurrency/);
  assert.doesNotMatch(page, /settings\.import_cooldown_min_seconds/);
  assert.match(page, /className="health-progress"/);
  assert.match(page, /api\.healthProbe/);
  assert.match(page, /敏感残留尚未清理/);
  assert.doesNotMatch(page, /name="import_worker_count"/);
  assert.doesNotMatch(page, /name="import_daily_limit"/);
});

test("settings expose processing, usage, and billing controls", () => {
  assert.match(page, /视频图文总结模型/);
  assert.match(page, /settings\.processing_model_options\.map/);
  assert.match(page, /刷新官方账单/);
  assert.match(page, /账单查询 AccessKey ID/);
  assert.match(page, /saveBillingCredentials/);
  assert.match(page, /账单查询凭据已在本机后台加密保存/);
  assert.match(page, /本月账单/);
  assert.match(page, /usage\?\.official_billed_cny/);
  assert.match(page, /clearAllKeys/);
  assert.match(page, /删除全部 API Key 与 AccessKey/);
  assert.match(api, /\/api\/settings\/secrets/);
});

test("local video import keeps upload limits, recovery, and rights attestation", () => {
  assert.match(page, /const LOCAL_VIDEO_LIMIT = 10/);
  assert.match(page, /multiple[\s\S]{0,120}accept="\.mp4,\.mov,\.mkv,\.webm/);
  assert.match(page, /rights_attested: true/);
  assert.match(page, /rememberLocalImportBatch\(created\.id\)/);
  assert.match(batchStorage, /LOCAL_IMPORT_BATCH_STORAGE_KEY/);
  assert.match(page, /已恢复刷新前的导入批次/);
  assert.match(libraryComponent, /value: "local_upload"/);
  assert.match(api, /\/api\/local-import-batches/);
  assert.match(api, /\/api\/local-import-batches\/\$\{encodeURIComponent\(batchId\)\}\/items\/\$\{itemId\}\/video/);
});

test("local video consent and preflight survive navigation within the browser tab", () => {
  assert.match(batchStorage, /LOCAL_IMPORT_RIGHTS_SESSION_KEY/);
  assert.match(batchStorage, /window\.sessionStorage\.getItem\(LOCAL_IMPORT_RIGHTS_SESSION_KEY\)/);
  assert.match(batchStorage, /window\.sessionStorage\.setItem\(LOCAL_IMPORT_RIGHTS_SESSION_KEY, "true"\)/);
  assert.match(page, /<ImportWorkspace\s+hidden=\{tab !== "import"\}/);
  assert.match(page, /<section className="import-workspace stack" hidden=\{hidden\}>/);
});

test("removed external import token controls stay absent", () => {
  assert.doesNotMatch(page, /外部导入令牌/);
  assert.doesNotMatch(page, /createIntegrationToken/);
  assert.doesNotMatch(page, /revokeIntegrationToken/);
  assert.doesNotMatch(api, /\/api\/settings\/integration-token/);
});

test("pending and in-library works can share local collections", () => {
  assert.match(page, /新建收藏夹/);
  assert.match(page, /选择目标收藏夹/);
  assert.match(page, /加入收藏夹/);
  assert.match(page, /state === "pending" \|\| state === "in_library"/);
  assert.match(page, /收藏夹关系会在作品完成 AI 处理后继续保留/);
  assert.match(page, /批量开始入库/);
  assert.match(page, /设置总结提示词/);
  assert.match(page, /收藏夹专属 AI 规则/);
  assert.match(page, /activeCollection\.summary_prompt \|\| globalSummaryPrompt/);
  assert.match(page, /已作为可编辑正文载入/);
  assert.doesNotMatch(page, /placeholder=\{globalSummaryPrompt/);
  assert.match(api, /updateCollectionSummaryPrompt/);
  assert.match(api, /\/api\/library\/ingest\/jobs/);
});

test("sidebar ambient scenes exist for every theme", () => {
  assert.match(page, /className="theme-ambient"/);
  for (const theme of ["sunset-cloudsea", "eastern-mist", "ocean-dawn"]) {
    assert.match(themeBaseStyles, new RegExp(`data-theme="${theme}"[^}]*--ambient-symbol`));
  }
  assert.match(themeBaseStyles, /sunset-cloudsea[^}]*--ambient-symbol:"☁"/s);
  assert.match(themeBaseStyles, /sunset-cloudsea[^}]*theme-ambient i[^}]*#5cb8ff/s);
  assert.match(themeBaseStyles, /eastern-mist[^}]*--ambient-symbol:"山"/s);
  assert.match(themeBaseStyles, /ocean-dawn[^}]*--ambient-symbol:"≋"/s);
  assert.doesNotMatch(themeBaseStyles, /data-theme="classic-night"/);
});

test("theme picker and background preferences remain local", () => {
  assert.match(page, /role="radiogroup" aria-label="界面主题"/);
  assert.match(page, /className={`theme-card/);
  assert.match(page, /className="theme-preview"/);
  assert.match(page, /className="background-intensity"/);
  assert.match(page, /aria-label="背景显现度"/);
  assert.match(themeProvider, /BACKGROUND_INTENSITY_STORAGE_KEY/);
  assert.match(themeProvider, /setBackgroundIntensity/);
});

test("library pagination de-duplicates overlapping load-more responses", () => {
  const current = { items: [{ id: 1 }, { id: 2 }], total: 4, selected_count: 0, account_selected_count: 0, next_offset: 2 };
  const incoming = { items: [{ id: 2 }, { id: 3 }], total: 4, selected_count: 0, account_selected_count: 0, next_offset: 3 };
  assert.deepEqual(mergeWorksPage(current, incoming, true).items.map((work) => work.id), [1, 2, 3]);
  assert.deepEqual(mergeWorksPage(current, incoming, false).items.map((work) => work.id), [2, 3]);
});

test("partial batch confirmation removes only server-confirmed preview items", () => {
  const batch = { id: "batch-a", items: [{ id: 11 }, { id: 12 }, { id: 13 }] };
  const reconciled = removeConfirmedImportItems(batch, new Set([11, 13]));
  assert.deepEqual(reconciled.items.map((item) => item.id), [12]);
  assert.deepEqual(batch.items.map((item) => item.id), [11, 12, 13]);
});

test("processing job polling invalidates dependent data only on a terminal transition", () => {
  const job = (id, state, jobType = "ingest") => ({ id, state, job_type: jobType });
  assert.equal(didAnyJobReachTerminal([job("a", "running")], [job("a", "running")]), false);
  assert.equal(didAnyJobReachTerminal([job("a", "running")], [job("a", "succeeded")]), true);
  assert.equal(didAnyJobReachTerminal([job("preview", "running", "link_preview")], []), false);
  assert.equal(operationIncludesJob({ job: job("nested", "queued") }), true);
  assert.match(page, /const next = await api\.jobs\(\)/);
  assert.match(page, /if \(reachedTerminal\) \{[\s\S]*?loadUsage\(\)[\s\S]*?loadCollections\(\)/);
  assert.doesNotMatch(page, /loadCommon/);
});

test("batch storage keeps generic and package recovery indexes together", () => {
  const createStorage = () => {
    const values = new Map();
    return {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, String(value)),
      removeItem: (key) => values.delete(key),
    };
  };
  const previousWindow = globalThis.window;
  const localStorage = createStorage();
  globalThis.window = { localStorage, sessionStorage: createStorage() };
  try {
    rememberPackageBatch("package-1");
    assert.deepEqual(JSON.parse(localStorage.getItem(IMPORT_BATCH_STORAGE_KEY)), ["package-1"]);
    assert.deepEqual(JSON.parse(localStorage.getItem(PACKAGE_IMPORT_STORAGE_KEY)), ["package-1"]);
    localStorage.setItem(PACKAGE_IMPORT_STORAGE_KEY, "not-json");
    assert.deepEqual(storedPackageBatches(), []);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("critical API contract guards reject malformed local responses", () => {
  assert.deepEqual(parseJobs([{ id: "j1", job_type: "ingest", state: "running", message: "处理中", progress: {} }])[0].id, "j1");
  assert.throws(() => parseJobs([{ id: 1, job_type: "ingest", state: "running", message: "处理中", progress: {} }]), /任务列表格式无效/);
  const runtimeSettings = {
    daily_media_minutes_limit: 60,
    daily_llm_token_limit: 100_000,
    monthly_warning_cny: 20,
    scene_threshold: 0.35,
    max_scene_candidates: 120,
    max_keyframes: 12,
    min_keyframe_gap_seconds: 2,
    dpapi_warning: "本地估算",
    has_dashscope_key: true,
    has_bss_credentials: false,
    has_f2_cookie: false,
    security_cleanup_required: false,
    security_cleanup_message: "",
    summary_prompt: "总结",
    default_summary_prompt: "默认",
    processing_model: "processing",
    processing_model_options: [],
    chat_fast_model: "fast",
    chat_deep_model: "deep",
    chat_model_options: [],
    default_answer_format: "markdown",
    ocr_model: "ocr",
    asr_model: "asr",
    embedding_model: "embedding",
    import_batch_limit: 10,
    import_daily_limit: 150,
    import_worker_count: 3,
    import_network_concurrency: 1,
    import_cooldown_min_seconds: 4,
    import_cooldown_max_seconds: 8,
  };
  assert.equal(parseRuntimeSettings(runtimeSettings).summary_prompt, "总结");
  assert.throws(
    () => parseRuntimeSettings({ ...runtimeSettings, daily_media_minutes_limit: undefined }),
    /设置格式无效/,
  );
  assert.throws(
    () => parseRuntimeSettings({ ...runtimeSettings, import_daily_limit: Number.NaN }),
    /设置格式无效/,
  );
  assert.throws(
    () => parseRuntimeSettings({ ...runtimeSettings, has_f2_cookie: "false" }),
    /设置格式无效/,
  );
  assert.throws(() => parseImportBatch({ id: "b1", state: "running", items: [] }), /导入批次格式无效/);
  assert.equal(parseProbe({
    probe: "coordinators",
    status: "healthy",
    message: "正常",
    details: { coordinators: [{ name: "processing", alive: true, workers_alive: 1, workers_expected: 1, last_error: null }] },
  }).details.coordinators[0].alive, true);
});

test("health checks include coordinator liveness details", () => {
  assert.match(page, /HEALTH_PROBE_NAMES = \["database", "media_runtime", "coordinators", "security_cleanup"\]/);
  assert.match(contracts, /coordinators\?: Array/);
  assert.match(page, /后台协调器/);
  assert.match(page, /workers_alive/);
});

test("import helper copy omits internal worker and cooldown details", () => {
  assert.doesNotMatch(page, /3 个 worker · 平台网络严格串行/);
  assert.doesNotMatch(page, /每条链接完成后随机冷却 4–8 秒/);
});

test("toast colors use semantic tokens across light and dark themes", () => {
  for (const token of ["--toast-bg", "--toast-text", "--toast-success-border", "--toast-error-border"]) {
    assert.match(styles, new RegExp(token));
    assert.match(themeBaseStyles, new RegExp(token));
  }
  assert.match(styles, /\.toast\s*\{[^}]*background:var\(--toast-bg\)[^}]*color:var\(--toast-text\)/s);
  assert.match(themeBaseStyles, /html\[data-theme-tone="dark"\][^{]*\{[^}]*--toast-bg:/s);
});

test("LaTeX delimiters normalize without changing code fences", async () => {
  const { normalizeMathMarkdown, markdownToPlainText } = await import("../lib/markdown.ts");
  const source = "公式：\\\\[ \\int_a^b f(x) \\mathrm{d}x \\\\]，其中 \\\\(a=0\\\\)。";
  const normalized = normalizeMathMarkdown(source);
  assert.match(normalized, /\$\$\n\\int_a\^b f\(x\) \\mathrm\{d\}x\n\$\$/);
  assert.match(normalized, /\$a=0\$/);
  assert.equal(normalizeMathMarkdown("```latex\n\\\\[x\\\\]\n```"), "```latex\n\\\\[x\\\\]\n```");
  assert.match(markdownToPlainText(source), /\\int_a\^b/);
});

test("library keeps primary states plus supplement view, summaries, lazy covers, and Obsidian export", async () => {
  const obsidian = await readFile(new URL("../lib/obsidian.ts", import.meta.url), "utf8");
  for (const state of ["pending", "in_library", "supplement", "issues", "archived"]) {
    assert.match(page, new RegExp(`state === "${state}"`));
  }
  assert.match(page, /补齐\/更新总结/);
  assert.match(page, /导出到 Obsidian/);
  assert.match(page, /设置图片统一存放位置/);
  assert.match(page, /loading="lazy" decoding="async"/);
  assert.match(assets, /value\?\.startsWith\("\/api\/"\) \? `\$\{API_BASE\}\$\{value\}` : null/);
  assert.match(page, /className="technical-details" aria-label="技术详情"/);
  assert.match(page, /确认永久删除这个作品、总结、索引与本地资产/);
  assert.match(page, /\["required", "failed"\]\.includes\(work\.supplement_state \|\| ""\)/);
  assert.match(page, /className="work-supplement-indicator">需要补件/);
  assert.match(styles, /\.work-supplement-indicator/);
  assert.match(obsidian, /showDirectoryPicker/);
  assert.match(obsidian, /text\/markdown;charset=utf-8/);
  assert.match(obsidian, /chooseObsidianImageDirectory/);
});

test("supplement view explains material gaps and uploads complete local media", () => {
  assert.match(page, /待补件 <span className="n">\{summary\.supplement_count \?\? 0\}<\/span>/);
  assert.match(page, /<strong>材料缺口：<\/strong>\{supplementReasonLabel\(work\.supplement_reason\)\}/);
  assert.match(page, /work\.evidence_state === "sufficient" \? "现有材料可检索"/);
  assert.match(page, /full_video_unavailable: "未取得完整视频文件；已有总结可能来自字幕、音频或部分画面"/);
  assert.match(page, /HIDDEN_TRACK_REPORT_KEYS = new Set\(\["migration", "kind"\]\)/);
  assert.match(page, /required: evidenceState === "sufficient" \? "建议补充" : "需要补充"/);
  assert.match(page, /typeof report\.available === "boolean"/);
  assert.match(page, /report\.available \? "可用" : "未取得"/);
  assert.match(page, /label: "音频\/字幕"/);
  assert.match(page, /return `音频：\$\{audio\} · 字幕：\$\{subtitle\}`/);
  assert.match(page, /trackReportRows\(work\.track_report, work\.kind\)/);
  assert.doesNotMatch(page, /证据/);
  assert.match(page, /我确认有权处理并上传该本地素材/);
  assert.match(page, /work\.kind === "image"\s*\? "上传完整图片组"\s*:\s*"上传完整视频"/s);
  assert.match(page, /api\.uploadWorkSupplement\(work\.id, selectedFiles\)/);
  assert.match(page, /我确认有权处理并上传这些本地素材/);
  assert.match(api, /state: "pending" \| "in_library" \| "supplement" \| "issues" \| "archived"/);
  assert.match(api, /body\.append\("rights_attested", "true"\)/);
  assert.match(api, /body\.append\("files", file, file\.name\)/);
  assert.match(api, /\/api\/library\/works\/\$\{id\}\/supplement/);
  assert.match(contracts, /supplement_state\?: SupplementState/);
  assert.match(contracts, /evidence_state\?: EvidenceState/);
  assert.match(contracts, /track_report\?: TrackReport \| null/);
  assert.match(styles, /\.supplement-details/);
  assert.match(styles, /\.evidence-state\.searchable/);
});

test("success notices auto-dismiss while errors stay until closed", () => {
  assert.match(page, /window\.setTimeout\(\(\) => setNotice\(""\), 5000\)/);
  assert.doesNotMatch(page, /setTimeout\(\(\) => setError\(""\)/);
});

test("cancelling a native folder picker is not reported as an application error", () => {
  assert.match(page, /import \{ isUserCancelled, reason \} from "\.\.\/lib\/errors"/);
  assert.match(page, /if \(isUserCancelled\(value\)\) return false/);
  assert.match(page, /if \(isUserCancelled\(value\)\) return;/);
});

test("native folder picker abort errors are detected by name and browser message", async () => {
  const { isUserCancelled } = await import("../lib/errors.ts");
  const namedAbort = new Error("用户取消了目录选择");
  namedAbort.name = "AbortError";
  assert.equal(isUserCancelled(namedAbort), true);
  assert.equal(
    isUserCancelled(
      new Error(
        "Failed to execute 'showDirectoryPicker' on 'Window': The user aborted a request.",
      ),
    ),
    true,
  );
  assert.equal(isUserCancelled(new Error("目录没有写入权限")), false);
});

test("production page and summary route compile around current navigation", async () => {
  const summaryPage = await readFile(new URL("../app/works/[id]/page.tsx", import.meta.url), "utf8");
  assert.match(summaryPage, /\?tab=library&state=in_library&view=works/);
  assert.match(summaryPage, /window\.history\.back\(\)/);
  assert.match(summaryPage, /readLibraryReturnContext\(\)/);
  assert.match(page, /rememberLibraryReturnContext\(\{/);
  assert.match(page, /window\.scrollTo\(\{ top: context\.scrollY, behavior: "auto" \}\)/);
  assert.match(libraryReturn, /tokbrain\.library-return\.v1/);
  assert.match(summaryPage, /summary-content/);
  assert.match(summaryPage, /永久删除/);
  assert.match(summaryPage, /api\.remove\(workId\)/);
});
