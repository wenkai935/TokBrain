"use client";

import type { FormEvent } from "react";
import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type {
  Health,
  ModelProfile,
  ModelProfilePayload,
  ModelProfilesView,
  ModelProviderType,
  RuntimeSettings,
  RuntimeSettingsUpdate,
  Usage,
} from "../lib/api";
import { reason } from "../lib/errors";
import type { PerformOperation } from "../lib/uiTypes";
import { useTheme } from "../themes/ThemeProvider";
import type { ThemeDefinition } from "../themes/registry";
import { CookieVault } from "./CookieVault";

export function ThemePicker() {
  const { theme, themes, setTheme, backgroundIntensity, setBackgroundIntensity } = useTheme();
  return (
    <section className="card theme-picker">
      <div className="section-head">
        <div><span className="kicker">界面皮肤</span><h2>选择主题</h2></div>
        <span className="theme-current"><b>{theme.mark}</b>{theme.name}</span>
      </div>
      <div className="theme-grid" role="radiogroup" aria-label="界面主题">
        {themes.map((item) => (
          <button
            type="button"
            role="radio"
            aria-checked={item.id === theme.id}
            className={`theme-card ${item.id === theme.id ? "selected" : ""}`}
            key={item.id}
            onClick={() => setTheme(item.id)}
            style={themePreviewStyle(item)}
          >
            <span className="theme-preview"><span className="theme-badge">{item.mark}</span></span>
            <span className="theme-card-copy"><strong>{item.name}</strong><small>{item.description}</small></span>
          </button>
        ))}
      </div>
      <label className="background-intensity">
        <span><strong>背景显现度</strong><small>调整主题背景图的可见程度</small></span>
        <input aria-label="背景显现度" type="range" min="0" max="100" value={backgroundIntensity} onChange={(event) => setBackgroundIntensity(Number(event.target.value))} />
        <output>{backgroundIntensity}%</output>
      </label>
    </section>
  );
}

function themePreviewStyle(theme: ThemeDefinition) {
  return {
    "--preview-bg": theme.preview[0],
    "--preview-surface": theme.preview[1],
    "--preview-accent": theme.preview[2],
    "--preview-text": theme.preview[3],
  } as React.CSSProperties;
}

const HEALTH_PROBE_NAMES = ["database", "media_runtime", "coordinators", "security_cleanup"] as const;

function coordinatorProbeDetails(probe: Health["probes"][number] | undefined) {
  const coordinators = probe?.details.coordinators;
  if (!coordinators?.length) return "";
  const labels: Record<string, string> = {
    link_preview: "链接预检",
    package_import: "数据包",
    processing: "入库处理",
  };
  return coordinators.map((item) =>
    `${labels[item.name] || item.name} ${item.workers_alive}/${item.workers_expected}`,
  ).join(" · ");
}

function healthFromProbes(probes: Health["probes"]): Health {
  const overall = probes.some((probe) => probe.status === "down")
    ? "down"
    : probes.some((probe) => probe.status === "degraded")
      ? "degraded"
      : "healthy";
  return {
    overall,
    summary: overall === "healthy" ? "本地运行环境正常" : "部分本地处理能力需要处理",
    checked_at: new Date().toISOString(),
    probes,
  };
}

function modelOptionLabel(model: string) {
  const notes: Record<string, string> = {
    "qwen3.6-flash": "低成本默认",
    "qwen3.7-flash": "新一代轻量",
    "qwen3.7-plus": "能力与成本均衡",
    "qwen3.7-max": "高能力",
    "qwen-math-turbo": "数学专项，仅建议用于对话",
    "deepseek-r1-distill-qwen-7b": "推理蒸馏",
    "deepseek-v4-flash": "第三方轻量",
    "deepseek-v4-pro": "第三方高能力",
    "glm-5": "第三方推理",
    "glm-5.1": "第三方推理",
    "glm-5.2": "第三方推理",
  };
  return notes[model] ? `${model}（${notes[model]}）` : model;
}

