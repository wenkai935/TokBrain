import type * as Contract from "./contracts";
import {
  parseCollections,
  parseHealth,
  parseImportBatch,
  parseJobs,
  parseModelProfiles,
  parseProbe,
  parseRuntimeSettings,
  parseWorksPage,
  parseWorkSummaryDetail,
} from "./contractGuards";

export interface SyncOverview {
  feishu: {
    connected: boolean;
    query_ok: boolean;
    last_query: string;
    rows: number;
    query_detail?: string;
    cli_detail?: string;
    user_name?: string;
    expires_at?: string;
    refresh_expires_at?: string;
  };
  obsidian: {
    connected: boolean;
    path: string;
    detail?: string;
  };
  poll: {
    frontend_refresh_sec: number;
  };
}

/** 飞书授权一键续期的流程状态（对应 GET/POST /api/sync/feishu/relogin）。 */
export interface FeishuRelogin {
  state: "idle" | "starting" | "waiting" | "success" | "failed";
  url: string;
  user_code: string;
  qrcode_b64: string;
  message: string;
  started_at: string;
  finished_at: string;
}

/** 飞书发现批次里的一条：state=ready 可解析，unresolved 是本轮没取到信息的。 */
export interface DiscoveryItem {
  record_id: string;
  batch_id: string;
  item_id: number | null;
  link: string;
  lid: string;
  state: "ready" | "unresolved";
  title: string;
  author_name: string;
  platform: string;
  kind: string;
  cover_url: string;
  duration_seconds: number;
  platform_work_id: string;
  reason: string;
}

/**
 * 飞书发现批次。
 *
 * state=filling 表示 bridge 还在补齐这一轮，此时 items 必为空、计数全 0——
 * 前端在 filling 期间不显示任何东西，避免用户看到数字跳动。
 */
export interface DiscoveryView {
  state: "idle" | "filling" | "ready";
  filling: number;
  total: number;
  resolvable: number;
  unresolved: number;
  items: DiscoveryItem[];
  truncated: number;
}

export interface DiscoveryConfirmResult {
  confirmed: number;
  work_ids: number[];
  job_id: string;
  remaining: number;
}

export type {
  Collection,
  Health,
  ImportBatch,
  ImportBatchCreated,
  ImportBatchCreateRequest,
  ImportItem,
  ImportItemUpdate,
  ImportItemUpdateResult,
  Job,
  LibrarySummary,
  LocalImportBatchRequest,
  LocalVideoUploadResult,
  ModelProfile,
  ModelProfilePayload,
  ModelProfilesView,
  ModelProviderType,
  PackageImportBatchRequest,
  ObsidianManifest,
  PlatformId,
  PlatformStatus,
  PlatformPullResult,
  Probe,
  RuntimeSettings,
  RuntimeSettingsUpdate,
  Usage,
  Work,
  WorkSupplementResult,
  WorksPage,
  WorkSummaryDetail,
} from "./contracts";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "";

const LOCAL_REQUEST_HEADER = "tokbrain-local";
const GET_RETRY_DELAY_MS = 300;
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const UPLOAD_REQUEST_TIMEOUT_MS = 30 * 60_000;
const PULL_REQUEST_TIMEOUT_MS = 120_000;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function messageFromPayload(payload: unknown, fallback: string): string {
  if (payload === null || typeof payload !== "object") return fallback;
  const body = payload as {
    detail?: unknown;
    message?: unknown;
  };
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) {
    const first = body.detail.find((item) => item && typeof item === "object") as
      | { msg?: unknown; message?: unknown; loc?: unknown }
      | undefined;
    const message = typeof first?.message === "string"
      ? first.message
      : typeof first?.msg === "string"
        ? first.msg
        : "";
    if (message) {
      const location = Array.isArray(first?.loc)
        ? first.loc.filter((item) => !["body", "query", "path", "header"].includes(String(item))).join(".")
        : "";
      return location ? `${location}：${message}` : message;
    }
  }
  if (body.detail && typeof body.detail === "object") {
    const detail = body.detail as { message?: unknown; code?: unknown };
    if (typeof detail.message === "string") return detail.message;
    if (typeof detail.code === "string") return detail.code;
  }
  return typeof body.message === "string" ? body.message : fallback;
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function requestOptions(init: RequestInit): RequestInit {
  const headers = new Headers(init.headers);
  headers.set("X-Requested-With", LOCAL_REQUEST_HEADER);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  return { ...init, cache: "no-store", headers };
}

