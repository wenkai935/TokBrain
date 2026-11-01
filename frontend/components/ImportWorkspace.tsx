"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import type {
  Collection,
  ImportBatch,
  Job,
  LibrarySummary,
  PlatformId,
  Usage,
  Work,
} from "../lib/api";
import { reason } from "../lib/errors";
import { rememberLocalImportBatch } from "../lib/importBatchStorage";
import type { PerformOperation } from "../lib/uiTypes";

const PLATFORMS: Array<{ key: PlatformId; label: string; icon: string; url: string }> = [
  { key: "douyin", label: "抖音", icon: "🎵", url: "https://www.douyin.com/" },
  { key: "bilibili", label: "B站", icon: "📺", url: "https://www.bilibili.com/" },
  { key: "xiaohongshu", label: "小红书", icon: "📕", url: "https://www.xiaohongshu.com/" },
];

const SCOPES = [
  { key: "fav", label: "收藏" },
  { key: "like", label: "喜欢" },
  { key: "work", label: "我的作品" },
  { key: "history", label: "观看历史" },
  { key: "fail", label: "读取失败" },
];

type CollectedVideo = {
  aweme_id: string;
  title: string | null;
  author: string | null;
  source_url: string;
  published_at: string | null;
  cover_url: string | null;
};

type CollectedGroup = {
  name: string;
  count: number;
  items: CollectedVideo[];
};

type CollectedData = {
  scope: string;
  count: number;
  groups: CollectedGroup[];
  items: CollectedVideo[];
};

const LOCAL_VIDEO_EXTENSIONS = new Set(["mp4", "mov", "mkv", "webm"]);
const LOCAL_VIDEO_MAX_BYTES = 1024 * 1024 * 1024;
const LOCAL_VIDEO_LIMIT = 10;

