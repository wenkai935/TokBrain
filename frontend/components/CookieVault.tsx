"use client";

import type { ChangeEvent } from "react";
import { useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import type { CookiePlatformStatus, CookieVaultState } from "../lib/api";
import { reason } from "../lib/errors";

const PLATFORM_ORDER = ["douyin", "bilibili", "xiaohongshu"];

function statusOf(item: CookiePlatformStatus): { text: string; color: string } {
  if (!item.exists) return { text: "未上传", color: "var(--muted)" };
  if (item.auth_ready) return { text: "登录态就绪", color: "var(--cyan)" };
  return { text: "缺少登录态", color: "var(--amber)" };
}

function formatTime(value: string | null): string {
  if (!value) return "";
  return value.replace("T", " ").replace(/[+-]\d{2}:\d{2}$/, "");
}

/**
 * 统一 Cookie 库面板。
 *
 * 设计意图：全项目只保留一个上传口。用户在这里放一份 cookies.txt，
 * 抖音下载器、F2、DOM 采集、以及后续的小红书/B 站采集都从同一个目录取，
 * 不再各自要一份、也不必在多个设置项里重复粘贴。
 */
export function CookieVault() {
  const [state, setState] = useState<CookieVaultState | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const allInputRef = useRef<HTMLInputElement>(null);
  const platformInputs = useRef<Record<string, HTMLInputElement | null>>({});

  // 首次进来拉一次状态；后续上传/删除直接用接口返回值更新，避免多余往返。
  useEffect(() => {
    let active = true;
    api.cookieVault()
      .then((next) => {
        if (active) setState(next);
      })
      .catch((value) => {
        if (active) setError(reason(value, "读取 Cookie 库失败"));
      });
    return () => {
      active = false;
    };
  }, []);

  async function uploadAll(file: File) {
    setBusy("all");
    setError("");
    setMessage("");
    try {
      const next = await api.uploadCookieVault(file);
      setState(next);
      const hit = next.platforms.map((item) => `${item.label} ${item.cookie_count} 条`);
      const skipped = next.skipped ?? [];
      const missed = skipped.length
        ? `；未识别到 ${skipped
            .map((key) => state?.platforms.find((item) => item.platform === key)?.label || key)
            .join("、")}`
        : "";
      setMessage(`上传完成：${hit.join("，")}${missed}`);
    } catch (value) {
      setError(reason(value, "上传失败，请确认导出的是 cookies.txt"));
    } finally {
      setBusy("");
    }
  }

  async function uploadOne(platform: string, file: File) {
    setBusy(platform);
    setError("");
    setMessage("");
    try {
      const saved = await api.uploadPlatformCookie(platform, file);
      setState((current) =>
        current
          ? {
              ...current,
              platforms: current.platforms.map((item) =>
                item.platform === saved.platform ? saved : item,
              ),
            }
          : current,
      );
      setMessage(`${saved.label}已更新：${saved.cookie_count} 条 Cookie`);
    } catch (value) {
      setError(reason(value, "上传失败，请确认文件里有该平台的 Cookie"));
    } finally {
      setBusy("");
    }
  }

  async function remove(platform: string, label: string) {
    if (!window.confirm(`删除${label}的 Cookie？下载与采集会立刻失去登录态。`)) return;
    setBusy(platform);
    setError("");
    setMessage("");
    try {
      const cleared = await api.deletePlatformCookie(platform);
      setState((current) =>
        current
          ? {
              ...current,
              platforms: current.platforms.map((item) =>
                item.platform === cleared.platform ? cleared : item,
              ),
            }
          : current,
      );
      setMessage(`${label}的 Cookie 已删除`);
    } catch (value) {
      setError(reason(value, "删除失败"));
    } finally {
      setBusy("");
    }
  }

  function onFile(
    event: ChangeEvent<HTMLInputElement>,
    handler: (file: File) => void | Promise<void>,
  ) {
    const file = event.target.files?.[0];
    // 清空 value：同一份文件重新上传也要触发 change
    event.target.value = "";
    if (file) void handler(file);
  }

  const platforms = (state?.platforms || [])
    .slice()
    .sort(
      (a, b) => PLATFORM_ORDER.indexOf(a.platform) - PLATFORM_ORDER.indexOf(b.platform),
    );

  return (
    <section className="card cookie-vault">
      <div className="section-head">
        <div>
          <span className="kicker">统一 Cookie 库</span>
          <h2>Cookie 上传</h2>
        </div>
        <button
          type="button"
          className="secondary"
          disabled={busy === "all"}
          onClick={() => allInputRef.current?.click()}
        >
          {busy === "all" ? "上传中…" : "上传 cookies.txt"}
        </button>
        <input
          ref={allInputRef}
          type="file"
          accept=".txt,.json,text/plain"
          hidden
          onChange={(event) => onFile(event, uploadAll)}
        />
      </div>

      <p className="muted" style={{ margin: "0 0 4px", lineHeight: 1.7 }}>
        所有需要登录态的功能都从这一个目录读取，上传一次即可：抖音下载器、F2 链接解析、
        DOM 采集，以及以后的 B 站 / 小红书采集。支持浏览器导出的
        <code> cookies.txt</code>（Netscape）、扩展导出的 JSON、以及直接粘贴的
        <code> a=1; b=2</code> 串——后端会统一转成 Netscape 落盘，
        <code> yt-dlp --cookies</code> 可以直接吃。
      </p>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 8,
          alignItems: "center",
          margin: "10px 0 16px",
          padding: "10px 12px",
          border: "1px solid var(--line)",
          borderRadius: 10,
          background: "var(--panel-2)",
          fontSize: 12,
        }}
      >
        <span className="muted">存放位置</span>
        <code style={{ overflowWrap: "anywhere" }}>{state?.directory || "读取中…"}</code>
      </div>

      {message && (
        <p style={{ margin: "0 0 12px", color: "var(--cyan)", fontSize: 13 }}>{message}</p>
      )}
      {error && (
        <p role="alert" style={{ margin: "0 0 12px", color: "var(--danger-text)", fontSize: 13 }}>
          {error}
        </p>
      )}

      <div style={{ display: "grid", gap: 10 }}>
        {platforms.map((item) => {
          const badge = statusOf(item);
          return (
            <div
              key={item.platform}
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0,1fr) auto",
                gap: 12,
                alignItems: "center",
                padding: "12px 14px",
                border: "1px solid var(--line)",
                borderRadius: 12,
                background: "var(--panel-2)",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <strong style={{ fontSize: 14 }}>{item.label}</strong>
                  <span
                    style={{
                      fontSize: 11,
                      padding: "2px 8px",
                      borderRadius: 999,
                      border: `1px solid ${badge.color}`,
                      color: badge.color,
                    }}
                  >
                    {badge.text}
                  </span>
                  <span className="muted" style={{ fontSize: 11 }}>
                    {item.filename}
                  </span>
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 4, lineHeight: 1.6 }}>
                  {item.exists
                    ? `${item.cookie_count} 条 · 上次更新 ${formatTime(item.updated_at) || "未知"}${
                        item.expired_count ? ` · ${item.expired_count} 条已过期` : ""
                      }`
                    : item.note}
                </div>
                {item.exists && !item.auth_ready && item.missing_auth.length > 0 && (
                  <div style={{ fontSize: 12, marginTop: 4, color: "var(--amber)" }}>
                    缺少关键项：{item.missing_auth.join("、")}（多半是未登录状态下导出的）
                  </div>
                )}
              </div>

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy === item.platform}
                  onClick={() => platformInputs.current[item.platform]?.click()}
                >
                  {busy === item.platform ? "上传中…" : "上传"}
                </button>
                <input
                  ref={(node) => {
                    platformInputs.current[item.platform] = node;
                  }}
                  type="file"
                  accept=".txt,.json,text/plain"
                  hidden
                  onChange={(event) => onFile(event, (file) => uploadOne(item.platform, file))}
                />
                {item.exists && (
                  <>
                    {/* .secondary 只作用于 button，链接要自己对齐同样的外观 */}
                    <a
                      href={api.cookieDownloadUrl(item.platform)}
                      download
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        borderRadius: 8,
                        padding: "11px 17px",
                        border: "1px solid var(--line)",
                        color: "inherit",
                        textDecoration: "none",
                        fontSize: 13,
                      }}
                    >
                      下载
                    </a>
                    <button
                      type="button"
                      className="link"
                      disabled={busy === item.platform}
                      onClick={() => void remove(item.platform, item.label)}
                    >
                      删除
                    </button>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