async function parsedBody(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}

/** 统一 Cookie 库：一个平台的状态。只有名字/条数，永远不含 Cookie 值。 */
export interface CookiePlatformStatus {
  platform: string;
  label: string;
  filename: string;
  path: string;
  note: string;
  exists: boolean;
  cookie_count: number;
  auth_ready: boolean;
  missing_auth: string[];
  names: string[];
  expired_count: number;
  size_bytes: number;
  updated_at: string | null;
}

/** 一次全量上传的返回：命中哪些平台，哪些没识别到。 */
export interface CookieVaultState {
  directory: string;
  platforms: CookiePlatformStatus[];
  skipped?: string[];
}

class LocalApiClient {
  constructor(private readonly baseUrl: string) {}

  private endpoint(path: string): string {
    return `${this.baseUrl}${path}`;
  }

  private async fetchOnce(
    path: string,
    options: RequestInit,
    timeoutMs: number,
  ): Promise<Response> {
    if (!timeoutMs) return fetch(this.endpoint(path), options);
    const controller = new AbortController();
    const forwardAbort = () => controller.abort(options.signal?.reason);
    if (options.signal?.aborted) forwardAbort();
    else options.signal?.addEventListener("abort", forwardAbort, { once: true });
    const timer = window.setTimeout(
      () => controller.abort(new DOMException("请求超时", "TimeoutError")),
      timeoutMs,
    );
    try {
      return await fetch(this.endpoint(path), { ...options, signal: controller.signal });
    } finally {
      window.clearTimeout(timer);
      options.signal?.removeEventListener("abort", forwardAbort);
    }
  }

  private async fetchWithSafeRetry(
    path: string,
    init: RequestInit,
    timeoutMs: number,
  ): Promise<Response> {
    const options = requestOptions(init);
    const method = (options.method || "GET").toUpperCase();
    try {
      return await this.fetchOnce(path, options, timeoutMs);
    } catch (cause) {
      if (options.signal?.aborted) throw cause;
      if (method !== "GET") {
        throw new ApiError(
          0,
          cause instanceof DOMException && cause.name === "TimeoutError"
            ? "请求本机后端超时，请刷新确认结果后再重试，避免重复提交"
            : "本机后端未响应，请刷新确认后再重试，避免重复提交",
        );
      }
    }

    await wait(GET_RETRY_DELAY_MS);
    try {
      return await this.fetchOnce(path, options, timeoutMs);
    } catch (cause) {
      if (options.signal?.aborted) throw cause;
      throw new ApiError(
        0,
        cause instanceof DOMException && cause.name === "TimeoutError"
          ? "请求本机后端超时，请确认服务仍在运行"
          : "无法连接本机后端，请确认服务仍在运行",
      );
    }
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  ): Promise<T> {
    const response = await this.fetchWithSafeRetry(path, init, timeoutMs);
    const payload = await parsedBody(response);
    if (!response.ok) {
      throw new ApiError(
        response.status,
        messageFromPayload(payload, `请求失败 (${response.status})`),
      );
    }
    return payload as T;
  }

  health(): Promise<Contract.Health> {
    return this.request<unknown>("/api/system-health").then(parseHealth);
  }

  /** 飞书/obsidian 连接与最近查询状态：每次调用后端都会真实查一次飞书表。 */
  syncOverview(): Promise<SyncOverview> {
    return this.request<unknown>("/api/sync/overview").then((body) => body as SyncOverview);
  }

