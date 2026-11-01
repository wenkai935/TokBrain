"use client";

/* eslint-disable @next/next/no-img-element -- public covers and local assets are user-selected sources. */

import { useCallback, useEffect, useRef, useState } from "react";

import { Library } from "../components/Library";
import { ImportWorkspace } from "../components/ImportWorkspace";
import { ParseWorkspace } from "../components/ParseWorkspace";
import { Settings, ThemePicker } from "../components/Settings";
import {
  api,
  Collection,
  DiscoveryView,
  FeishuRelogin,
  Health,
  ImportBatch,
  ImportItem,
  ImportItemUpdate,
  Job,
  LibrarySummary,
  RuntimeSettings,
  Usage,
  WorksPage,
} from "../lib/api";
import { isUserCancelled, reason } from "../lib/errors";
import { removeConfirmedImportItems } from "../lib/importBatches";
import {
  clearLibraryReturnContext,
  readLibraryReturnContext,
} from "../lib/libraryReturn";
import { mergeWorksPage } from "../lib/libraryPagination";
import { activeProcessingJobIds, didAnyJobReachTerminal, isActiveProcessingJob, operationIncludesJob } from "../lib/jobPolling";
import type { LibraryState } from "../lib/uiTypes";
import { rememberImportBatch, rememberLocalImportRightsAttestation, storedImportBatchIds, storedLocalImportRightsAttestation } from "../lib/importBatchStorage";
import { useTheme } from "../themes/ThemeProvider";

type Tab = "import" | "parse" | "library" | "settings";

// 左下角飞书状态面板：轮询间隔（前端刷新节奏，默认 10 分钟）与最近查询时间，
// 均落在浏览器缓存（localStorage），刷新/重开页面后沿用。
const FEISHU_POLL_KEY = "tokbrain.feishuPollSec";
const FEISHU_LAST_QUERY_KEY = "tokbrain.feishuLastQuery";
const DEFAULT_FEISHU_POLL_SEC = 600;
const FEISHU_POLL_OPTIONS = [10, 30, 60, 300, 600, 1800];
// 飞书发现批次：轮询只读一次本地 sqlite，8s 一次足够及时且不会打飞书接口。
const DISCOVERY_POLL_MS = 8000;
// 记住「已点稍后」的批次签名，刷新页面不再弹；有新链接（签名变）才重新弹。
const DISCOVERY_DISMISS_KEY = "tokbrain.discovery.dismissed";

function storedFeishuPollSec(): number {
  try {
    const raw = Number(localStorage.getItem(FEISHU_POLL_KEY));
    if (FEISHU_POLL_OPTIONS.includes(raw)) return raw;
  } catch {
    /* ignore */
  }
  return DEFAULT_FEISHU_POLL_SEC;
}

function rememberFeishuPollSec(seconds: number): void {
  try {
    localStorage.setItem(FEISHU_POLL_KEY, String(seconds));
  } catch {
    /* ignore */
  }
}

function storedFeishuLastQuery(): string {
  try {
    return localStorage.getItem(FEISHU_LAST_QUERY_KEY) || "";
  } catch {
    return "";
  }
}

function rememberFeishuLastQuery(value: string): void {
  try {
    localStorage.setItem(FEISHU_LAST_QUERY_KEY, value);
  } catch {
    /* ignore */
  }
}

const EMPTY_SUMMARY: LibrarySummary = {
  candidate_count: 0,
  selected_count: 0,
  local_item_count: 0,
  issue_count: 0,
  archived_count: 0,
  known_distinct_count: 0,
  remote_folder_item_sum: 0,
};
const EMPTY_WORKS: WorksPage = {
  items: [],
  total: 0,
  selected_count: 0,
  account_selected_count: 0,
  next_offset: null,
};
const TERMINAL_BATCH_STATES = new Set(["succeeded", "partial", "failed", "cancelled"]);
const ACTIVE_ITEM_STATES = new Set(["queued", "resolving", "pending"]);

function batchIsTerminal(batch: ImportBatch) {
  return TERMINAL_BATCH_STATES.has(batch.state);
}

function batchHasActiveItems(batch: ImportBatch) {
  return batch.items.some((item) => ACTIVE_ITEM_STATES.has(item.status));
}

const RETAINED_IMPORT_STATUSES = new Set(["ready", "needs_local_file"]);

function jobLabel(job: Job) {
  if (job.job_type === "link_preview") return "视频链接预检";
  if (job.job_type === "ingest") return "作品入库";
  if (job.job_type === "summarize") return "整理作品精华";
  return "本地任务";
}

function stateLabel(state: string) {
  return (
    {
      queued: "等待中",
      running: "进行中",
      automating: "自动确认中",
      cancelling: "安全停止中",
      cancelled: "已中断",
      succeeded: "已完成",
      partial: "部分完成",
      failed: "失败",
    }[state] || state
  );
}

function samePreviewWork(left: ImportItem, right: ImportItem) {
  if (
    left.platform_work_id
    && right.platform_work_id
    && left.platform_work_id === right.platform_work_id
  ) {
    return true;
  }
  return Boolean(
    left.normalized_url
    && right.normalized_url
    && left.normalized_url === right.normalized_url,
  );
}