const PROVIDER_LABELS: Record<ModelProviderType, string> = {
  dashscope: "阿里云百炼 DashScope",
  openai: "OpenAI 兼容接口",
};

const MODEL_PROFILE_ROLES: Array<{ key: keyof ProfileDraft; label: string; hint: string }> = [
  { key: "ocr_model", label: "画面识别模型", hint: "视频关键帧 OCR" },
  { key: "asr_model", label: "语音转写模型", hint: "音频语音识别" },
  { key: "embedding_model", label: "向量模型", hint: "知识库语义检索" },
  { key: "enrichment_model", label: "总结模型", hint: "入库与 AI 摘要生成" },
  { key: "chat_model", label: "对话模型", hint: "AI 问答主模型" },
  { key: "rerank_model", label: "重排模型", hint: "检索结果精排" },
];

type ProfileDraft = {
  name: string;
  provider_type: ModelProviderType;
  base_url: string;
  api_key: string;
  ocr_model: string;
  asr_model: string;
  embedding_model: string;
  enrichment_model: string;
  chat_model: string;
  rerank_model: string;
};

function profileToDraft(profile: ModelProfile | null): ProfileDraft {
  return {
    name: profile?.name || "",
    provider_type: profile?.provider_type || "dashscope",
    base_url: profile?.base_url || "",
    api_key: "",
    ocr_model: profile?.ocr_model || "",
    asr_model: profile?.asr_model || "",
    embedding_model: profile?.embedding_model || "",
    enrichment_model: profile?.enrichment_model || "",
    chat_model: profile?.chat_model || "",
    rerank_model: profile?.rerank_model || "",
  };
}