  /**
   * 飞书发现批次：bridge 补齐期间返回 state=filling（清单为空），
   * 补齐结束才一次性给出完整清单，保证前端显示的数字不再变化。
   */
  discovery(): Promise<DiscoveryView> {
    return this.request<unknown>("/api/sync/discovery").then((body) => body as DiscoveryView);
  }

  /** 用户点了「解析」：只提交选中的条目，未选中的留在待确认里。 */
  confirmDiscovery(itemIds: number[]): Promise<DiscoveryConfirmResult> {
    return this.request<DiscoveryConfirmResult>("/api/sync/discovery/confirm", {
      method: "POST",
      body: JSON.stringify({ item_ids: itemIds }),
    });
  }

  /**
   * 飞书授权一键续期：后端先重测授权（多数红点是缓存或 token 已被 CLI 自动刷新），
   * 仍失效才发起 device flow 并返回授权链接与二维码。
   */
  feishuReloginStart(): Promise<FeishuRelogin> {
    return this.request<unknown>("/api/sync/feishu/relogin", { method: "POST" }).then(
      (body) => body as FeishuRelogin,
    );
  }

  /** 查询续期流程进度；CLI 授权成功后自动变成 success。 */
  feishuReloginState(): Promise<FeishuRelogin> {
    return this.request<unknown>("/api/sync/feishu/relogin").then(
      (body) => body as FeishuRelogin,
    );
  }

  healthProbe(probe: string): Promise<Contract.Probe> {
    return this.request<unknown>(`/api/system-health/probes/${encodeURIComponent(probe)}`).then(parseProbe);
  }

  settings(): Promise<Contract.RuntimeSettings> {
    return this.request<unknown>("/api/settings").then(parseRuntimeSettings);
  }

  modelProfiles(): Promise<Contract.ModelProfilesView> {
    return this.request<unknown>("/api/model-profiles").then(parseModelProfiles);
  }

  createModelProfile(
    payload: Contract.ModelProfilePayload,
  ): Promise<Contract.ModelProfilesView> {
    return this.request<unknown>("/api/model-profiles", {
      method: "POST",
      body: JSON.stringify(payload),
    }).then(parseModelProfiles);
  }

  updateModelProfile(
    profileId: string,
    payload: Contract.ModelProfilePayload,
  ): Promise<Contract.ModelProfilesView> {
    return this.request<unknown>(
      `/api/model-profiles/${encodeURIComponent(profileId)}`,
      { method: "PUT", body: JSON.stringify(payload) },
    ).then(parseModelProfiles);
  }

  deleteModelProfile(profileId: string): Promise<Contract.ModelProfilesView> {
    return this.request<unknown>(
      `/api/model-profiles/${encodeURIComponent(profileId)}`,
      { method: "DELETE" },
    ).then(parseModelProfiles);
  }

  activateModelProfile(profileId: string): Promise<Contract.ModelProfilesView> {
    return this.request<unknown>(
      `/api/model-profiles/${encodeURIComponent(profileId)}/activate`,
      { method: "POST" },
    ).then(parseModelProfiles);
  }