function readableBytes(bytes: number) {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function mediaIcon(type: string) {
  return type.startsWith("video/") ? "🎬" : type.startsWith("audio/") ? "🎵" : type.startsWith("image/") ? "🖼️" : "📦";
}

function localFileError(file: File) {
  const extension = file.name.split(".").pop()?.toLowerCase() || "";
  if (!LOCAL_VIDEO_EXTENSIONS.has(extension)) return "仅支持 MP4、MOV、MKV、WebM 视频";
  if (file.size <= 0) return "文件为空";
  if (file.size > LOCAL_VIDEO_MAX_BYTES) return "文件超过 1 GB 上限";
  return "";
}

function jobStateLabel(state: string) {
  return (
    {
      queued: "排队中",
      running: "解析中",
      automating: "自动确认中",
      cancelling: "安全停止中",
      cancelled: "已中断",
      succeeded: "已完成",
      partial: "部分完成",
      failed: "失败",
      await_fix: "待补件",
    }[state] || state
  );
}

function jobTaskState(state: string) {
  if (state === "succeeded" || state === "partial") return "done";
  if (state === "await_fix") return "await";
  return state;
}

function jobLabel(job: Job) {
  // 颗粒度：每个 job = 单条视频（见 PRD 5.3 / 8.3）
  if (job.job_type === "link_preview") return "视频预检";
  if (job.job_type === "ingest") return "视频入库";
  if (job.job_type === "summarize") return "视频精华整理";
  return "视频导入";
}

export function ImportWorkspace({
  hidden,
  usage,
  summary,
  collections,
  jobs,
  onLocalBatchReady,
  onPlatformBatchReady,
  onRefreshJobs,
  perform,
  notify,
  activeBatchId,
}: {
  hidden: boolean;
  usage: Usage | null;
  summary: LibrarySummary;
  collections: Collection[];
  jobs: Job[];
  onLocalBatchReady: (batch: ImportBatch) => Promise<void>;
  onPlatformBatchReady: (batchId: string) => Promise<void>;
  onRefreshJobs: () => Promise<void>;
  perform: PerformOperation;
  notify: (message: string) => void;
  activeBatchId: string | null;
}) {
  return (
    <section className="import-workspace stack" hidden={hidden}>
      <ImportStats usage={usage} summary={summary} />
      <LocalImport
        collections={collections}
        onBatchReady={onLocalBatchReady}
        notify={notify}
      />
      <NetworkImport
        notify={notify}
        onRefreshJobs={onRefreshJobs}
        onPlatformBatchReady={onPlatformBatchReady}
        activeBatchId={activeBatchId}
      />
      <ImportProgress jobs={jobs} onRefresh={onRefreshJobs} perform={perform} />
      <WorksLibrary />
    </section>
  );
}

function ImportStats({ usage, summary }: { usage: Usage | null; summary: LibrarySummary }) {
  return (
    <div className="card">
      <div className="card-h"><div className="t">数据统计</div></div>
      <div className="grid4">
        <div className="stat"><div className="k">处理量</div><div className="v">{usage?.daily_works_used || 0}</div></div>
        <div className="stat"><div className="k">AI 用量</div><div className="v">{(usage?.daily_llm_tokens_used || 0).toLocaleString()}</div></div>
        <div className="stat"><div className="k">已入库</div><div className="v">{summary.local_item_count.toLocaleString()}</div></div>
        <div className="stat"><div className="k">待入库</div><div className="v">{summary.candidate_count.toLocaleString()}</div></div>
      </div>
    </div>
  );
}

type LocalDraft = { id: string; file: File; url: string; error: string };

function LocalImport({
  collections,
  onBatchReady,
  notify,
}: {
  collections: Collection[];
  onBatchReady: (batch: ImportBatch) => Promise<void>;
  notify: (message: string) => void;
}) {
  const [drafts, setDrafts] = useState<LocalDraft[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    return () => drafts.forEach((draft) => URL.revokeObjectURL(draft.url));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function append(files: File[]) {
    setError("");
    if (!files.length) return;
    const remaining = Math.max(0, LOCAL_VIDEO_LIMIT - drafts.length);
    if (remaining === 0) {
      setError(`每批最多选择 ${LOCAL_VIDEO_LIMIT} 个视频`);
      return;
    }
    const accepted = files.slice(0, remaining).map((file): LocalDraft => ({
      id: crypto.randomUUID(),
      file,
      url: URL.createObjectURL(file),
      error: localFileError(file),
    }));
    setDrafts((current) => [...current, ...accepted]);
    if (files.length > remaining) setError(`每批最多 ${LOCAL_VIDEO_LIMIT} 个，本次仅加入前 ${remaining} 个`);
  }

  function remove(id: string) {
    setDrafts((current) => {
      const target = current.find((draft) => draft.id === id);
      if (target) URL.revokeObjectURL(target.url);
      return current.filter((draft) => draft.id !== id);
    });
  }

  const validCount = drafts.filter((draft) => !draft.error).length;

  async function confirmUpload() {
    if (!drafts.length) {
      notify("请先拖入或选择文件");
      return;
    }
    // 已移除「请先勾选授权声明」拦截：上传区不再有授权勾选，改由后端固定记录 rights_attested
    const valid = drafts.filter((draft) => !draft.error);
    if (!valid.length) return;
    setUploading(true);
    setError("");
    try {
      const manualCollectionId = collections.find((group) => group.key === "manual-import")?.id
        ?? collections[0]?.id
        ?? null;
      const created = await api.createLocalImportBatch({
        rights_attested: true, // 后端契约要求固定为 true；与 UI 勾选无关
        items: valid.map((draft) => ({
          client_item_id: draft.id,
          filename: draft.file.name,
          size_bytes: draft.file.size,
          ...(manualCollectionId != null ? { target_collection_id: manualCollectionId } : {}),
        })),
      });
      rememberLocalImportBatch(created.id);
      for (let index = 0; index < valid.length; index += 1) {
        const draft = valid[index];
        const item = created.items.find((entry) => entry.client_item_id === draft.id)
          ?? created.items.find((entry) => entry.ordinal === index + 1);
        if (!item) continue;
        await api.uploadLocalImportVideo(created.id, item.id, draft.file);
      }
      const refreshed = await api.importBatch(created.id);
      await onBatchReady(refreshed);

      // —— 上传成功、作品已发往解析队列后，清空本区域（仅成功路径执行）——
      drafts.forEach((draft) => URL.revokeObjectURL(draft.url)); // 释放预览 blob
      setDrafts([]);                                             // 清空已选文件列表
      setError("");                                              // 复位错误提示
      if (fileInputRef.current) fileInputRef.current.value = "";  // 允许再次选择同名文件
    } catch (value) {
      setError(reason(value, "本地导入失败")); // 失败时保留已选文件，便于重试
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="card">
      <div className="card-h"><div className="t">本地导入</div><span className="tag t-p0">P0</span></div>
      <div className="block">
        <div className="bt">📁 上传文件</div>
        <div
          className="dropzone"
          role="button"
          tabIndex={0}
          onClick={() => !uploading && fileInputRef.current?.click()}
          onKeyDown={(event) => {
            if ((event.key === "Enter" || event.key === " ") && !uploading) fileInputRef.current?.click();
          }}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            if (!uploading) append(Array.from(event.dataTransfer.files));
          }}
          style={{ opacity: uploading ? 0.6 : undefined }}
        >
          <strong>拖拽 / 点击上传区域（视频）</strong>
          <span>支持 mp4 / mov / mkv / webm，单文件不超过 1 GB</span>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".mp4,.mov,.mkv,.webm,video/mp4,video/quicktime,video/webm,video/x-matroska"
          hidden
          disabled={uploading}
          onChange={(event) => {
            append(Array.from(event.target.files || []));
            event.target.value = "";
          }}
        />
        {!!drafts.length && (
          <div className="items" style={{ marginTop: 10 }}>
            {drafts.map((draft) => (
              <div className="pv" key={draft.id}>
                {draft.file.type.startsWith("video/")
                  ? <video src={draft.url} muted preload="metadata" />
                  : draft.file.type.startsWith("image/")
                    ? <img src={draft.url} alt="" />
                    : <span className="ph-ic">{mediaIcon(draft.file.type)}</span>}
                <span className="nm" title={draft.file.name}>
                  {draft.file.name}
                  <span className="sz">{readableBytes(draft.file.size)}</span>
                  {draft.error && <span className="sz" style={{ color: "var(--danger)" }}>{draft.error}</span>}
                </span>
                <button className="rm" title="移除" onClick={() => remove(draft.id)} disabled={uploading}>×</button>
              </div>
            ))}
          </div>
        )}
        {/* 上传操作行：已移除「授权声明」勾选与提示文案，仅保留确认上传按钮 */}
        <div className="local-import-foot" style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", marginTop: 12, gap: 16 }}>
          <button className="btn primary" disabled={!validCount || uploading} onClick={confirmUpload}>
            {uploading ? "正在上传…" : `确认上传（${validCount}）`}
          </button>
        </div>
        {error && <p className="local-import-error" style={{ color: "var(--danger)", fontSize: 12, marginTop: 8 }}>{error}</p>}
      </div>
    </div>
  );
}

function NetworkImport({
  notify,
  onRefreshJobs,
  onPlatformBatchReady,
  activeBatchId,
}: {
  notify: (message: string) => void;
  onRefreshJobs: () => Promise<void>;
  onPlatformBatchReady: (batchId: string) => Promise<void>;
  activeBatchId: string | null;
}) {
  const [tab, setTab] = useState<PlatformId>("douyin");
  // 每个平台各自记忆当前子分类（v2 CUR_SCOPE[platform]）
  const [scopeByTab, setScopeByTab] = useState<Record<PlatformId, string>>({
    douyin: "fav",
    bilibili: "fav",
    xiaohongshu: "fav",
  });
  const scope = scopeByTab[tab];
  const cacheKey = `${tab}:${scope}`;
  const [collected, setCollected] = useState<Record<string, CollectedData>>({});
  const [loadingData, setLoadingData] = useState(false);
  // 勾选：platform:scope → aweme_id 集合（跨分组累计，真同步所选）
  const [selected, setSelected] = useState<Record<string, Set<string>>>({});
  // 当前选中分组索引（仅 fav 多分组时有意义）
  const [activeCat, setActiveCat] = useState<Record<string, number>>({});
  const [syncingCollect, setSyncingCollect] = useState(false);
  const [syncingSelected, setSyncingSelected] = useState(false);
  const [lastPull, setLastPull] = useState<number | null>(null);
  const [lastBatch, setLastBatch] = useState<{
    id: string;
    total_items: number;
    counts: Record<string, number>;
  } | null>(null);

  const active = PLATFORMS.find((item) => item.key === tab)!;
  const domReady = tab === "douyin";
  const data = collected[cacheKey];
  const groups = (data?.groups || []).filter((g) => g.items.length > 0);
  const hasSubcat = groups.length > 1; // 多分组（如收藏夹）→ 双栏；否则平铺
  const catIdx = Math.min(activeCat[cacheKey] ?? 0, Math.max(0, groups.length - 1));
  const visibleVideos = hasSubcat ? (groups[catIdx]?.items || []) : (data?.items || []);
  const selSet = selected[cacheKey] || new Set<string>();
  const selAll = selSet.size;
  const visibleIds = visibleVideos.map((v) => v.aweme_id);
  const visibleSel = visibleIds.filter((id) => selSet.has(id)).length;
  const allOn = visibleIds.length > 0 && visibleSel === visibleIds.length;
  const someOn = visibleSel > 0 && !allOn;

  useEffect(() => {
    let cancelled = false;
    api.lastPlatformBatch()
      .then((result) => { if (!cancelled) setLastBatch(result); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [lastPull]);

  // 切换平台/子分类时，拉取该分类已采集的数据用于展示与勾选
  useEffect(() => {
    if (scope === "fail" || !domReady) return; // 失败分类/其它平台无后端预览
    let cancelled = false;
    setLoadingData(true);
    api.collectedVideos(scope)
      .then((result) => {
        if (!cancelled) setCollected((current) => ({ ...current, [`${tab}:${scope}`]: result }));
      })
      .catch(() => undefined)
      .finally(() => { if (!cancelled) setLoadingData(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, scope]);

  function refreshCollected() {
    if (scope === "fail" || !domReady) return;
    return api.collectedVideos(scope)
      .then((result) => setCollected((current) => ({ ...current, [cacheKey]: result })))
      .catch(() => undefined);
  }

  function toggleVideo(awemeId: string) {
    setSelected((current) => {
      const next = new Set(current[cacheKey] || []);
      if (next.has(awemeId)) next.delete(awemeId);
      else next.add(awemeId);
      return { ...current, [cacheKey]: next };
    });
  }

  function toggleSelectAll() {
    if (!visibleIds.length) return;
    setSelected((current) => {
      const next = new Set(current[cacheKey] || []);
      if (allOn) visibleIds.forEach((id) => next.delete(id));
      else visibleIds.forEach((id) => next.add(id));
      return { ...current, [cacheKey]: next };
    });
  }

  function selectSubcat(groupIdx: number) {
    setActiveCat((current) => ({ ...current, [cacheKey]: groupIdx }));
  }

  async function syncNow() {
    if (scope === "fail" || !selAll) return;
    const ids = Array.from(selSet);
    setSyncingSelected(true);
    try {
      const result = await api.syncSelected(scope, ids);
      notify(result.message || `已将 ${result.count} 条加入导入进度`);
      setSelected((current) => {
        const rest = { ...current };
        delete rest[cacheKey];
        return rest;
      });
      await onRefreshJobs();
      if (result.batch_id) {
        try {
          await onPlatformBatchReady(result.batch_id);
        } catch {
          notify("已入库，但加载解析进度失败，请切换到「解析」页手动刷新");
        }
      }
    } catch (error) {
      notify(reason(error, "同步失败"));
    } finally {
      setSyncingSelected(false);
    }
  }

  function lastBatchSummary(counts: Record<string, number>) {
    const ready = counts.ready || 0;
    const needs = counts.needs_local_file || 0;
    const resolving = (counts.pending || 0) + (counts.resolving || 0) + (counts.queued || 0);
    const failed = counts.failed || 0;
    const parts = [
      ready ? `可确认 ${ready}` : "",
      needs ? `需补件 ${needs}` : "",
      resolving ? `解析中 ${resolving}` : "",
      failed ? `失败 ${failed}` : "",
    ].filter(Boolean);
    return parts.length ? parts.join(" · ") : "已全部处理";
  }

  async function collect() {
    if (!domReady || scope === "fail") return;
    setSyncingCollect(true);
    try {
      const { task_id } = await api.domCollect(scope);
      notify(`采集已启动（${SCOPES.find((s) => s.key === scope)?.label}），请在弹出的浏览器完成登录（首次）`);
      await pollCollect(task_id);
    } catch (error) {
      notify(reason(error, "采集失败"));
    } finally {
      setSyncingCollect(false);
    }
  }

  async function pollCollect(taskId: string) {
    const maxTries = 450; // 15 分钟 / 2 秒
    for (let i = 0; i < maxTries; i++) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      const st = await api.domCollectStatus(taskId);
      if (st.status === "done") {
        setLastPull(st.pulled_count);
        notify(st.message || `已采集 ${st.pulled_count} 条`);
        await refreshCollected();
        if (st.batch_id) {
          // 旧 auto_import 场景：采集即全量入库 → 直接跳解析页
          try {
            await onPlatformBatchReady(st.batch_id);
          } catch {
            notify("采集完成，但加载解析进度失败，请切换到「解析」页手动刷新");
          }
        }
        return;
      }
      if (st.status === "error") {
        notify(st.message || "采集失败");
        return;
      }
    }
    notify("采集超时，请确认浏览器登录状态后重试");
  }

  function openLogin() {
    window.open(active.url, "_blank", "noopener,noreferrer");
    notify(`已打开 ${active.label} 网页`);
  }

  function videoCards(list: CollectedVideo[]) {
    return list.map((video) => {
      const isSel = selSet.has(video.aweme_id);
      return (
        <div
          className={`vg-card ${isSel ? "sel" : ""}`}
          key={video.aweme_id}
          onClick={() => toggleVideo(video.aweme_id)}
          title={video.title || video.aweme_id}
        >
          <span className={`vg-check ${isSel ? "on" : ""}`} />
          <div className="vg-cover">
            {video.cover_url ? <img src={video.cover_url} alt="" loading="lazy" /> : <span>🎬</span>}
          </div>
          <div className="vg-title">{video.title || video.aweme_id}</div>
          <div className="vg-meta">
            <span className="vg-author">{video.author || "抖音"}</span>
            {video.published_at ? <span className="vg-time">{video.published_at}</span> : null}
          </div>
        </div>
      );
    });
  }

  return (
    <div className="card">
      <div className="card-h"><div className="t">网络视频</div><span className="tag t-p0">P0</span></div>
      <div className="note gray" style={{ marginBottom: 12, marginTop: 0 }}>
        采集 = 拉取所选来源到预览；勾选视频后点「同步当前 N 条」才入库
      </div>
      <div className="ntabs">
        {PLATFORMS.map((platform) => (
          <button
            key={platform.key}
            className={tab === platform.key ? "on" : ""}
            onClick={() => setTab(platform.key)}
          >
            {platform.label}
          </button>
        ))}
      </div>

      <div className="naccount">
        <span className="ph-ic" style={{ fontSize: 18 }}>{active.icon}</span>
        <div className="info">
          <div className="title">{active.label}账号</div>
          <div className="desc">
            {domReady
              ? "浏览器自动化采集（Playwright 弹窗登录）· 无需粘贴 Cookie"
              : "DOM 采集待接入 · 可先打开网页完成登录"}
          </div>
        </div>
        <span className={`status ${domReady ? "on" : ""}`}>{domReady ? "可采集" : "待接入"}</span>
        <button className="btn" style={{ fontSize: 11, padding: "5px 10px" }} onClick={openLogin}>
          打开网页
        </button>
      </div>

      <div className="scope-tags">
        {SCOPES.map((item) => (
          <button
            key={item.key}
            className={scope === item.key ? "on" : ""}
            onClick={() => setScopeByTab((current) => ({ ...current, [tab]: item.key }))}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="scope-body">
        <div className="subcol-head">
          <div className="subcol-head-l">
            <span className="scope-now">{SCOPES.find((item) => item.key === scope)?.label}</span>
            <span className="subcol-count">
              {scope === "fail"
                ? "失败项在解析页处理"
                : data
                  ? `共 ${data.count} 条`
                  : lastPull != null
                    ? `上次采集 ${lastPull} 条`
                    : "尚未采集"}
            </span>
          </div>
          <div className="subcol-head-r">
            {scope !== "fail" && domReady && (
              <button className="btn" style={{ fontSize: 11.5, padding: "5px 12px" }} onClick={collect} disabled={syncingCollect}>
                {syncingCollect ? "采集中…" : "采集"}
              </button>
            )}
            {visibleVideos.length > 0 && (
              <span className="chk-all" onClick={toggleSelectAll} role="button" aria-checked={allOn}>
                <span className={`box ${allOn ? "on" : ""} ${someOn ? "ind" : ""}`} />
                <span className="chk-label">全选</span>
              </span>
            )}
            <button
              className="btn primary"
              onClick={syncNow}
              disabled={syncingSelected || selAll === 0 || scope === "fail"}
            >
              {syncingSelected
                ? "正在入队…"
                : scope === "fail"
                  ? "失败项无需同步"
                  : `同步当前 ${selAll} 条`}
            </button>
          </div>
        </div>

        {loadingData && !data ? (
          <div className="vg-empty">加载中…</div>
        ) : visibleVideos.length > 0 ? (
          <>
            {hasSubcat && (
              <div className="scope-tags sub-tabs" role="group" aria-label="收藏夹分类">
                {groups.map((group, groupIndex) => (
                  <button
                    key={group.name}
                    className={groupIndex === catIdx ? "on" : ""}
                    onClick={() => selectSubcat(groupIndex)}
                    title={group.name}
                  >
                    <span>{group.name.split("\n")[0] || group.name}</span>
                    <span className="scat-n">{group.count}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="subcol-videos">
              {videoCards(hasSubcat ? groups[catIdx]?.items ?? [] : visibleVideos)}
            </div>
          </>
        ) : (
          <div className="vg-empty" style={{ border: "1px dashed var(--line)", borderRadius: 8 }}>
            {scope === "fail"
              ? "「读取失败」是采集结果分类：解析失败的视频会在此显示，可在解析页补件重试"
              : domReady
                ? `「${SCOPES.find((s) => s.key === scope)?.label}」暂无数据：点「采集」抓取，勾选后点「同步」入库`
                : `${active.label} DOM 采集待接入，请先打开网页登录账号`}
          </div>
        )}

        {lastBatch && domReady && lastBatch.id !== activeBatchId && (
          <div className="note gray" style={{ marginTop: 12, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <span>上次采集批次：{lastBatch.total_items} 条（{lastBatchSummary(lastBatch.counts)}）</span>
            <button className="btn" onClick={() => onPlatformBatchReady(lastBatch.id)}>查看解析进度</button>
          </div>
        )}
      </div>
    </div>
  );
}

const TERMINAL_JOB_STATES = new Set(["succeeded", "partial", "failed", "cancelled", "await_fix"]);

function WorksLibrary() {
  const [works, setWorks] = useState<Work[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      // 查询 works 表当前入库数据（默认展示已入库 in_library）
      const page = await api.works("in_library");
      setWorks(page.items || []);
      setTotal(page.total || 0);
    } catch (value) {
      setError(reason(value, "查询作品库失败"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    api.works("in_library")
      .then((page) => { if (!cancelled) { setWorks(page.items || []); setTotal(page.total || 0); } })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="card sup">
      <div className="card-h">
        <div className="t">作品库 · works 表</div>
        <span className="tag t-sup">查询</span>
        <div className="sup-actions">
          <span className="subcol-count">{total} 条已入库</span>
          <button className={`icon-btn ${loading ? "spin" : ""}`} title="刷新" onClick={load}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v6h-6" />
            </svg>
          </button>
        </div>
      </div>
      {error && <div className="note" style={{ marginTop: 8 }}>{error}</div>}
      {works.length ? (
        <div className="task-list">
          {works.slice(0, 20).map((w) => (
            <div className="task" key={w.id ?? w.platform_work_id} data-status="">
              {w.cover_url ? (
                <img className="wk-cover" src={w.cover_url} alt="" />
              ) : (
                <span className="wk-cover ph">🎬</span>
              )}
              <span className="wk-info">
                <span className="wk-title" title={w.title || w.platform_work_id}>
                  {w.title || w.platform_work_id}
                </span>
                <span className="wk-meta">
                  {w.author_name ? `${w.author_name} · ` : ""}
                  {w.import_source || w.kind || "抖音"}
                </span>
              </span>
              <span className={`tstat ${w.library_state === "in_library" ? "done" : ""}`}>
                {w.library_state}
              </span>
            </div>
          ))}
          {works.length > 20 && (
            <div className="note gray" style={{ marginTop: 6 }}>
              仅展示前 20 条，共 {total} 条（完整数据见「库」页）
            </div>
          )}
        </div>
      ) : (
        <div className="task-empty">{loading ? "查询中…" : "works 表暂无已入库数据"}</div>
      )}
    </div>
  );
}

function ImportProgress({
  jobs,
  onRefresh,
  perform,
}: {
  jobs: Job[];
  onRefresh: () => Promise<void>;
  perform: PerformOperation;
}) {
  const [showHelp, setShowHelp] = useState(false);
  const [spinning, setSpinning] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [removingIds, setRemovingIds] = useState<Set<string>>(new Set());
  const activeCount = jobs.filter((job) => !TERMINAL_JOB_STATES.has(job.state)).length;
  const [collapsed, setCollapsed] = useState(activeCount === 0);

  async function refresh() {
    setSpinning(true);
    try {
      await onRefresh();
    } finally {
      window.setTimeout(() => setSpinning(false), 600);
    }
  }

  useEffect(() => {
    if (!showHelp) return;
    const onDocClick = () => setShowHelp(false);
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [showHelp]);

  const visibleJobs = jobs.filter((job) => !dismissed.has(job.id));
  const hasFailed = jobs.some((job) => job.state === "failed");
  const completedCount = jobs.filter((job) => TERMINAL_JOB_STATES.has(job.state)).length;

  function clearCompleted() {
    setDismissed(new Set(jobs.filter((job) => TERMINAL_JOB_STATES.has(job.state)).map((job) => job.id)));
  }

  function dismissJob(id: string) {
    if (removingIds.has(id)) return;
    setRemovingIds((current) => new Set([...current, id]));
    window.setTimeout(() => {
      setDismissed((current) => new Set([...current, id]));
      setRemovingIds((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    }, 250);
  }

  return (
    <div className="card sup">
      <div className="card-h" onClick={() => setCollapsed((v) => !v)} role="button" aria-expanded={!collapsed} title="点击展开/收起">
        <span className="chev">{collapsed ? "▸" : "▾"}</span>
        <div className="t">导入进度</div>
        {activeCount > 0 && <span className="n">{activeCount} 进行中</span>}
        <span className="tag t-sup">补充</span>
        <div className="sup-actions" onClick={(e) => e.stopPropagation()}>
          {completedCount > 0 && (
            <button
              className="text-btn"
              title="隐藏已完成的任务"
              onClick={clearCompleted}
            >
              清除已完成（{completedCount}）
            </button>
          )}
          <span className="ip-info-wrap">
            <button
              className="icon-btn"
              title="任务状态说明"
              onClick={(e) => {
                e.stopPropagation();
                setShowHelp((v) => !v);
              }}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9" /><circle cx="12" cy="7.5" r="1.3" fill="currentColor" stroke="none" /><line x1="12" y1="11" x2="12" y2="16" />
              </svg>
            </button>
            {showHelp && (
              <div className="ip-pop" role="dialog" aria-label="任务状态图例" onClick={(e) => e.stopPropagation()}>
                <div className="ip-pop-title">任务状态图例</div>
                {[["排队中", "#93C5FD"], ["解析中", "#6EE7B7"], ["已完成", "#4be3a0"], ["失败", "#F87171"]].map(([label, color]) => (
                  <div className="ip-pop-row" key={label}>
                    <span className="ip-dot" style={{ background: color }} />
                    <span>{label}</span>
                  </div>
                ))}
                <span className="ip-pop-arrow" />
              </div>
            )}
          </span>
          <button className={`icon-btn ${spinning ? "spin" : ""}`} title="刷新" onClick={refresh}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v6h-6" />
            </svg>
          </button>
        </div>
      </div>
      {!collapsed && (
        <>
      {visibleJobs.length ? (
        <div
          className="task-list"
          style={{
            gridAutoFlow: "column",
            gridTemplateRows: `repeat(${Math.max(1, Math.ceil(visibleJobs.length / 2))}, auto)`,
          }}
        >
          {visibleJobs.map((job) => {
            const completed = Number(job.progress.items_completed || job.progress.completed || 0);
            const total = job.total_items || Number(job.progress.items_total || 0);
            const percent = total ? Math.min(100, (completed / total) * 100) : 0;
            const terminal = TERMINAL_JOB_STATES.has(job.state);
            return (
              <div className={`task${removingIds.has(job.id) ? " removing" : ""}`} data-status={jobTaskState(job.state)} key={job.id}>
                <span className="tdot" />
                <span className="tnm" title={job.message}>{jobLabel(job)} · {job.message}</span>
                <div className="tbar"><div className="tbar-fill" style={{ width: `${percent}%` }} /></div>
                <span className={`tstat ${job.state === "succeeded" || job.state === "partial" ? "done" : job.state === "failed" ? "failed" : ""}`}>
                  {jobStateLabel(job.state)}
                </span>
                {job.state === "failed" && (
                  <button className="tretry" onClick={() => perform("retry-failed-imports", () => api.retryFailedImports(), "已重试全部失败导入")}>
                    重试
                  </button>
                )}
                {terminal && (
                  <button className="tdismiss" title="从列表移除" onClick={() => dismissJob(job.id)}>×</button>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="task-empty">暂无导入任务</div>
      )}
      {dismissed.size > 0 && (
        <button className="text-btn ghost" style={{ marginTop: 8 }} onClick={() => setDismissed(new Set())}>
          显示已隐藏的 {dismissed.size} 条任务
        </button>
      )}
      {hasFailed && (
        <div className="note" style={{ marginTop: 10 }}>
          导入后触发：启动前端查询 → 开始轮询 → 进入解析流程。失败条目可在解析页补件后重试。
        </div>
      )}
        </>)}
    </div>
  );
}