function ModelProfileManager({ perform, children }: { perform: PerformOperation; children?: React.ReactNode }) {
  const [view, setView] = useState<ModelProfilesView | null>(null);
  const [loadError, setLoadError] = useState("");
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState("");
  const [editorError, setEditorError] = useState("");

  useEffect(() => {
    let active = true;
    api.modelProfiles()
      .then((result) => { if (active) setView(result); })
      .catch((value) => { if (active) setLoadError(reason(value, "读取模型配置失败")); });
    return () => { active = false; };
  }, []);

  async function runProfileOp(
    key: string,
    operation: () => Promise<ModelProfilesView>,
    success: string,
  ) {
    setBusyKey(key);
    setEditorError("");
    try {
      const ok = await perform(key, operation, success);
      if (ok) {
        setDraft(null);
        setEditingId(null);
      }
      return ok;
    } finally {
      setBusyKey("");
    }
  }

  function openCreate() {
    setEditorError("");
    if (view) {
      // Start from the deployment-default models so new dashscope profiles
      // mirror the current behaviour until the user overrides each role.
      setDraft(profileToDraft(view.default_profile));
    } else {
      setDraft(profileToDraft(null));
    }
    setEditingId(null);
  }

  async function saveEditor() {
    if (!draft) return;
    const name = draft.name.trim();
    if (!name) {
      setEditorError("请填写配置名称。");
      return;
    }
    const payload: ModelProfilePayload = {
      name,
      provider_type: draft.provider_type,
      base_url: draft.base_url.trim(),
      api_key: draft.api_key.trim(),
      ocr_model: draft.ocr_model.trim(),
      asr_model: draft.asr_model.trim(),
      embedding_model: draft.embedding_model.trim(),
      enrichment_model: draft.enrichment_model.trim(),
      chat_model: draft.chat_model.trim(),
      rerank_model: draft.rerank_model.trim(),
    };
    const key = editingId ? `mp-update-${editingId}` : "mp-create";
    void runProfileOp(
      key,
      editingId
        ? () => api.updateModelProfile(editingId, payload).then((result) => { setView(result); return result; })
        : () => api.createModelProfile(payload).then((result) => { setView(result); return result; }),
      editingId ? `已保存配置「${name}」` : `已新增配置「${name}」`,
    );
  }

  const rows = view
    ? [view.default_profile, ...view.items]
    : [];

  const activeRow = view
    ? rows.find((profile) => profile.id === view.active_id) || view.default_profile
    : null;

  return (
    <div className="model-profiles-card">
      {loadError && <p className="profile-error">{loadError}</p>}

      {view && activeRow && (
        <div className="profile-row active current-profile">
          <div className="profile-main">
            <div className="profile-title">
              <strong>{activeRow.name}</strong>
              <span className="pill succeeded">生效中</span>
            </div>
            <div className="profile-meta">
              {PROVIDER_LABELS[activeRow.provider_type] || activeRow.provider_type}
              {activeRow.base_url ? ` · ${activeRow.base_url}` : ""}
            </div>
            <div className="profile-models" aria-label="当前生效配置使用的模型">
              <span>画面 {activeRow.ocr_model || "—"}</span>
              <span>语音 {activeRow.asr_model || "—"}</span>
              <span>向量 {activeRow.embedding_model || "—"}</span>
              <span>总结 {activeRow.enrichment_model || "—"}</span>
              <span>对话 {activeRow.chat_model || "—"}</span>
              <span>重排 {activeRow.rerank_model || "—"}</span>
            </div>
          </div>
        </div>
      )}

      {children}

      <div className="subhead standalone"><span>新增模型</span></div>
      <p className="muted">可保存多套供应商与模型组合并随时切换。媒体解析链（OCR/ASR/画面/总结）仅支持百炼型配置；密钥经 DPAPI 加密只保存在本机后台，页面不显示明文。</p>
      <div className="button-row profile-add">
        <button type="button" className="secondary" disabled={busyKey !== "" || draft !== null} onClick={openCreate}>
          + 新增一套供应商配置
        </button>
      </div>

      {draft && (
        <form className="profile-editor" onSubmit={(event) => { event.preventDefault(); void saveEditor(); }}>
          <div className="profile-editor-title">
            <strong>{editingId ? "编辑供应商配置" : "新增供应商配置"}</strong>
            <span className={`pill ${draft.provider_type === "openai" ? "running" : "succeeded"}`}>{PROVIDER_LABELS[draft.provider_type]}</span>
          </div>
          <div className="form-grid">
            <label className="field wide">
              <span>配置名称</span>
              <input value={draft.name} maxLength={80} autoFocus
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="例如：备选 DeepSeek 通道 / 本地网关" />
            </label>
            <label className="field">
              <span>供应商类型</span>
              <select value={draft.provider_type} aria-label="供应商类型"
                onChange={(event) => {
                  const providerType = event.target.value as ModelProviderType;
                  setDraft({ ...draft, provider_type: providerType, base_url: providerType === "dashscope" ? "" : draft.base_url });
                }}>
                <option value="dashscope">阿里云百炼 DashScope</option>
                <option value="openai">OpenAI 兼容接口</option>
              </select>
              <small>{draft.provider_type === "openai" ? "纯文本/向量/对话；不提供媒体解析专属能力" : "支持画面识别、语音转写与媒体总结全链路"}</small>
            </label>
            <label className="field">
              <span>API Key {editingId ? "（留空保持不变）" : ""}</span>
              <input type="password" autoComplete="new-password" value={draft.api_key}
                onChange={(event) => setDraft({ ...draft, api_key: event.target.value })}
                placeholder={editingId ? "已加密保存，无需重新输入" : "请输入该供应商的 API Key"} />
            </label>
            {draft.provider_type === "openai" && (
              <label className="field wide">
                <span>接口地址 base_url</span>
                <input value={draft.base_url} maxLength={500} placeholder="https://api.openai.com/v1"
                  onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} />
                <small>留空时后端使用默认 OpenAI 兼容接入点。</small>
              </label>
            )}
            {MODEL_PROFILE_ROLES.map((role) => (
              <label className="field" key={role.key}>
                <span>{role.label}</span>
                <input value={draft[role.key]} maxLength={200} placeholder="留空使用内置默认"
                  onChange={(event) => setDraft({ ...draft, [role.key]: event.target.value })} />
                <small>{role.hint}</small>
              </label>
            ))}
          </div>
          <div className="button-row">
            <button type="submit" className="primary" disabled={busyKey !== "" || !draft.name.trim()}>
              {busyKey ? "保存中…" : "保存配置"}
            </button>
            <button type="button" className="secondary" disabled={busyKey !== ""}
              onClick={() => { setDraft(null); setEditingId(null); setEditorError(""); }}>
              取消
            </button>
          </div>
          {editorError && <p className="profile-error">{editorError}</p>}
        </form>
      )}
    </div>
  );
}