  saveSettings(
    body: Contract.RuntimeSettingsUpdate,
  ): Promise<Contract.RuntimeSettings> {
    return this.request<unknown>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }).then(parseRuntimeSettings);
  }

  clearAllKeys(): Promise<Contract.RuntimeSettings> {
    return this.request<unknown>("/api/settings/secrets", { method: "DELETE" }).then(parseRuntimeSettings);
  }

  usage(): Promise<Contract.Usage> {
    return this.request("/api/settings/usage");
  }

  refreshOfficialBill(): Promise<{ status: string; message: string }> {
    return this.request("/api/settings/official-bill/refresh", {
      method: "POST",
    });
  }

  createImportBatch(
    body: Contract.ImportBatchCreateRequest,
  ): Promise<Contract.ImportBatchCreated> {
    return this.request("/api/import-batches", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  importBatch(id: string): Promise<Contract.ImportBatch> {
    return this.request<unknown>(`/api/import-batches/${encodeURIComponent(id)}`).then(parseImportBatch);
  }

  createLocalImportBatch(
    body: Contract.LocalImportBatchRequest,
  ): Promise<Contract.ImportBatch> {
    return this.request<unknown>("/api/local-import-batches", {
      method: "POST",
      body: JSON.stringify(body),
    }).then(parseImportBatch);
  }

  createPackageImportBatch(
    body: Contract.PackageImportBatchRequest,
  ): Promise<Contract.ImportBatch> {
    return this.request<unknown>("/api/package-import-batches", {
      method: "POST",
      body: JSON.stringify(body),
    }).then(parseImportBatch);
  }

  packageImportBatch(id: string): Promise<Contract.ImportBatch> {
    return this.request<unknown>(`/api/package-import-batches/${encodeURIComponent(id)}`).then(parseImportBatch);
  }

  uploadPackageImportFile(
    batchId: string,
    fileId: string,
    file: File,
  ): Promise<{ status: string; sha256: string; idempotent: boolean }> {
    const body = new FormData();
    body.append("file", file);
    return this.request<{ status: string; sha256: string; idempotent: boolean }>(
      `/api/package-import-batches/${encodeURIComponent(batchId)}/files/${encodeURIComponent(fileId)}`,
      { method: "PUT", body },
      UPLOAD_REQUEST_TIMEOUT_MS,
    );
  }

  analyzePackageImportBatch(id: string): Promise<Contract.ImportBatch> {
    return this.request<unknown>(
      `/api/package-import-batches/${encodeURIComponent(id)}/analyze`,
      { method: "POST" },
    ).then(parseImportBatch);
  }

  uploadLocalImportVideo(
    batchId: string,
    itemId: number,
    file: File,
  ): Promise<Contract.LocalVideoUploadResult> {
    const body = new FormData();
    body.append("file", file);
    return this.request(
      `/api/local-import-batches/${encodeURIComponent(batchId)}/items/${itemId}/video`,
      { method: "PUT", body },
      UPLOAD_REQUEST_TIMEOUT_MS,
    );
  }

  updateImportItem(
    itemId: number,
    body: Contract.ImportItemUpdate,
  ): Promise<Contract.ImportItemUpdateResult> {
    return this.request(`/api/import-items/${itemId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }

  cancelImportBatch(id: string): Promise<Contract.ImportBatch> {
    return this.request<unknown>(`/api/import-batches/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }).then(parseImportBatch);
  }

  confirmImportBatch(
    id: string,
    items: Array<{ item_id: number; collection_id?: number }>,
  ): Promise<Contract.ImportConfirmation> {
    const assigned = items.filter(
      (item): item is { item_id: number; collection_id: number } =>
        item.collection_id !== undefined,
    );
    return this.request(`/api/import-batches/${encodeURIComponent(id)}/confirm`, {
      method: "POST",
      body: JSON.stringify({
        item_ids: items
          .filter((item) => item.collection_id === undefined)
          .map((item) => item.item_id),
        items: assigned,
      }),
    });
  }

  uploadImportAssets(
    itemId: number,
    files: File[],
  ): Promise<{ item_id: number; status: string; kind: string; message: string }> {
    const body = new FormData();
    for (const file of files) body.append("files", file);
    return this.request(`/api/import-items/${itemId}/assets`, {
      method: "POST",
      body,
    }, UPLOAD_REQUEST_TIMEOUT_MS);
  }

  removeImportItem(itemId: number): Promise<Contract.ImportBatch> {
    return this.request<unknown>(`/api/import-items/${itemId}`, { method: "DELETE" }).then(parseImportBatch);
  }

  async deleteImportItems(itemIds: number[]): Promise<void> {
    for (const id of itemIds) {
      try {
        await this.removeImportItem(id);
      } catch {
        // 单条删除失败不影响其余条目
      }
    }
  }

  retryImportItems(itemIds: number[]): Promise<void> {
    return this.request("/api/import-items/retry", {
      method: "POST",
      body: JSON.stringify({ item_ids: itemIds }),
    });
  }

  jobs(): Promise<Contract.Job[]> {
    return this.request<unknown>("/api/jobs").then(parseJobs);
  }

  cancelJob(id: string): Promise<Contract.Job> {
    return this.request(`/api/jobs/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    });
  }

  retryFailedImports(): Promise<{ job_id: string | null; retried: number; message: string }> {
    return this.request("/api/jobs/retry", { method: "POST" });
  }

  clearCompletedJobs(): Promise<{ deleted: number }> {
    return this.request("/api/jobs/completed", { method: "DELETE" });
  }

  collections(): Promise<{
    items: Contract.Collection[];
    summary: Contract.LibrarySummary;
  }> {
    return this.request<unknown>("/api/library/collections").then(parseCollections);
  }

  createCollection(title: string): Promise<Contract.Collection> {
    return this.request("/api/library/collections", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
  }

  updateCollectionSummaryPrompt(
    collectionId: number,
    summaryPrompt: string | null,
  ): Promise<Pick<Contract.Collection, "id" | "key" | "title" | "cover_url" | "summary_prompt">> {
    return this.request(`/api/library/collections/${collectionId}`, {
      method: "PUT",
      body: JSON.stringify({ summary_prompt: summaryPrompt }),
    });
  }

  addWorksToCollection(
    collectionId: number,
    workIds: number[],
  ): Promise<{
    collection_id: number;
    title: string;
    requested: number;
    eligible: number;
    added: number;
  }> {
    return this.request(`/api/library/collections/${collectionId}/works`, {
      method: "POST",
      body: JSON.stringify({ work_ids: workIds }),
    });
  }

  works(
    state: "pending" | "in_library" | "supplement" | "issues" | "archived",
    filters: {
      collectionId?: number;
      platform?: string;
      kind?: string;
      author?: string;
      source?: string;
      offset?: number;
    } = {},
  ): Promise<Contract.WorksPage> {
    const query = new URLSearchParams({
      library_state: state,
      offset: String(filters.offset ?? 0),
      limit: "60",
    });
    if (filters.collectionId !== undefined) {
      query.set("collection_id", String(filters.collectionId));
    }
    if (filters.platform) query.set("platform", filters.platform);
    if (filters.kind) query.set("kind", filters.kind);
    if (filters.author) query.set("author", filters.author);
    if (filters.source) query.set("source", filters.source);
    return this.request<unknown>(`/api/library/works?${query}`).then(parseWorksPage);
  }

  platformStatus(platform: Contract.PlatformId): Promise<Contract.PlatformStatus> {
    return this.request(`/api/platforms/${encodeURIComponent(platform)}/status`);
  }

  pull(
    platform: Contract.PlatformId,
    body: { mode: string; limit?: number; enqueue?: boolean; profile_url?: string },
  ): Promise<Contract.PlatformPullResult> {
    return this.request(`/api/platforms/${encodeURIComponent(platform)}/pull`, {
      method: "POST",
      body: JSON.stringify(body),
    }, PULL_REQUEST_TIMEOUT_MS);
  }

  domCollect(scope: string): Promise<{ task_id: string; status: string }> {
    return this.request("/api/platforms/douyin/dom-collect", {
      method: "POST",
      body: JSON.stringify({ scope }),
    });
  }

  domCollectStatus(
    taskId: string,
  ): Promise<{ task_id: string; status: string; message: string; pulled_count: number; batch_id: string | null }> {
    return this.request(`/api/platforms/douyin/dom-collect/${encodeURIComponent(taskId)}`);
  }

  lastPlatformBatch(): Promise<{
    id: string;
    state: string;
    total_items: number;
    created_at: string;
    counts: Record<string, number>;
  } | null> {
    return this.request("/api/platforms/douyin/last-batch");
  }

  collectedVideos(scope: string): Promise<{
    scope: string;
    count: number;
    groups: Array<{
      name: string;
      count: number;
      items: Array<{
        aweme_id: string;
        title: string | null;
        author: string | null;
        source_url: string;
        published_at: string | null;
        cover_url: string | null;
      }>;
    }>;
    items: Array<{
      aweme_id: string;
      title: string | null;
      author: string | null;
      source_url: string;
      published_at: string | null;
      cover_url: string | null;
    }>;
  }> {
    return this.request(`/api/platforms/douyin/collected?scope=${encodeURIComponent(scope)}`);
  }

  syncSelected(
    scope: string,
    awemeIds: string[],
  ): Promise<{ batch_id: string; count: number; enqueued: boolean; message: string }> {
    return this.request("/api/platforms/douyin/sync-selected", {
      method: "POST",
      body: JSON.stringify({ scope, aweme_ids: awemeIds }),
    });
  }

  uploadWorkSupplement(
    id: number,
    files: File[],
  ): Promise<Contract.WorkSupplementResult> {
    const body = new FormData();
    body.append("rights_attested", "true");
    for (const file of files) body.append("files", file, file.name);
    return this.request(`/api/library/works/${id}/supplement`, {
      method: "POST",
      body,
    }, UPLOAD_REQUEST_TIMEOUT_MS);
  }

  restore(id: number): Promise<unknown> {
    return this.request(`/api/library/works/${id}/restore`, { method: "POST" });
  }

  retry(id: number): Promise<unknown> {
    return this.request(`/api/library/works/${id}/retry`, { method: "POST" });
  }

  ingest(workIds: number[]): Promise<Contract.Job> {
    return this.request("/api/library/ingest/jobs", {
      method: "POST",
      body: JSON.stringify({ work_ids: workIds }),
    });
  }

  summarize(workIds: number[]): Promise<Contract.Job> {
    return this.request("/api/library/summaries/jobs", {
      method: "POST",
      body: JSON.stringify({ work_ids: workIds }),
    });
  }

  workSummary(id: number): Promise<Contract.WorkSummaryDetail> {
    return this.request<unknown>(`/api/library/works/${id}/summary`).then(parseWorkSummaryDetail);
  }

  obsidianManifest(workIds: number[]): Promise<Contract.ObsidianManifest> {
    return this.request("/api/library/obsidian/manifest", {
      method: "POST",
      body: JSON.stringify({ work_ids: workIds }),
    });
  }

  remove(id: number): Promise<unknown> {
    return this.request(`/api/library/works/${id}`, { method: "DELETE" });
  }

  // --- 统一 Cookie 库 -------------------------------------------------------

  cookieVault(): Promise<CookieVaultState> {
    return this.request<CookieVaultState>("/api/cookies");
  }

  /** 一份浏览器全量导出，后端按域名自动拆到各平台。 */
  uploadCookieVault(file: File): Promise<CookieVaultState> {
    const body = new FormData();
    body.append("file", file);
    return this.request<CookieVaultState>(
      "/api/cookies/upload",
      { method: "POST", body },
      UPLOAD_REQUEST_TIMEOUT_MS,
    );
  }

  uploadPlatformCookie(platform: string, file: File): Promise<CookiePlatformStatus> {
    const body = new FormData();
    body.append("file", file);
    return this.request<CookiePlatformStatus>(
      `/api/cookies/${encodeURIComponent(platform)}/upload`,
      { method: "POST", body },
      UPLOAD_REQUEST_TIMEOUT_MS,
    );
  }

  deletePlatformCookie(platform: string): Promise<CookiePlatformStatus> {
    return this.request<CookiePlatformStatus>(
      `/api/cookies/${encodeURIComponent(platform)}`,
      { method: "DELETE" },
    );
  }

  /** 直接给 <a download> 用：核对库里存的与喂给 yt-dlp 的是同一份。 */
  cookieDownloadUrl(platform: string): string {
    return this.endpoint(`/api/cookies/${encodeURIComponent(platform)}/download`);
  }
}

export const api = new LocalApiClient(API_BASE);
