"use client";

import { useState } from "react";

import { api } from "../lib/api";
import type {
  Collection,
  ImportBatch,
  ImportItem,
  ImportItemUpdate,
  RuntimeSettings,
} from "../lib/api";
import { localAssetUrl } from "../lib/assets";

function itemStatus(item: ImportItem) {
  if (item.status === "duplicate") {
    return item.error_code === "duplicate_input" ? "预检重复" : "已在知识库";
  }
  return (
    {
      pending: "等待中",
      queued: "排队中",
      resolving: "解析中",
      ready: "可以确认",
      needs_local_file: "需要补件",
      confirmed: "已加入待入库",
      failed: "解析失败",
      blocked: "因风控停止",
      cancelled: "已中断",
    }[item.status] || item.status
  );
}

function statusTag(item: ImportItem) {
  const map: Record<string, string> = {
    ready: "t-ok",
    needs_local_file: "t-sup",
    duplicate: "t-v1",
    failed: "t-p0",
    blocked: "t-p0",
    cancelled: "t-v2",
    confirmed: "t-ok",
    queued: "t-v1",
    resolving: "t-v1",
    pending: "t-v1",
  };
  return map[item.status] || "t-v1";
}

const PARSE_STAGES = ["预检", "音视分离", "模型总结", "标签分类"];

function stageDone(status: string) {
  if (status === "queued") return 0;
  if (status === "resolving") return 1;
  if (status === "needs_local_file") return 2;
  if (status === "ready" || status === "confirmed") return 4;
  if (status === "failed" || status === "blocked" || status === "cancelled") return 1;
  return 0;
}

const PAGE_SIZE = 5;

const PARSING_STATES = new Set(["queued", "resolving", "pending"]);
const RETRYABLE_STATES = new Set(["failed", "cancelled", "blocked"]);

const STAT_GROUPS: Array<{
  key: string;
  label: string;
  match: (item: ImportItem) => boolean;
}> = [
  { key: "duplicate", label: "重复", match: (item) => item.status === "duplicate" },
  { key: "cancelled", label: "中断", match: (item) => item.status === "cancelled" || item.status === "blocked" },
  { key: "failed", label: "失败", match: (item) => item.status === "failed" },
  { key: "needs_local_file", label: "需补件", match: (item) => item.status === "needs_local_file" },
  { key: "resolving", label: "解析中", match: (item) => PARSING_STATES.has(item.status) },
  { key: "ready", label: "可确认", match: (item) => item.status === "ready" },
];

const STAT_COLOR: Record<string, string> = {
  duplicate: "#534AB7",
  cancelled: "#BA7517",
  failed: "#A32D2D",
  needs_local_file: "#185FA5",
  resolving: "#1D9E75",
  ready: "#639922",
};