export function Settings({
  settings,
  usage,
  health,
  onHealthChange,
  busy,
  perform,
}: {
  settings: RuntimeSettings;
  usage: Usage | null;
  health: Health | null;
  onHealthChange: (health: Health) => void;
  busy: string;
  perform: PerformOperation;
}) {
  const [checking, setChecking] = useState(false);
  const [checkProgress, setCheckProgress] = useState(health ? 100 : 0);
  const [liveProbes, setLiveProbes] = useState<Health["probes"]>(health?.probes || []);
  const [billingAccessKeyIdDraft, setBillingAccessKeyIdDraft] = useState("");
  const [billingAccessKeySecretDraft, setBillingAccessKeySecretDraft] = useState("");
  const [dashscopeApiKeyDraft, setDashscopeApiKeyDraft] = useState("");

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const numeric = ["daily_media_minutes_limit", "daily_llm_token_limit", "monthly_warning_cny", "scene_threshold", "max_scene_candidates", "max_keyframes", "min_keyframe_gap_seconds"] as const;
    const body: RuntimeSettingsUpdate = {};
    numeric.forEach((name) => body[name] = Number(form.get(name)));
    body.processing_model = String(form.get("processing_model") || "");
    await perform("settings", () => api.saveSettings(body), "设置已保存");
  }

  async function saveParseSettings() {
    const form = new FormData(document.querySelector<HTMLFormElement>("form.stack")!);
    const names = ["scene_threshold", "max_scene_candidates", "max_keyframes", "min_keyframe_gap_seconds"] as const;
    const body: RuntimeSettingsUpdate = {};
    names.forEach((name) => { body[name] = Number(form.get(name)); });
    await perform("parse-settings", () => api.saveSettings(body), "解析设置已保存");
  }

  async function saveFeeSettings() {
    const form = new FormData(document.querySelector<HTMLFormElement>("form.stack")!);
    const names = ["daily_media_minutes_limit", "monthly_warning_cny", "daily_llm_token_limit"] as const;
    const body: RuntimeSettingsUpdate = {};
    names.forEach((name) => { body[name] = Number(form.get(name)); });
    await perform("fee-settings", () => api.saveSettings(body), "费用设置已保存");
  }

  async function runHealthDetection() {
    setChecking(true);
    setCheckProgress(0);
    setLiveProbes([]);
    await perform(
      "health-check",
      async () => {
        const nextProbes: Health["probes"] = [];
        for (const [index, probeName] of HEALTH_PROBE_NAMES.entries()) {
          const probe = await api.healthProbe(probeName);
          nextProbes.push(probe);
          setLiveProbes([...nextProbes]);
          setCheckProgress(Math.round(((index + 1) / HEALTH_PROBE_NAMES.length) * 100));
          // Keep each genuine probe result visible long enough to be perceived
          // instead of flashing directly from 0% to 100% on fast local machines.
          await new Promise((resolve) => window.setTimeout(resolve, 180));
        }
        onHealthChange(healthFromProbes(nextProbes));
      },
      "本地检测已完成",
    );
    setChecking(false);
  }

  async function saveDashscopeKey() {
    const value = dashscopeApiKeyDraft.trim();
    if (!value) return;
    const saved = await perform(
      "dashscope-key",
      () => api.saveSettings({ dashscope_api_key: value }),
      "百炼模型 API Key 已在本机后台加密保存",
    );
    if (saved) setDashscopeApiKeyDraft("");
  }

  async function saveBillingCredentials() {
    const accessKeyId = billingAccessKeyIdDraft.trim();
    const accessKeySecret = billingAccessKeySecretDraft.trim();
    if (!accessKeyId && !accessKeySecret) return;
    const saved = await perform(
      "billing-credentials",
      () => api.saveSettings({
        ...(accessKeyId ? { bss_access_key_id: accessKeyId } : {}),
        ...(accessKeySecret ? { bss_access_key_secret: accessKeySecret } : {}),
      }),
      "账单查询凭据已在本机后台加密保存",
    );
    if (saved) {
      setBillingAccessKeyIdDraft("");
      setBillingAccessKeySecretDraft("");
    }
  }

  async function clearAllKeys() {
    const confirmed = window.confirm(
      "确定删除本机保存的全部百炼 API Key 和账单 AccessKey 吗？删除后 AI 处理、对话和官方账单查询将不可用，直至重新填写。",
    );
    if (!confirmed) return;
    const cleared = await perform(
      "clear-all-keys",
      () => api.clearAllKeys(),
      "全部模型 API Key 与账单 AccessKey 已删除",
    );
    if (cleared) {
      setDashscopeApiKeyDraft("");
      setBillingAccessKeyIdDraft("");
      setBillingAccessKeySecretDraft("");
    }
  }

  const activeProbeIndex = checking
    ? Math.min(HEALTH_PROBE_NAMES.length - 1, liveProbes.length)
    : -1;
  const visibleProbes = checking ? liveProbes : liveProbes.length ? liveProbes : health?.probes || [];

  return (
    <div className="settings-layout">
      <form className="stack" onSubmit={save}>
        <ThemePicker />
        {settings.security_cleanup_required && (
          <section className="risk-notice"><strong>敏感残留尚未清理</strong><p>{settings.security_cleanup_message}</p></section>
        )}
        <section className="card health-card">
          <div className="section-head">
            <div><span className="kicker">本地检查</span><h2>{health?.summary || "本地运行环境"}</h2></div>
            <div className="health-detection">
              <button type="button" className="secondary" disabled={checking || busy === "health-check"} onClick={runHealthDetection}>
                {checking ? "检测中…" : "重新检测"}
              </button>
              <span>{checking ? `${checkProgress}%` : health ? "检测完成" : "尚未检测"}</span>
            </div>
          </div>
          <div className="health-progress" aria-label="本地检测进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={checkProgress} role="progressbar">
            <i style={{ width: `${checkProgress}%` }} />
          </div>
          <div className="probe-list">
            {HEALTH_PROBE_NAMES.map((probeName, index) => {
              const probe = visibleProbes.find((item) => item.probe === probeName);
              const pendingState = checking && index === activeProbeIndex ? "checking" : "unknown";
              return (
                <div className="probe" key={probeName}>
                  <span className={`signal ${probe?.status || pendingState}`} />
                  <div>
                    <strong>{probeName === "database" ? "本地数据库" : probeName === "media_runtime" ? "音视频工具" : probeName === "coordinators" ? "后台协调器" : "敏感数据清理"}</strong>
                    <small>{probe?.message || (pendingState === "checking" ? "正在检测…" : "等待检测")}</small>
                    {probeName === "coordinators" && coordinatorProbeDetails(probe) && <small>{coordinatorProbeDetails(probe)}</small>}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
        <CookieVault />
        <section className="card model-config-card">
          <div className="section-head">
            <div><span className="kicker">模型配置</span><h2>模型与对话</h2></div>
          </div>
          <div className="subhead standalone"><span>当前模型</span></div>
          <ModelProfileManager perform={perform}>
          <div className="form-grid">
            <div className="field model-readonly">
              <span>语音转写模型</span>
              <p>{settings.asr_model}（百炼）</p>
            </div>
            <label className="field">
              <span>视频图文总结模型</span>
              <select name="processing_model" defaultValue={settings.processing_model}>
                {settings.processing_model_options.map((model) => (
                  <option key={model} value={model}>{modelOptionLabel(model)}</option>
                ))}
              </select>
              <small>仅列出支持 JSON 结构化总结的文本生成模型；向量、重排、语音模型不能用于这里。</small>
            </label>
            <label className="field wide inline-action">
              <span>百炼模型 API Key {settings.has_dashscope_key && "（已加密保存）"}</span>
              <div className="input-with-button">
                <input
                  name="dashscope_api_key"
                  type="password"
                  autoComplete="new-password"
                  value={dashscopeApiKeyDraft}
                  onChange={(event) => setDashscopeApiKeyDraft(event.target.value)}
                  placeholder={settings.has_dashscope_key ? "已加密保存，无需重新输入" : "请输入百炼 API Key"}
                />
                <button
                  type="button"
                  className="secondary"
                  disabled={!dashscopeApiKeyDraft.trim() || busy === "dashscope-key"}
                  onClick={saveDashscopeKey}
                >
                  {busy === "dashscope-key" ? "保存中…" : "保存"}
                </button>
              </div>
            </label>
            <label className="field">
              <span>账单查询 AccessKey ID {settings.has_bss_credentials && "（后台已保存）"}</span>
              <input
                name="bss_access_key_id"
                type="password"
                autoComplete="off"
                value={billingAccessKeyIdDraft}
                onChange={(event) => setBillingAccessKeyIdDraft(event.target.value)}
                placeholder={settings.has_bss_credentials ? "已加密保存；无需重新输入" : "请输入只读账单 AccessKey ID"}
              />
            </label>
            <label className="field">
              <span>账单查询 AccessKey Secret {settings.has_bss_credentials && "（后台已保存）"}</span>
              <input
                name="bss_access_key_secret"
                type="password"
                autoComplete="off"
                value={billingAccessKeySecretDraft}
                onChange={(event) => setBillingAccessKeySecretDraft(event.target.value)}
                placeholder={settings.has_bss_credentials ? "已加密保存；不会回显完整密钥" : "请输入只读账单 AccessKey Secret"}
              />
            </label>
            <div className="field wide billing-credentials-status">
              <span>账单查询状态</span>
              <div>
                <span className={`pill ${settings.has_bss_credentials ? "succeeded" : "cancelled"}`}>
                  {settings.has_bss_credentials ? "已加密保存" : "未配置"}
                </span>
                <small>
                  {settings.has_bss_credentials
                    ? "AccessKey ID 与 Secret 已保存在本机后台，退出页面或重启应用后仍然有效。为避免泄露，密码框不会显示原文。"
                    : "尚未保存账单凭据。请同时填写 ID 与 Secret 后点击右侧按钮。"}
                </small>
                <button
                  type="button"
                  className="secondary"
                  disabled={
                    (!billingAccessKeyIdDraft.trim() && !billingAccessKeySecretDraft.trim())
                    || (!settings.has_bss_credentials && (!billingAccessKeyIdDraft.trim() || !billingAccessKeySecretDraft.trim()))
                    || busy === "billing-credentials"
                  }
                  onClick={saveBillingCredentials}
                >
                  {busy === "billing-credentials" ? "保存中…" : settings.has_bss_credentials ? "更新凭据" : "保存"}
                </button>
              </div>
            </div>
          </div>
          </ModelProfileManager>
        </section>
        <section className="card parse-fee-card">
          <div className="section-head">
            <div><span className="kicker">解析配置与费用</span><h2>解析与额度</h2></div>
          </div>
          <div className="form-grid">
            <div className="subhead"><span>解析设置</span></div>
            <Field name="max_scene_candidates" label="初选画面上限" value={settings.max_scene_candidates} min={12} max={1000} />
            <Field name="scene_threshold" label="画面变化灵敏度" value={settings.scene_threshold} min={0.05} max={0.95} step=".05" />
            <Field name="min_keyframe_gap_seconds" label="画面最小间隔" value={settings.min_keyframe_gap_seconds} min={0.2} max={60} step=".1" />
            <Field name="max_keyframes" label="最终保留画面" value={settings.max_keyframes} min={1} max={48} />
            <div className="button-row group-save">
              <button type="button" className="primary" disabled={busy === "parse-settings"} onClick={saveParseSettings}>
                {busy === "parse-settings" ? "保存中…" : "保存解析设置"}
              </button>
            </div>
            <div className="subhead"><span>费用</span></div>
            <Field name="daily_media_minutes_limit" label="每日解析媒体总时长" value={settings.daily_media_minutes_limit} min={1} />
            <Field name="monthly_warning_cny" label="月度费用警戒线" value={settings.monthly_warning_cny} min={0} step=".01" />
            <label className="field wide">
              <span>每日调用 tokens</span>
              <div className="field-control">
                <input name="daily_llm_token_limit" type="number" defaultValue={settings.daily_llm_token_limit} min={1000} />
                <small className="wan-hint">{formatWan(settings.daily_llm_token_limit)}（万进制）</small>
              </div>
            </label>
            <div className="button-row group-save">
              <button type="button" className="primary" disabled={busy === "fee-settings"} onClick={saveFeeSettings}>
                {busy === "fee-settings" ? "保存中…" : "保存费用设置"}
              </button>
            </div>
            <div className="field wide billing-summary">
              <span>本月账单</span>
              <div className="billing-summary-grid">
                <span>
                  <small>TokBrain 本地估算</small>
                  <strong>¥ {(usage?.month_estimated_cny || 0).toFixed(4)}</strong>
                </span>
                <span>
                  <small>阿里云官方账单</small>
                  <strong>{usage?.official_billed_cny == null ? "尚未查询" : `¥ ${usage.official_billed_cny.toFixed(4)}`}</strong>
                </span>
                <span>
                  <small>官方账单状态</small>
                  <strong>{
                    usage?.official_status === "available_delayed"
                      ? "已获取（存在结算延迟）"
                      : usage?.official_status === "error"
                        ? "查询失败"
                        : "尚未查询"
                  }</strong>
                </span>
                <span>
                  <small>官方数据更新时间</small>
                  <strong>{usage?.official_data_as_of ? new Date(usage.official_data_as_of).toLocaleString("zh-CN") : "—"}</strong>
                </span>
              </div>
            </div>
            <div className="field wide billing-refresh">
              <span>官方账单核对</span>
              <div>
                <small>先保存后可直接刷新；官方数据通常有约 24 小时结算延迟，可能与本地动态估算不同。</small>
                <button
                  type="button"
                  className="secondary"
                  disabled={!settings.has_bss_credentials || busy === "official-bill"}
                  onClick={() => perform("official-bill", () => api.refreshOfficialBill(), "官方账单已刷新")}
                >
                  {busy === "official-bill" ? "查询中…" : "刷新官方账单"}
                </button>
              </div>
            </div>
          </div>
          <p className="muted">{settings.dpapi_warning}</p>
          <p className="muted">今日公开链接 {usage?.daily_links_used || 0}/{usage?.daily_links_limit || 150} · 今日 AI {(usage?.daily_llm_tokens_used || 0).toLocaleString()}/{(usage?.daily_llm_tokens_limit || 0).toLocaleString()}</p>
        </section>
        <section className="card credential-cleanup">
          <div>
            <span className="kicker">敏感凭据清理</span>
            <h2>删除全部 API Key 与 AccessKey</h2>
            <p className="muted">删除百炼模型 API Key、账单 AccessKey ID/Secret 及已缓存的官方账单；不会删除知识库、作品、总结或解析 Cookie。</p>
          </div>
          <button type="button" className="danger" disabled={busy === "clear-all-keys"} onClick={clearAllKeys}>
            {busy === "clear-all-keys" ? "删除中…" : "删除全部密钥"}
          </button>
        </section>
      </form>
    </div>
  );
}

function formatWan(value: number): string {
  return String(Math.trunc(value)).replace(/\B(?=(\d{4})+(?!\d))/g, ",");
}

function Field({ name, label, value, min, max, step = "1" }: { name: string; label: string; value: number; min: number; max?: number; step?: string }) {
  return <label className="field"><span>{label}</span><div className="field-control"><input name={name} type="number" defaultValue={value} min={min} max={max} step={step} /></div></label>;
}