export default function Home() {
  const { theme } = useTheme();
  const [tab, setTab] = useState<Tab>("import");
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [summary, setSummary] = useState<LibrarySummary>(EMPTY_SUMMARY);
  const [worksPage, setWorksPage] = useState<WorksPage>(EMPTY_WORKS);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryState, setLibraryState] = useState<LibraryState>("pending");
  const [collectionId, setCollectionId] = useState<number | null>(null);
  const [platform, setPlatform] = useState("");
  const [kind, setKind] = useState("");
  const [author, setAuthor] = useState("");
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [rightsAttested, setRightsAttested] = useState(false);
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [retainedBatches, setRetainedBatches] = useState<ImportBatch[]>([]);
  const [selectedItems, setSelectedItems] = useState<Set<number>>(new Set());
  const [itemCollections, setItemCollections] = useState<Map<number, number>>(new Map());
  const [feishuPollSec, setFeishuPollSec] = useState<number>(DEFAULT_FEISHU_POLL_SEC);
  // 左下角状态：null=检测中；飞书行显示授权/连接态 + 最近查询时间；obsidian 行
  // 显示本地知识库是否可写（前后端均为本次新增）。
  const [feishuConnected, setFeishuConnected] = useState<boolean | null>(null);
  const [feishuLastQuery, setFeishuLastQuery] = useState<string>("");
  const [feishuRows, setFeishuRows] = useState(0);
  const [obsidianConnected, setObsidianConnected] = useState<boolean | null>(null);
  const [obsidianPath, setObsidianPath] = useState("");
  // mounted 门控：SSR 与客户端 hydration 首帧统一用占位/默认值，挂载后再显示
  // 来自 localStorage 的客户端派生值，避免服务端/客户端文本不一致导致水合报错。
  const [mounted, setMounted] = useState(false);
  // 飞书授权一键续期：点按钮先重测授权，仍失效才弹二维码/链接等用户扫码确认。
  const [relogin, setRelogin] = useState<FeishuRelogin | null>(null);
  const [reloginBusy, setReloginBusy] = useState(false);

  // 飞书发现批次：后端在补齐期间返回 filling，此时整块不渲染，数字不会跳。
  const [discovery, setDiscovery] = useState<DiscoveryView | null>(null);
  const [discoveryPicked, setDiscoveryPicked] = useState<Set<number>>(new Set());
  const [discoveryOpen, setDiscoveryOpen] = useState(false);
  const [discoveryBusy, setDiscoveryBusy] = useState(false);
  const [discoveryDismissed, setDiscoveryDismissed] = useState("");
  const discoverySignature = useRef("");
  const discoveryDismissLoaded = useRef(false);
  const restoredLibraryPosition = useRef(false);
  const batchRef = useRef<ImportBatch | null>(null);
  const libraryRequestRef = useRef(0);
  const libraryLoadingRef = useRef(false);
  const worksPageLengthRef = useRef(worksPage.items.length);
  const activeOperationRef = useRef("");
  worksPageLengthRef.current = worksPage.items.length;

  useEffect(() => {
    batchRef.current = batch;
  }, [batch]);

  // 挂载后从浏览器缓存恢复飞书轮询间隔与最近查询时间（仅客户端执行）。
  // 服务端渲染与 hydration 首帧均用默认值/占位，规避水合不匹配。
  useEffect(() => {
    setMounted(true);
    setFeishuPollSec(storedFeishuPollSec());
    const lastQuery = storedFeishuLastQuery();
    if (lastQuery) setFeishuLastQuery(lastQuery);
  }, []);

  const loadHealth = useCallback(async () => {
    setHealth(await api.health());
  }, []);

  const loadSettings = useCallback(async () => {
    setSettings(await api.settings());
  }, []);

  const loadUsage = useCallback(async () => {
    setUsage(await api.usage());
  }, []);

  const loadJobs = useCallback(async () => {
    const next = await api.jobs();
    setJobs(next);
  }, []);

  const loadCollections = useCallback(async () => {
    const groups = await api.collections();
    setCollections(groups.items);
    setSummary(groups.summary);
  }, []);

  const refreshSharedState = useCallback(async () => {
    await Promise.allSettled([
      loadSettings(),
      loadUsage(),
      loadJobs(),
      loadCollections(),
    ]);
  }, [loadCollections, loadJobs, loadSettings, loadUsage]);

  const loadLibrary = useCallback(
    async (append = false) => {
      if (append && libraryLoadingRef.current) return;
      const requestId = ++libraryRequestRef.current;
      libraryLoadingRef.current = true;
      setLibraryLoading(true);
      const offset = append ? worksPageLengthRef.current : 0;
      try {
        const page = await api.works(libraryState, {
          collectionId: collectionId ?? undefined,
          platform: platform || undefined,
          kind: kind || undefined,
          author: author || undefined,
          source: source || undefined,
          offset,
        });
        if (requestId !== libraryRequestRef.current) return;
        setWorksPage((current) => mergeWorksPage(current, page, append));
      } catch (value) {
        if (requestId === libraryRequestRef.current) throw value;
      } finally {
        if (requestId === libraryRequestRef.current) {
          libraryLoadingRef.current = false;
          setLibraryLoading(false);
        }
      }
    },
    [collectionId, libraryState, platform, kind, author, source],
  );

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("tab") === "library") {
      setTab("library");
      const state = params.get("state");
      if (state && ["pending", "in_library", "supplement", "issues", "archived"].includes(state)) {
        setLibraryState(state as LibraryState);
      }
      const restoredCollectionId = Number(params.get("collection_id"));
      if (Number.isInteger(restoredCollectionId) && restoredCollectionId > 0) {
        setCollectionId(restoredCollectionId);
      }
    }
  }, []);

  useEffect(() => {
    Promise.allSettled([loadHealth(), refreshSharedState()]).catch(() => undefined);
  }, [loadHealth, refreshSharedState]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setRightsAttested(storedLocalImportRightsAttestation());
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  function changeRightsAttestation(attested: boolean) {
    setRightsAttested(attested);
    rememberLocalImportRightsAttestation(attested);
  }

  function changeFeishuPoll(seconds: number) {
    setFeishuPollSec(seconds);
    rememberFeishuPollSec(seconds);
    const label = seconds >= 60 ? `${seconds / 60} 分钟` : `${seconds} 秒`;
    setNotice(`飞书状态刷新间隔已设为 ${label}`);
  }

  const syncInFlight = useRef(false);
  const loadSync = useCallback(async () => {
    // 单飞：上一轮未结束则不并发，避免打爆 lark-cli 子进程
    if (syncInFlight.current) return;
    syncInFlight.current = true;
    try {
      const state = await api.syncOverview();
      setFeishuConnected(state.feishu.connected);
      setFeishuRows(state.feishu.rows);
      if (state.feishu.last_query) {
        setFeishuLastQuery(state.feishu.last_query);
        rememberFeishuLastQuery(state.feishu.last_query);
      }
      setObsidianConnected(state.obsidian.connected);
      setObsidianPath(state.obsidian.path || "");
    } catch {
      // 后端暂不可达：保留上一轮状态，下一轮自动重试，不打扰用户
    } finally {
      syncInFlight.current = false;
    }
  }, []);

  useEffect(() => {
    // 浏览器一打开即真实查一次飞书表并开始按所选间隔轮询
    loadSync();
    const timer = window.setInterval(loadSync, Math.max(10, feishuPollSec) * 1000);
    return () => window.clearInterval(timer);
  }, [loadSync, feishuPollSec]);

  /**
   * 飞书发现批次轮询。
   *
   * 后端在 bridge 补齐期间返回 state=filling（清单为空），这里不做任何展示；
   * 补齐结束才一次性给出清单。签名只在「这一批条目变了」时才重置勾选，
   * 否则用户手工取消的勾会被每 8 秒的轮询冲掉。
   */
  const loadDiscovery = useCallback(async () => {
    try {
      const state = await api.discovery();
      setDiscovery(state);
      if (!discoveryDismissLoaded.current) {
        // 首次拿到结果后再读本地记忆（异步回调里 setState，不触发级联渲染）
        discoveryDismissLoaded.current = true;
        setDiscoveryDismissed(window.localStorage.getItem(DISCOVERY_DISMISS_KEY) || "");
      }
      const signature = state.items.map((item) => item.record_id).join(",");
      if (signature !== discoverySignature.current) {
        discoverySignature.current = signature;
        setDiscoveryPicked(
          new Set(
            state.items
              .filter((item) => item.state === "ready" && item.item_id !== null)
              .map((item) => item.item_id as number),
          ),
        );
        setDiscoveryOpen(false);
      }
    } catch {
      // 后端暂不可达：保留上一轮结果，下一轮自动重试
    }
  }, []);

  useEffect(() => {
    loadDiscovery();
    const timer = window.setInterval(loadDiscovery, DISCOVERY_POLL_MS);
    return () => window.clearInterval(timer);
  }, [loadDiscovery]);

  const toggleDiscoveryItem = useCallback((itemId: number) => {
    setDiscoveryPicked((current) => {
      const next = new Set(current);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }, []);

  const approveDiscovery = useCallback(async () => {
    const itemIds = [...discoveryPicked];
    if (!itemIds.length || discoveryBusy) return;
    setDiscoveryBusy(true);
    setError("");
    try {
      const result = await api.confirmDiscovery(itemIds);
      setNotice(
        result.job_id
          ? `已提交 ${result.confirmed} 条解析，进度见下方任务队列`
          : `已确认 ${result.confirmed} 条，但未取得作品，请到解析页查看`,
      );
      await loadDiscovery();
      await loadJobs();
    } catch (value) {
      setError(reason(value, "提交解析失败"));
    } finally {
      setDiscoveryBusy(false);
    }
  }, [discoveryPicked, discoveryBusy, loadDiscovery, loadJobs]);

  /** 一键续期：先让后端重测授权，多数情况重测即恢复；仍失效才进入扫码流程。 */
  const startFeishuRelogin = useCallback(async () => {
    if (reloginBusy) return;
    setReloginBusy(true);
    setError("");
    try {
      const state = await api.feishuReloginStart();
      if (state.state === "success") {
        setNotice(state.message || "飞书授权已恢复");
        setRelogin(null);
        await loadSync();
      } else if (state.state === "failed") {
        setError(state.message || "发起飞书授权失败");
        setRelogin(state);
      } else {
        setRelogin(state);
      }
    } catch (value) {
      setError(reason(value, "发起飞书授权失败"));
    } finally {
      setReloginBusy(false);
    }
  }, [loadSync, reloginBusy]);

  const reloginActive = relogin?.state === "starting" || relogin?.state === "waiting";
  useEffect(() => {
    // 续期流程进行中：每 2s 轮询一次，CLI 授权成功后自动刷新连接状态并收起弹层
    if (!reloginActive) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const state = await api.feishuReloginState();
        if (cancelled) return;
        if (state.state === "success") {
          setNotice(state.message || "飞书授权已恢复");
          setRelogin(null);
          await loadSync();
          return;
        }
        setRelogin(state);
      } catch {
        // 后端暂不可达：保留弹层，下一轮自动重试
      }
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [reloginActive, loadSync]);

  useEffect(() => {
    let cancelled = false;
    async function recoverImports() {
      const ids = [...new Set(storedImportBatchIds())];
      if (!ids.length) return;
      const settled = await Promise.allSettled(ids.map((id) => api.importBatch(id)));
      if (cancelled) return;
      const loaded = settled
        .filter((result): result is PromiseFulfilledResult<ImportBatch> => result.status === "fulfilled")
        .map((result) => result.value)
        .sort((left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime());
      const loadedIds = new Set(loaded.map((entry) => entry.id));
      const recovered = loaded
        .map((entry) => {
          if (!batchIsTerminal(entry)) return entry;
          // Platform-pull batches are created terminal but may still be resolving
          // items in the background coordinator; keep the whole list while that runs.
          const active = entry.items.filter((item) => ACTIVE_ITEM_STATES.has(item.status));
          const items = active.length
            ? entry.items
            : entry.items.filter((item) => RETAINED_IMPORT_STATUSES.has(item.status));
          return { ...entry, items };
        })
        .filter((entry) => !batchIsTerminal(entry) || entry.items.length);
      if (!recovered.length) {
        setRetainedBatches((current) => current.filter((entry) => !loadedIds.has(entry.id)));
        return;
      }
      const latestActive = recovered.findLast((entry) => !batchIsTerminal(entry));
      const latest = latestActive || recovered.at(-1)!;
      const currentBatch = batchRef.current;
      const primary = currentBatch || latest;
      setBatch((current) => current || latest);
      setRetainedBatches((current) => {
        const merged = new Map(
          current
            .filter((entry) => !loadedIds.has(entry.id))
            .map((entry) => [entry.id, entry]),
        );
        for (const entry of recovered) {
          if (entry.id !== primary.id) merged.set(entry.id, entry);
        }
        return [...merged.values()];
      });
      const readyItems = recovered.flatMap((entry) =>
        entry.items.filter((item) => item.status === "ready"),
      );
      setSelectedItems((current) => new Set([
        ...current,
        ...readyItems.map((item) => item.id),
      ]));
      setItemCollections((current) => {
        const updated = new Map(current);
        for (const item of readyItems) {
          if (item.target_collection_id != null) {
            updated.set(item.id, item.target_collection_id);
          }
        }
        return updated;
      });
      setNotice("已恢复刷新前的导入批次；可继续查看进度、补件或确认入库");
    }
    recoverImports().catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (tab === "library") loadLibrary(false).catch((value) => setError(reason(value)));
  }, [loadLibrary, tab]);

  useEffect(() => {
    if (restoredLibraryPosition.current || tab !== "library" || !worksPage.items.length) return;
    const context = readLibraryReturnContext();
    if (
      !context
      || context.state !== libraryState
      || context.collectionId !== collectionId
    ) return;
    restoredLibraryPosition.current = true;
    const frame = window.requestAnimationFrame(() => {
      window.scrollTo({ top: context.scrollY, behavior: "auto" });
      clearLibraryReturnContext();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [collectionId, libraryState, tab, worksPage.items.length]);

  useEffect(() => {
    if (!batch) return;
    // Poll while the batch is in progress, or while a (terminal) platform-pull
    // batch still has items resolving in the background coordinator.
    if (batchIsTerminal(batch) && !batchHasActiveItems(batch)) return;
    const timer = window.setTimeout(async () => {
      try {
        const next = await api.importBatch(batch.id);
        setBatch(next);
        if (batchIsTerminal(next)) {
          const readyIds = next.items
            .filter((item) => item.status === "ready")
            .map((item) => item.id);
          const duplicateCount = Number(next.progress.duplicates || 0);
          const confirmableCount = next.items.filter((item) =>
            RETAINED_IMPORT_STATUSES.has(item.status),
          ).length;
          setSelectedItems((current) => new Set([...current, ...readyIds]));
          if (duplicateCount > 0) {
            setNotice(
              confirmableCount > 0
                ? `已去掉 ${duplicateCount} 条重复，${confirmableCount} 个不同作品预检完成`
                : `已去掉 ${duplicateCount} 条重复，没有新的作品需要预检`,
            );
          }
          await Promise.allSettled([loadUsage(), loadCollections()]);
        }
      } catch (value) {
        setError(reason(value, "读取解析进度失败"));
      }
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [batch, loadCollections, loadUsage]);

  useEffect(() => {
    const activeIds = activeProcessingJobIds(jobs);
    if (!activeIds.size) return;
    const timer = window.setTimeout(async () => {
      try {
        const next = await api.jobs();
        setJobs(next);
        const reachedTerminal = didAnyJobReachTerminal(jobs, next);
        if (reachedTerminal) {
          await Promise.allSettled([
            loadUsage(),
            loadCollections(),
            ...(tab === "library" ? [loadLibrary(false)] : []),
          ]);
        }
      } catch {
        // The next polling cycle or a manual refresh will retry local status reads.
      }
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [jobs, loadCollections, loadLibrary, loadUsage, tab]);

  function beginOperation(name: string) {
    if (activeOperationRef.current) return false;
    activeOperationRef.current = name;
    setBusy(name);
    setError("");
    return true;
  }

  function endOperation(name: string) {
    if (activeOperationRef.current !== name) return;
    activeOperationRef.current = "";
    setBusy("");
  }

  async function perform(name: string, operation: () => Promise<unknown>, success: string) {
    if (!beginOperation(name)) return false;
    try {
      const result = await operation();
      setNotice(success);
      if (operationIncludesJob(result)) {
        await loadJobs();
      } else if (name !== "health-check" && name !== "obsidian-image-folder") {
        await refreshSharedState();
      }
      return true;
    } catch (value) {
      if (isUserCancelled(value)) return false;
      setError(reason(value));
      return false;
    } finally {
      endOperation(name);
    }
  }

  async function cancelBatch() {
    if (!batch || !beginOperation("cancel-batch")) return;
    try {
      setBatch(await api.cancelImportBatch(batch.id));
      setNotice("正在安全停止；已成功解析的结果会继续保留");
    } catch (value) {
      setError(reason(value));
    } finally {
      endOperation("cancel-batch");
    }
  }

  async function acceptLocalBatch(next: ImportBatch) {
    const currentBatch = batchRef.current;
    if (currentBatch && currentBatch.id !== next.id) {
      const retainedItems = currentBatch.items.filter((item) =>
        RETAINED_IMPORT_STATUSES.has(item.status),
      );
      if (retainedItems.length) {
        setRetainedBatches((current) => [
          ...current.filter((entry) => entry.id !== currentBatch.id && entry.id !== next.id),
          { ...currentBatch, items: retainedItems },
        ]);
      }
    }
    batchRef.current = next;
    setBatch(next);
    setTab("parse");
    const readyItems = next.items.filter((item) => item.status === "ready");
    setSelectedItems((current) => new Set([
      ...current,
      ...readyItems.map((item) => item.id),
    ]));
    setItemCollections((current) => {
      const updated = new Map(current);
      for (const item of readyItems) {
        if (item.target_collection_id != null) {
          updated.set(item.id, item.target_collection_id);
        }
      }
      return updated;
    });
    const failed = next.items.filter((item) => item.status === "failed").length;
    setNotice(
      failed > 0
        ? `本地视频已验证 ${readyItems.length} 个，${failed} 个失败；成功项已自动勾选`
        : `${readyItems.length} 个本地视频已验证并自动勾选，可以确认加入待入库`,
    );
    await refreshSharedState();
  }

  async function acceptPlatformBatch(batchId: string) {
    try {
      const next = await api.importBatch(batchId);
      rememberImportBatch(batchId);
      batchRef.current = next;
      setBatch(next);
      setTab("parse");
      const readyItems = next.items.filter((item) => item.status === "ready");
      setSelectedItems((current) => new Set([...current, ...readyItems.map((item) => item.id)]));
      setItemCollections((current) => {
        const updated = new Map(current);
        for (const item of readyItems) {
          if (item.target_collection_id != null) updated.set(item.id, item.target_collection_id);
        }
        return updated;
      });
      setNotice(
        batchHasActiveItems(next)
          ? `已采集 ${next.total_items} 条视频，正在解析，可到「解析」页查看实时进度`
          : `已采集 ${next.total_items} 条视频，解析结果可到「解析」页查看`,
      );
      await refreshSharedState();
    } catch (value) {
      setError(reason(value, "读取解析进度失败"));
    }
  }

  async function editPreviewItem(item: ImportItem, changes: ImportItemUpdate) {
    const owner = [batch, ...retainedBatches].find((candidate) =>
      candidate?.items.some((entry) => entry.id === item.id),
    );
    const operationName = `edit-preview-${item.id}`;
    if (!owner || !beginOperation(operationName)) return;
    try {
      await api.updateImportItem(item.id, changes);
      const next = await api.importBatch(owner.id);
      if (batch?.id === owner.id) {
        setBatch(next);
      } else {
        setRetainedBatches((current) =>
          current.map((entry) => entry.id === owner.id ? next : entry),
        );
      }
      setNotice("本地视频信息已保存");
    } catch (value) {
      setError(reason(value, "保存本地视频信息失败"));
    } finally {
      endOperation(operationName);
    }
  }

  async function removePreviewItem(item: ImportItem) {
    if (!window.confirm("确认删除这条预检结果？已加入知识库的作品不会在这里被删除。")) return;
    const owner = [batch, ...retainedBatches].find((candidate) =>
      candidate?.items.some((entry) => entry.id === item.id),
    );
    const operationName = `remove-preview-${item.id}`;
    if (!owner || !beginOperation(operationName)) return;
    try {
      const next = await api.removeImportItem(item.id);
      if (batch?.id === owner.id) {
        setBatch(next.items.length ? next : null);
      } else {
        setRetainedBatches((current) =>
          current.flatMap((entry) => {
            if (entry.id !== owner.id) return [entry];
            const retained = {
              ...next,
              items: next.items.filter((candidate) =>
                RETAINED_IMPORT_STATUSES.has(candidate.status),
              ),
            };
            return retained.items.length ? [retained] : [];
          }),
        );
      }
      setSelectedItems((current) => {
        const updated = new Set(current);
        updated.delete(item.id);
        return updated;
      });
      setItemCollections((current) => {
        const updated = new Map(current);
        updated.delete(item.id);
        return updated;
      });
      setNotice("预检结果已删除");
    } catch (value) {
      setError(reason(value, "删除预检结果失败"));
    } finally {
      endOperation(operationName);
    }
  }

  async function confirmBatch(itemIds: number[]) {
    if (!itemIds.length || activeOperationRef.current) return;
    const requestedItems = new Set(itemIds);
    const candidates = [...retainedBatches, ...(batch ? [batch] : [])];
    const manualCollectionId = collections.find((group) => group.key === "manual-import")?.id;
    const uniqueItems: ImportItem[] = [];
    const groups = candidates
      .map((entry) => ({
        batch: entry,
        items: entry.items
          .filter((item) => {
            if (
              !requestedItems.has(item.id)
              || !RETAINED_IMPORT_STATUSES.has(item.status)
              || uniqueItems.some((candidate) => samePreviewWork(candidate, item))
            ) {
              return false;
            }
            uniqueItems.push(item);
            return true;
          })
          .map((item) => ({
            item_id: item.id,
            collection_id: itemCollections.get(item.id) ?? manualCollectionId,
          })),
      }))
      .filter((entry) => entry.items.length);
    if (!groups.length) return;
    const confirmedIds: number[] = [];
    const confirmedItemIds = new Set<number>();
    const failures: string[] = [];
    if (!beginOperation("confirm")) return;
    try {
      for (const group of groups) {
        try {
          const result = await api.confirmImportBatch(group.batch.id, group.items);
          confirmedIds.push(...result.work_ids);
          group.items.forEach((item) => confirmedItemIds.add(item.item_id));
        } catch (value) {
          failures.push(`${group.batch.id}: ${reason(value, "确认失败")}`);
        }
      }

      if (!confirmedItemIds.size) {
        setError(failures.join("；") || "确认入库失败");
        return;
      }

      const uniqueConfirmedIds = [...new Set(confirmedIds)];
      setNotice(
        failures.length
          ? `已确认 ${uniqueConfirmedIds.length} 个不同作品；${failures.length} 个批次失败，失败项仍保留`
          : `已将 ${uniqueConfirmedIds.length} 个不同作品加入待入库，可直接在知识库点击“开始入库”处理`,
      );
      setSelectedItems((current) => {
        const next = new Set(current);
        confirmedItemIds.forEach((id) => next.delete(id));
        return next;
      });
      setItemCollections((current) => {
        const next = new Map(current);
        confirmedItemIds.forEach((id) => next.delete(id));
        return next;
      });

      const settled = await Promise.allSettled(candidates.map((entry) => api.importBatch(entry.id)));
      const refreshed = candidates.map((entry, index) => {
        const result = settled[index];
        if (result.status === "fulfilled") return result.value;
        return removeConfirmedImportItems(entry, confirmedItemIds);
      });
      const current = batch
        ? refreshed.find((entry) => entry.id === batch.id) || null
        : null;
      setBatch(current);
      setRetainedBatches(
        refreshed
          .filter((entry) => entry.id !== batch?.id)
          .map((entry) => ({
            ...entry,
            items: entry.items.filter((item) =>
              RETAINED_IMPORT_STATUSES.has(item.status),
            ),
          }))
          .filter((entry) => entry.items.length),
      );
      if (failures.length) setError(failures.join("；"));
      await refreshSharedState();
    } finally {
      endOperation("confirm");
    }
  }

  async function retryFailedImports() {
    if (!beginOperation("retry-failed-imports")) return;
    try {
      const result = await api.retryFailedImports();
      setNotice(result.message);
      if (result.job_id) await loadJobs();
    } catch (value) {
      setError(reason(value, "重试失败导入失败"));
    } finally {
      endOperation("retry-failed-imports");
    }
  }

  const activeJobs = jobs
    .filter(isActiveProcessingJob)
    .sort((left, right) => {
      const priority = (job: Job) => job.state === "running" || job.state === "cancelling" ? 0 : 1;
      return priority(left) - priority(right)
        || new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
    });
  const titles: Record<Tab, string> = {
    import: "导入视频",
    parse: theme.copy.pages.parse,
    library: theme.copy.pages.library,
    settings: theme.copy.pages.settings,
  };

  // 只用 record_id 拼签名：标题变化不该让「已稍后」的记忆失效。
  const discoverySignatureText = discovery
    ? discovery.items.map((item) => item.record_id).join(",")
    : "";
  const discoveryReady = discovery?.state === "ready" && discovery.total > 0;
  // 点了「稍后」就降级成 topbar 上的小 chip，而不是消失——否则这批链接永久沉默。
  const discoveryChip = discoveryReady && discoverySignatureText === discoveryDismissed;
  const discoveryVisible = discoveryReady && !discoveryChip;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">{theme.mark}</span>
          <div><strong>TokBrain</strong><small>{theme.copy.brandTagline}</small></div>
        </div>
        <nav aria-label="主要功能">
          <button type="button" aria-current={tab === "import" ? "page" : undefined} className={tab === "import" ? "active" : ""} onClick={() => setTab("import")}>导入<i /></button>
          <button type="button" aria-current={tab === "parse" ? "page" : undefined} className={tab === "parse" ? "active" : ""} onClick={() => setTab("parse")}>解析<i /></button>
          <button type="button" aria-current={tab === "library" ? "page" : undefined} className={tab === "library" ? "active" : ""} onClick={() => setTab("library")}>知识库<i /></button>
          <button type="button" aria-current={tab === "settings" ? "page" : undefined} className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}>设置<i /></button>
        </nav>
        <div className="theme-ambient" aria-hidden="true">
          <span className="ambient-symbol" />
          {Array.from({ length: 7 }, (_, index) => <i key={index} />)}
        </div>
        <div className="sidebar-foot">
          <div className="status-row">
            <span className={`signal ${feishuConnected == null ? "checking" : feishuConnected ? "healthy" : "down"}`} />
            <div>
              <strong>{feishuConnected == null ? "飞书状态检测中…" : feishuConnected ? "飞书已连接" : "飞书授权过期"}</strong>
              <small>最近查询 · {mounted ? (feishuLastQuery || "—") : "—"}{feishuRows ? ` · ${feishuRows} 行` : ""}</small>
            </div>
            {feishuConnected === false && (
              <button
                type="button"
                className="relogin-trigger"
                onClick={startFeishuRelogin}
                disabled={reloginBusy}
                title="先重测授权，仍失效则打开扫码续期"
              >
                {reloginBusy ? "发起中…" : "一键续期"}
              </button>
            )}
          </div>
          <div className="status-row">
            <span className="signal polling" />
            <div className="feishu-poll">
              <strong>飞书状态刷新</strong>
              <select
                className="poll-select"
                aria-label="飞书状态刷新间隔"
                value={feishuPollSec}
                onChange={(event) => changeFeishuPoll(Number(event.target.value))}
              >
                {FEISHU_POLL_OPTIONS.map((seconds) => (
                  <option key={seconds} value={seconds}>
                    {seconds >= 60 ? `${seconds / 60} 分钟` : `${seconds} 秒`}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="status-row">
            <span className={`signal ${obsidianConnected == null ? "checking" : obsidianConnected ? "healthy" : "down"}`} />
            <div>
              <strong>{obsidianConnected == null ? "本地知识库检测中…" : obsidianConnected ? "本地知识库已连接" : "本地知识库未连接"}</strong>
              <small>{obsidianPath ? `Vault · ${obsidianPath}` : "Obsidian Vault 检测"}</small>
            </div>
          </div>
        </div>
      </aside>
      {relogin && (
        <div className="relogin-backdrop" role="dialog" aria-modal="true" aria-label="飞书授权续期">
          <div className="relogin-panel">
            <div className="relogin-head">
              <strong>飞书授权续期</strong>
              <button type="button" aria-label="关闭" onClick={() => setRelogin(null)}>×</button>
            </div>
            {relogin.state === "failed" ? (
              <>
                <p className="relogin-msg fail">{relogin.message || "授权未完成"}</p>
                <div className="relogin-actions">
                  <button type="button" onClick={startFeishuRelogin} disabled={reloginBusy}>重新发起</button>
                  <button type="button" className="ghost" onClick={() => setRelogin(null)}>关闭</button>
                </div>
              </>
            ) : relogin.qrcode_b64 ? (
              <>
                <p className="relogin-msg">用飞书扫码，或点下方链接完成授权</p>
                <img
                  className="relogin-qr"
                  src={`data:image/png;base64,${relogin.qrcode_b64}`}
                  alt="飞书授权二维码"
                />
                {relogin.user_code && <p className="relogin-code">用户码 · {relogin.user_code}</p>}
                <a className="relogin-link" href={relogin.url} target="_blank" rel="noreferrer">
                  在浏览器中打开授权页
                </a>
                <p className="relogin-hint">链接 10 分钟内有效，授权完成后本面板会自动关闭。</p>
              </>
            ) : (
              <p className="relogin-msg">正在获取授权链接…</p>
            )}
          </div>
        </div>
      )}
      <main>
        <header className="topbar">
          <div><span className="eyebrow">LOCAL · USER INITIATED</span><h1>{titles[tab]}</h1></div>
          {discoveryChip && discovery && (
            <button
              type="button"
              className="discovery-chip"
              title="重新展开飞书发现的链接"
              onClick={() => {
                window.localStorage.removeItem(DISCOVERY_DISMISS_KEY);
                setDiscoveryDismissed("");
              }}
            >
              <i />
              飞书 {discovery.resolvable} 条待解析
            </button>
          )}
        </header>
        {notice && <div className="toast ok" role="status" aria-live="polite">{notice}<button type="button" aria-label="关闭成功提示" onClick={() => setNotice("")}>×</button></div>}
        {error && <div className="toast error" role="alert">{error}<button type="button" aria-label="关闭错误提示" onClick={() => setError("")}>×</button></div>}
        {discoveryVisible && discovery && (
          <section className={`discovery-banner${discoveryOpen ? " open" : ""}`}>
            <div className="discovery-head">
              <div className="discovery-copy">
                <span className="kicker">飞书同步 · 已补齐</span>
                <strong>飞书发现 {discovery.total} 条新链接，已补齐信息</strong>
                <p>
                  {discovery.unresolved
                    ? `${discovery.resolvable} 条可解析；${discovery.unresolved} 条未取到信息，链接可能已删除或需要登录`
                    : `${discovery.resolvable} 条均可解析，确认后开始下载与知识化`}
                </p>
              </div>
              <div className="discovery-actions">
                <button
                  className="primary"
                  disabled={!discoveryPicked.size || discoveryBusy}
                  onClick={approveDiscovery}
                >
                  {discoveryBusy ? "提交中…" : `解析这 ${discoveryPicked.size} 条`}
                </button>
                <button
                  className="secondary"
                  disabled={discovery.total < 3}
                  title={discovery.total < 3 ? "少于 3 条时无需展开，直接解析即可" : undefined}
                  onClick={() => setDiscoveryOpen((open) => !open)}
                >
                  {discoveryOpen ? "收起清单" : "查看清单"}
                </button>
                <button
                  className="link"
                  onClick={() => {
                    window.localStorage.setItem(DISCOVERY_DISMISS_KEY, discoverySignatureText);
                    setDiscoveryDismissed(discoverySignatureText);
                    setDiscoveryOpen(false);
                  }}
                >
                  稍后
                </button>
              </div>
            </div>
            {discoveryOpen && (
              <div className="discovery-list">
                <div className="discovery-list-head">
                  <span>
                    清单 · {discovery.total} 条
                    {discovery.truncated ? `（另有 ${discovery.truncated} 条未列出）` : ""}
                  </span>
                  <span>已选 {discoveryPicked.size} 条</span>
                </div>
                {discovery.items.map((item) => {
                  const selectable = item.state === "ready" && item.item_id !== null;
                  const checked = selectable && discoveryPicked.has(item.item_id as number);
                  return (
                    <div
                      key={item.record_id}
                      className={`discovery-item${selectable ? "" : " off"}`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={!selectable}
                        aria-label={`选择 ${item.title || item.link}`}
                        onChange={() => {
                          if (selectable) toggleDiscoveryItem(item.item_id as number);
                        }}
                      />
                      <span className="discovery-cover">
                        {item.kind === "image" ? "IMG" : "VID"}
                      </span>
                      <span className="discovery-meta">
                        <b>{item.title || item.link}</b>
                        <small>
                          {item.state === "ready"
                            ? [
                                item.author_name,
                                item.platform === "douyin" ? "抖音" : item.platform,
                                item.kind === "image" ? "图文" : "视频",
                              ]
                                .filter(Boolean)
                                .join(" · ")
                            : item.reason}
                        </small>
                      </span>
                      <span className={`pill ${selectable ? "ready" : "unresolved"}`}>
                        {selectable ? "可解析" : "未取到信息"}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        )}
        {!!activeJobs.length && (
          <div className="job-queue">
            {activeJobs.map((job, index) => (
              <JobProgress
                key={job.id}
                job={job}
                queuePosition={job.state === "queued" ? index + 1 : undefined}
                onCancel={() => perform(`cancel-job-${job.id}`, () => api.cancelJob(job.id), job.state === "queued" ? "排队任务已取消" : "正在安全停止任务")}
              />
            ))}
          </div>
        )}
        <ImportWorkspace
          hidden={tab !== "import"}
          usage={usage}
          summary={summary}
          collections={collections}
          jobs={jobs}
          onLocalBatchReady={acceptLocalBatch}
          onPlatformBatchReady={acceptPlatformBatch}
          activeBatchId={batch?.id ?? null}
          onRefreshJobs={async () => { await loadJobs(); }}
          perform={perform}
          notify={(message) => setNotice(message)}
        />
        <ParseWorkspace
          hidden={tab !== "parse"}
          batch={batch}
          retainedBatches={retainedBatches}
          selected={selectedItems}
          setSelected={setSelectedItems}
          itemCollections={itemCollections}
          setItemCollections={setItemCollections}
          busy={busy}
          onCancel={cancelBatch}
          onConfirm={confirmBatch}
          onDelete={removePreviewItem}
          onEdit={editPreviewItem}
          onRetryFailedImports={retryFailedImports}
          settings={settings}
          collections={collections}
        />
        {tab === "library" && (
          <Library
            key={`${libraryState}:${collectionId ?? "all"}:${platform}:${kind}:${author}:${source}`}
            state={libraryState}
            setState={(value) => {
              if (value === libraryState) return;
              libraryRequestRef.current += 1;
              setWorksPage(EMPTY_WORKS);
              setLibraryState(value);
            }}
            collectionId={collectionId}
            setCollectionId={(value) => {
              if (value === collectionId) return;
              libraryRequestRef.current += 1;
              setWorksPage(EMPTY_WORKS);
              setCollectionId(value);
            }}
            platform={platform}
            setPlatform={(value) => {
              if (value === platform) return;
              libraryRequestRef.current += 1;
              setWorksPage(EMPTY_WORKS);
              setPlatform(value);
            }}
            kind={kind}
            setKind={(value) => {
              if (value === kind) return;
              libraryRequestRef.current += 1;
              setWorksPage(EMPTY_WORKS);
              setKind(value);
            }}
            author={author}
            setAuthor={(value) => {
              if (value === author) return;
              libraryRequestRef.current += 1;
              setWorksPage(EMPTY_WORKS);
              setAuthor(value);
            }}
            source={source}
            setSource={(value) => {
              if (value === source) return;
              libraryRequestRef.current += 1;
              setWorksPage(EMPTY_WORKS);
              setSource(value);
            }}
            collections={collections}
            summary={summary}
            page={worksPage}
            reload={() => loadLibrary(false)}
            loadMore={() => loadLibrary(true)}
            loading={libraryLoading}
            perform={perform}
            globalSummaryPrompt={settings?.summary_prompt || ""}
            rightsAttested={rightsAttested}
            setRightsAttested={changeRightsAttestation}
          />
        )}
        {tab === "settings" && (
          settings ? (
            <Settings
              settings={settings}
              usage={usage}
              health={health}
              onHealthChange={setHealth}
              busy={busy}
              perform={perform}
            />
          ) : (
            <div className="stack">
              <ThemePicker />
              <section className="card">
                <p className="muted">本机后端暂未连接；主题仍可在当前设备上切换。</p>
              </section>
            </div>
          )
        )}
      </main>
    </div>
  );
}

function JobProgress({ job, queuePosition, onCancel }: { job: Job; queuePosition?: number; onCancel: () => void }) {
  const completed = Number(job.progress.items_completed || job.progress.completed || 0);
  const total = job.total_items || Number(job.progress.items_total || 0);
  const percent = total ? Math.min(100, (completed / total) * 100) : 0;
  return (
    <section className="job-progress">
      <div className="progress-copy">
        <span className="kicker">{jobLabel(job)}{queuePosition ? ` · 队列第 ${queuePosition} 位` : ""}</span>
        <strong>{job.message}</strong>
        <small>{completed}/{total || "?"}</small>
      </div>
      <div className="progress-track" role="progressbar" aria-label={job.message} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)}><i style={{ width: `${percent}%` }} /></div>
      <span className={`pill ${job.state}`}>{stateLabel(job.state)}</span>
      <button className="danger" disabled={job.state === "cancelling"} onClick={onCancel}>安全停止</button>
    </section>
  );
}