export function ParseWorkspace({
  hidden,
  batch,
  retainedBatches,
  selected,
  setSelected,
  itemCollections,
  setItemCollections,
  busy,
  onCancel,
  onConfirm,
  onDelete,
  onEdit,
  onBatchDelete,
  onBatchRetry,
  settings,
  collections,
}: {
  hidden: boolean;
  batch: ImportBatch | null;
  retainedBatches: ImportBatch[];
  selected: Set<number>;
  setSelected: (value: Set<number>) => void;
  itemCollections: Map<number, number>;
  setItemCollections: (value: Map<number, number>) => void;
  busy: string;
  onCancel: () => void;
  onConfirm: (itemIds: number[]) => void;
  onDelete: (item: ImportItem) => void;
  onEdit: (item: ImportItem, changes: ImportItemUpdate) => void;
  onBatchDelete: (itemIds: number[]) => void;
  onBatchRetry: (itemIds: number[]) => void;
  settings: RuntimeSettings | null;
  collections: Collection[];
}) {
  const [page, setPage] = useState(0);
  const [activeStats, setActiveStats] = useState<Set<string>>(new Set());
  const active = batch && !["succeeded", "partial", "failed", "cancelled"].includes(batch.state);
  const manualCollectionId = collections.find((group) => group.key === "manual-import")?.id;

  const rawItems = [
    ...retainedBatches.flatMap((entry) => entry.items),
    ...(batch?.items || []),
  ].filter((item) => !(item.status === "duplicate" && item.error_code === "duplicate_input"));

  const displayItems = rawItems.filter(
    (item, index, items) =>
      items.findIndex((candidate) => candidate.id === item.id) === index,
  );

  const selectable = displayItems.filter((item) => item.status === "ready");
  const selectedVisible = selectable.filter((item) => selected.has(item.id));

  const totalPages = Math.max(1, Math.ceil(displayItems.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pagedItems = displayItems.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  const pageSelectable = pagedItems.filter((item) => !PARSING_STATES.has(item.status));
  const pageSelected = pageSelectable.filter((item) => selected.has(item.id));
  const allPageSelected = pageSelectable.length > 0 && pageSelected.length === pageSelectable.length;
  const somePageSelected = pageSelected.length > 0 && !allPageSelected;

  const selectedItems = displayItems.filter((item) => selected.has(item.id));
  const deletableSelected = selectedItems.filter((item) => !PARSING_STATES.has(item.status));
  const retryableSelected = selectedItems.filter((item) => RETRYABLE_STATES.has(item.status));

  function toggle(id: number, checked: boolean) {
    const next = new Set(selected);
    if (checked) next.add(id); else next.delete(id);
    setSelected(next);
  }

  function toggleSelectAllPage() {
    const next = new Set(selected);
    if (allPageSelected) pageSelectable.forEach((item) => next.delete(item.id));
    else pageSelectable.forEach((item) => next.add(item.id));
    setSelected(next);
  }

  function toggleStat(group: typeof STAT_GROUPS[number]) {
    const ids = displayItems.filter(group.match).map((item) => item.id);
    const next = new Set(selected);
    if (activeStats.has(group.key)) {
      ids.forEach((id) => next.delete(id));
      const updated = new Set(activeStats);
      updated.delete(group.key);
      setActiveStats(updated);
    } else {
      const toAdd = group.key === "duplicate" && ids.length > 1 ? ids.slice(1) : ids;
      toAdd.forEach((id) => next.add(id));
      const updated = new Set(activeStats);
      updated.add(group.key);
      setActiveStats(updated);
    }
    setSelected(next);
  }

  return (
    <section className="parse-workspace stack" hidden={hidden}>
      <div className="card sup parse-card">
        <div className="parse-header">
          <div>
            <div className="parse-title">解析进度</div>
            <div className="parse-sub">
              {batch ? `${batch.progress.completed || 0}/${batch.total_items} · ${batch.total_items ? Math.round(((batch.progress.completed || 0) / batch.total_items) * 100) : 0}%` : "暂无进行中的解析"}
            </div>
          </div>
          <div className="parse-header-actions">
            {!!selectable.length && (
              <button
                className="btn primary parse-confirm-btn"
                disabled={!selectedVisible.length || Boolean(busy)}
                onClick={() => onConfirm(selectedVisible.map((item) => item.id))}
              >
                确认加入待入库（{selectedVisible.length}）
              </button>
            )}
          </div>
        </div>

        <div className="parse-stats">
          {STAT_GROUPS.map((group) => {
            const count = displayItems.filter(group.match).length;
            const isActive = activeStats.has(group.key);
            return (
              <button
                type="button"
                key={group.key}
                className={`parse-stat${isActive ? " active" : ""}`}
                style={isActive ? { background: STAT_COLOR[group.key], borderColor: STAT_COLOR[group.key] } : undefined}
                disabled={count === 0}
                onClick={() => toggleStat(group)}
              >
                <span className="parse-stat-l" style={isActive ? { color: "#fff" } : undefined}>{group.label}</span>
                <span className="parse-stat-n" style={isActive ? { color: "#fff" } : undefined}>{count}</span>
              </button>
            );
          })}
        </div>

        {!!displayItems.length && (
          <>
            <div className="parse-selectall-row">
              <div
                className={`parse-checkbox${allPageSelected ? " on" : ""}${somePageSelected ? " partial" : ""}`}
                role="checkbox"
                aria-checked={allPageSelected ? "true" : somePageSelected ? "mixed" : "false"}
                tabIndex={0}
                onClick={toggleSelectAllPage}
                onKeyDown={(event) => {
                  if (event.key === " " || event.key === "Enter") toggleSelectAllPage();
                }}
              />
              <span className="parse-selectall-label">全选本页</span>
              <span className="parse-selectall-hint">仅当前页</span>
            </div>

            <div className="parse-results">
              {pagedItems.map((item) => {
                const done = stageDone(item.status);
                const fill = Math.round((done / (PARSE_STAGES.length - 1)) * 100);
                const fullTitle = item.title || item.input_url || "";
                const displayTitle = fullTitle.length > 20 ? `${Array.from(fullTitle).slice(0, 20).join("")}…` : fullTitle;
                const selectableItem = !PARSING_STATES.has(item.status);
                return (
                  <div className="parse-result" data-id={item.id} data-state={item.status} key={item.id}>
                    <div className="parse-result-top">
                      {selectableItem ? (
                        <div
                          className={`parse-checkbox ${selected.has(item.id) ? "on" : ""}`}
                          role="checkbox"
                          aria-checked={selected.has(item.id)}
                          tabIndex={0}
                          onClick={() => toggle(item.id, !selected.has(item.id))}
                          onKeyDown={(event) => {
                            if (event.key === " " || event.key === "Enter") toggle(item.id, !selected.has(item.id));
                          }}
                        />
                      ) : (
                        <span className="parse-checkbox" style={{ cursor: "default", opacity: 0.4 }} />
                      )}
                      <div className="parse-result-title" title={fullTitle}>
                        {displayTitle}
                      </div>
                      {PARSING_STATES.has(item.status) ? (
                        <span className="parse-status-anim">
                          <span className="parse-spin" />
                          <span className="parse-status-txt">{itemStatus(item)}</span>
                        </span>
                      ) : (
                        <span className={`tag ${statusTag(item)}`}>{itemStatus(item)}</span>
                      )}
                    </div>

                    <div className="parse-timeline">
                      <div className="parse-timeline-track"><div className="parse-timeline-fill" style={{ width: `${fill}%` }} /></div>
                      <div className="parse-tl-steps">
                        {PARSE_STAGES.map((stage, index) => {
                          const isDone = index < done;
                          const isCur = index === done && item.status === "resolving";
                          return (
                            <div className={`parse-tl-step ${isDone ? "done" : isCur ? "cur" : ""}`} key={stage}>
                              <div className={`parse-tl-circle ${isDone ? "" : "pending"}`}>{isDone ? "✓" : ""}</div>
                              <div className="parse-tl-label">{stage}</div>
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    <div className="parse-result-meta">
                      <span className="parse-source">{item.platform === "local" ? "本地" : item.kind === "image" ? "图文" : "网络"}</span>
                      <div className="parse-meta-lines">
                        <div>{item.author_name || "作者待识别"}{item.duration_seconds > 0 ? ` · ${Math.round(item.duration_seconds)} 秒` : ""}</div>
                        <div>{item.cover_url && localAssetUrl(item.cover_url) ? "已有封面" : item.local_asset_count > 0 ? `本地素材 ${item.local_asset_count} 个` : "等待素材"}</div>
                      </div>
                    </div>

                    {item.status === "ready" && (
                      <label className="result-collection" style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--muted)", fontSize: 12 }}>
                        <span>加入收藏夹</span>
                        <select
                          className="parse-select"
                          style={{ maxWidth: 320 }}
                          value={itemCollections.get(item.id) ?? manualCollectionId ?? ""}
                          onChange={(event) => {
                            const next = new Map(itemCollections);
                            const targetCollectionId = event.target.value ? Number(event.target.value) : null;
                            if (targetCollectionId != null) next.set(item.id, targetCollectionId);
                            else next.delete(item.id);
                            setItemCollections(next);
                            if (item.platform === "local") onEdit(item, { target_collection_id: targetCollectionId });
                          }}
                        >
                          {collections.map((group) => (
                            <option key={group.id} value={group.id}>{group.title}{group.summary_prompt ? " · 专属提示词" : ""}</option>
                          ))}
                        </select>
                      </label>
                    )}

                    <div className="parse-actions">
                      {selectableItem && (
                        <button className="parse-del" onClick={() => onDelete(item)}>删除预检结果</button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {totalPages > 1 && (
              <div className="parse-pagination">
                <button className="parse-page-btn" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>‹</button>
                {Array.from({ length: totalPages }, (_, index) => (
                  <button
                    key={index}
                    className={`parse-page-btn ${index === safePage ? "on" : ""}`}
                    onClick={() => setPage(index)}
                  >
                    {index + 1}
                  </button>
                ))}
                <button className="parse-page-btn" disabled={safePage >= totalPages - 1} onClick={() => setPage(safePage + 1)}>›</button>
              </div>
            )}

            {selected.size > 0 && (
              <div className="parse-batch-bar">
                <button
                  type="button"
                  className="parse-batch-del"
                  disabled={Boolean(busy) || !deletableSelected.length}
                  onClick={() => onBatchDelete(deletableSelected.map((item) => item.id))}
                >
                  删除所选项（{deletableSelected.length}）
                </button>
                <button
                  type="button"
                  className="parse-batch-retry"
                  disabled={Boolean(busy) || !retryableSelected.length}
                  onClick={() => onBatchRetry(retryableSelected.map((item) => item.id))}
                >
                  重试所选项（{retryableSelected.length}）
                </button>
              </div>
            )}
          </>
        )}

        {!displayItems.length && (
          <div className="task-empty" style={{ minHeight: 120 }}>暂无预检结果，请先在「导入」页提交视频</div>
        )}

        {active && (
          <div className="parse-actions" style={{ justifyContent: "flex-start", marginTop: 4 }}>
            <button className="parse-pause" disabled={Boolean(busy)} onClick={onCancel}>
              {batch?.state === "cancelling" ? "正在安全停止" : "暂停该任务"}
            </button>
          </div>
        )}
      </div>

      <ModelConfig settings={settings} />
    </section>
  );
}

function ModelConfig({ settings }: { settings: RuntimeSettings | null }) {
  const [processingModel, setProcessingModel] = useState<string>(settings?.processing_model || "");
  const [saving, setSaving] = useState(false);

  if (!settings) {
    return (
      <div className="card model-config">
        <div className="card-h"><div className="t">模型配置</div></div>
        <p className="muted">本机后端暂未连接，无法读取模型配置。</p>
      </div>
    );
  }

  async function save() {
    if (!processingModel) return;
    setSaving(true);
    try {
      await api.saveSettings({ processing_model: processingModel });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card model-config">
      <div className="card-h"><div className="t">模型配置</div></div>
      <div className="model-desc">免费额度优先使用，超出部分按萃点计费。</div>
      <div className="model-split">
        <div className="model-col">
          <div className="model-col-header">
            <div>
              <div className="model-col-h">对话模型</div>
              <div className="model-col-note">用于日常问答与视频资料整理的大语言模型。</div>
            </div>
          </div>
          <select
            className="parse-select"
            value={processingModel}
            onChange={(event) => setProcessingModel(event.target.value)}
          >
            {settings.processing_model_options.map((model) => (
              <option key={model} value={model}>{model}</option>
            ))}
          </select>
          <div className="model-form-actions">
            <button className="btn primary" disabled={saving || !processingModel} onClick={save}>
              {saving ? "保存中…" : "保存并设为当前"}
            </button>
          </div>
        </div>
        <div className="model-col">
          <div className="model-col-header">
            <div>
              <div className="model-col-h">音频模型</div>
              <div className="model-col-note">用于语音转文字 / ASR 的专用模型，与对话模型分开管理。</div>
            </div>
          </div>
          <div className="model-item current">
            <div className="model-item-icon">🎙️</div>
            <div className="model-item-main">
              <div className="model-item-title">语音转写（固定）</div>
              <div className="model-item-sub">{settings.asr_model}</div>
            </div>
          </div>
          <p className="muted" style={{ marginTop: 10 }}>画面识别：{settings.ocr_model} · 向量检索：{settings.embedding_model}</p>
        </div>
      </div>
    </div>
  );
}
