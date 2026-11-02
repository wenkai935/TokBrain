"""DashScope model calls with usage accounting returned to the caller.

``DashScopeProvider`` extends the OpenAI-compatible provider from ``base`` with
the DashScope-only capabilities (ASR/OCR/keyframe/summary).  ``get_provider``
builds the configured provider for RAG-facing call sites.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, settings
from app.services.pricing import ASR_CNY_PER_SECOND, asr_cost, token_cost
from app.services.prompts import DEFAULT_SUMMARY_PROMPT
from app.services.providers.base import (
    BaseProvider,
    OpenAICompatibleProvider,
    ProviderUsage,
    TranscriptResult,
    TranscriptSegment,
    close_provider_clients,
)
from app.services.summaries import extract_summary_json
from app.services.temp_files import unlink_with_retries


__all__ = [
    "ASR_CNY_PER_SECOND",
    "ASR_RESULT_MAX_BYTES",
    "BaseProvider",
    "DashScopeProvider",
    "OpenAICompatibleProvider",
    "ProviderUsage",
    "SUMMARY_MAX_OUTPUT_TOKENS",
    "TranscriptResult",
    "TranscriptSegment",
    "asr_audio_command",
    "asr_cost",
    "close_provider_clients",
    "get_provider",
    "normalize_keyframe_selection",
    "normalize_visual_requirements",
    "parse_transcription_payload",
    "token_cost",
]


SUMMARY_MAX_OUTPUT_TOKENS = 16_384
ASR_RESULT_MAX_BYTES = 10 * 1024 * 1024
ASR_RESULT_MAX_REDIRECTS = 3
ASR_RESULT_HOST_SUFFIXES = (".aliyuncs.com",)
ASR_FFMPEG_TIMEOUT_SECONDS = 300
ASR_TASK_TIMEOUT_SECONDS = 900
ASR_HTTP_REQUEST_TIMEOUT_SECONDS = 60
ASR_UPLOAD_ATTEMPTS = 2


def _is_transient_provider_network_error(error: BaseException) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        module = type(current).__module__.lower()
        name = type(current).__name__.lower()
        value = str(current).lower()
        if (
            module.startswith(("requests", "urllib3"))
            and name
            in {
                "connectionerror",
                "connecttimeout",
                "proxyerror",
                "readtimeout",
                "sslerror",
                "timeout",
            }
        ) or any(
            marker in value
            for marker in (
                "httpsconnectionpool",
                "ssl: unexpected_eof",
                "ssleoferror",
                "connection reset",
                "proxyerror",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _upload_asr_audio(
    oss_utils,
    *,
    model: str,
    file_path: str,
    api_key: str,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    last_error: Exception | None = None
    for attempt in range(ASR_UPLOAD_ATTEMPTS):
        try:  # retry transient provider network errors per attempt.
            return str(
                oss_utils.upload(
                    model=model,
                    file_path=file_path,
                    api_key=api_key,
                )
            )
        except Exception as exc:  # noqa: PERF203
            if not _is_transient_provider_network_error(exc):
                raise
            last_error = exc
            if attempt + 1 < ASR_UPLOAD_ATTEMPTS:
                sleep(1.0)
    raise RuntimeError(
        "连接百炼语音服务时网络或 TLS 中断；请检查系统代理与网络后重试"
    ) from last_error


def _validate_asr_result_url(url: str) -> str:
    """Allow only public Alibaba Cloud HTTPS result locations."""

    try:
        parsed = urlsplit(str(url).strip())
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ASR 转写结果地址无效") from exc
    host = (parsed.hostname or "").lower().strip(".")
    dotted_host = f".{host}"
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or not any(dotted_host.endswith(suffix) for suffix in ASR_RESULT_HOST_SUFFIXES)
    ):
        raise RuntimeError("ASR 转写结果地址不在允许范围内")
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RuntimeError("ASR 转写结果域名解析失败") from exc
    if not addresses:
        raise RuntimeError("ASR 转写结果域名没有可用地址")
    for result in addresses:
        try:
            address = ipaddress.ip_address(result[4][0])
        except ValueError as exc:
            raise RuntimeError("ASR 转写结果域名解析异常") from exc
        if not address.is_global:
            raise RuntimeError("ASR 转写结果地址不是公网地址")
    return str(url)


def _download_asr_result(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    validate_url: Callable[[str], str] = _validate_asr_result_url,
) -> dict:
    """Download one bounded JSON result without leaking its signed query."""

    current = validate_url(url)
    try:
        with httpx.Client(
            timeout=30,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        ) as client:
            for redirect_count in range(ASR_RESULT_MAX_REDIRECTS + 1):
                with client.stream(
                    "GET", current, headers={"Accept": "application/json"}
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirect_count >= ASR_RESULT_MAX_REDIRECTS:
                            raise RuntimeError("ASR 转写结果重定向次数过多")
                        current = validate_url(urljoin(current, location))
                        continue
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"ASR 转写结果下载失败: HTTP {response.status_code}"
                        )
                    declared_length = response.headers.get("content-length")
                    if (
                        declared_length
                        and declared_length.isdigit()
                        and int(declared_length) > ASR_RESULT_MAX_BYTES
                    ):
                        raise RuntimeError("ASR 转写结果超过 10 MB 上限")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if len(content) + len(chunk) > ASR_RESULT_MAX_BYTES:
                            raise RuntimeError("ASR 转写结果超过 10 MB 上限")
                        content.extend(chunk)
                    try:
                        payload = json.loads(bytes(content).decode("utf-8"))
                    except (UnicodeDecodeError, ValueError) as exc:
                        raise RuntimeError("ASR 转写结果不是有效 JSON") from exc
                    if not isinstance(payload, dict):
                        raise RuntimeError("ASR 转写结果结构无效")
                    return payload
    except httpx.HTTPError:
        raise RuntimeError("ASR 转写结果下载失败") from None
    raise RuntimeError("ASR 转写结果下载失败")


def _as_seconds(sentence: dict, *keys: str) -> float | None:
    for key in keys:
        value = sentence.get(key)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed < 0:
            continue
        # Paraformer sentence timestamps are milliseconds for these field names.
        if key in {"begin_time", "end_time", "start_time", "stop_time"}:
            parsed /= 1000
        return parsed
    return None


def parse_transcription_payload(payload: dict) -> TranscriptResult:
    """Keep sentence timing from Paraformer while preserving its full text."""

    texts: list[str] = []
    segments: list[TranscriptSegment] = []
    for transcript in payload.get("transcripts") or []:
        if not isinstance(transcript, dict):
            continue
        sentences = transcript.get("sentences") or []
        sentence_texts: list[str] = []
        for sentence in sentences:
            if not isinstance(sentence, dict):
                continue
            text = str(sentence.get("text") or "").strip()
            if not text:
                continue
            sentence_texts.append(text)
            start = _as_seconds(
                sentence, "begin_time", "start_time", "start_seconds", "start"
            )
            end = _as_seconds(sentence, "end_time", "stop_time", "end_seconds", "end")
            if start is not None:
                segments.append(
                    TranscriptSegment(
                        start_seconds=start,
                        end_seconds=max(start, end if end is not None else start),
                        text=text,
                    )
                )
        full_text = str(transcript.get("text") or "").strip()
        if full_text:
            texts.append(full_text)
        elif sentence_texts:
            texts.append("".join(sentence_texts))
    return TranscriptResult(
        text="\n".join(dict.fromkeys(texts)).strip(),
        segments=tuple(sorted(segments, key=lambda item: item.start_seconds)),
    )


def normalize_visual_requirements(payload: dict, duration_seconds: float) -> list[dict]:
    normalized: list[dict] = []
    for index, item in enumerate(payload.get("requirements") or []):
        if not isinstance(item, dict):
            continue
        try:
            start = max(0.0, float(item.get("start_seconds", 0)))
            end = max(start, float(item.get("end_seconds", start)))
        except (TypeError, ValueError):
            continue
        if duration_seconds > 0:
            start = min(start, duration_seconds)
            end = min(end, duration_seconds)
        need = str(item.get("need") or item.get("description") or "").strip()
        if not need:
            continue
        try:
            priority = int(item.get("priority", 2))
        except (TypeError, ValueError):
            priority = 2
        normalized.append(
            {
                "id": str(item.get("id") or f"need-{index + 1}"),
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "need": need[:500],
                "keywords": [
                    str(value)[:80]
                    for value in item.get("keywords") or []
                    if str(value).strip()
                ][:10],
                "priority": min(3, max(1, priority)),
            }
        )
    return normalized[:16]


def normalize_keyframe_selection(
    payload: dict, allowed_ids: set[str], max_frames: int
) -> list[dict]:
    selected: list[dict] = []
    seen: set[str] = set()
    for item in payload.get("selected") or []:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or "")
        if identifier not in allowed_ids or identifier in seen:
            continue
        try:
            score = float(item.get("score", 0.5))
        except (TypeError, ValueError):
            score = 0.5
        selected.append(
            {
                "id": identifier,
                "score": min(1.0, max(0.0, score)),
                "reason": str(item.get("reason") or "与音频视觉需求匹配")[:500],
                "requirement_id": str(item.get("requirement_id") or "")[:100],
            }
        )
        seen.add(identifier)
        if len(selected) >= max_frames:
            break
    return selected


def asr_audio_command(ffmpeg: str, media_path: Path, audio_path: Path) -> list[str]:
    """Build the official 16 kHz mono Opus preprocessing command for Paraformer."""

    return [
        ffmpeg,
        "-nostdin",
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libopus",
        "-b:a",
        "32k",
        str(audio_path),
    ]


def _wait_for_transcription(
    transcription,
    task_id: str,
    *,
    api_key: str,
    timeout_seconds: float = ASR_TASK_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
):
    """Poll the SDK task with an application-owned total deadline."""

    deadline = monotonic() + max(1.0, timeout_seconds)
    terminal = {"SUCCEEDED", "FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}
    while True:
        result = transcription.fetch(task=task_id, api_key=api_key)
        if getattr(result, "status_code", None) != HTTPStatus.OK:
            return result
        output = getattr(result, "output", None)
        status = (
            output.get("task_status")
            if isinstance(output, dict)
            else getattr(output, "task_status", None)
        )
        if output is None or str(status or "").upper() in terminal:
            return result
        remaining = deadline - monotonic()
        if remaining <= 0:
            with suppress(Exception):
                transcription.cancel(task=task_id, api_key=api_key)
            raise RuntimeError("ASR 转写等待超时")
        sleep(min(2.0, remaining))


class DashScopeProvider(OpenAICompatibleProvider):
    """DashScope provider: OpenAI-compatible chat/embedding plus DashScope-only
    ASR/OCR/keyframe/summary capabilities.

    Model names default to deployment ``config`` values but may be overridden
    per instance, so a multi-profile setup (``model_profiles``) can run jobs
    against models chosen at runtime without touching deployment settings.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        ocr_model: str | None = None,
        asr_model: str | None = None,
        enrichment_model: str | None = None,
        embedding_model: str | None = None,
        chat_model: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("尚未配置百炼 API Key")
        super().__init__(
            api_key,
            base_url=base_url or settings.dashscope_base_url,
            embedding_model=embedding_model,
            chat_model=chat_model,
        )
        self.ocr_model = ocr_model or settings.ocr_model
        self.asr_model = asr_model or settings.asr_model
        self.enrichment_model = enrichment_model or settings.enrichment_model

    async def ocr_image(self, image_path: Path) -> tuple[str, ProviderUsage]:
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        response = await self.client.chat.completions.create(
            model=self.ocr_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "按阅读顺序完整提取图片中的文字；没有文字时返回空字符串。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"},
                        },
                    ],
                }
            ],
            temperature=0,
            max_tokens=2048,
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return text.strip(), ProviderUsage(
            self.ocr_model,
            input_tokens,
            output_tokens,
            token_cost(self.ocr_model, input_tokens, output_tokens),
            quantity=input_tokens + output_tokens,
        )

    async def analyze_keyframe(self, image_path: Path) -> tuple[dict, ProviderUsage]:
        """Extract visible text and a factual visual description in one image call."""

        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        response = await self.client.chat.completions.create(
            model=self.ocr_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "只依据图片返回 JSON 对象，字段为 ocr_text、"
                                "visual_description、content_type、quality_issue。"
                                "ocr_text 按阅读顺序完整提取可见文字；visual_description "
                                "用一句中文客观描述人物、物体、界面、图表、动作或对比结果；"
                                "不得推测图片外的信息。"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"},
                        },
                    ],
                }
            ],
            temperature=0,
            max_tokens=3072,
        )
        raw = (response.choices[0].message.content or "").strip()
        payload = extract_summary_json(raw) or {}
        if not isinstance(payload, dict):
            payload = {}
        analysis = {
            "ocr_text": str(payload.get("ocr_text") or "")[:12_000],
            "visual_description": str(payload.get("visual_description") or raw)[:2_000],
            "content_type": str(payload.get("content_type") or "unknown")[:100],
            "quality_issue": str(payload.get("quality_issue") or "")[:300],
        }
        usage = response.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return analysis, ProviderUsage(
            self.ocr_model,
            input_tokens,
            output_tokens,
            token_cost(self.ocr_model, input_tokens, output_tokens),
            quantity=input_tokens + output_tokens,
        )

    async def plan_visual_requirements(
        self,
        transcript: TranscriptResult,
        *,
        duration_seconds: float,
        model: str | None = None,
    ) -> tuple[list[dict], ProviderUsage]:
        """Turn timed speech into bounded visual evidence requests."""

        selected_model = model or self.enrichment_model
        timed_source = [
            {
                "start_seconds": item.start_seconds,
                "end_seconds": item.end_seconds,
                "text": item.text,
            }
            for item in transcript.segments
        ]
        source = json.dumps(timed_source, ensure_ascii=False)
        if not timed_source:
            source = transcript.text[:40_000]
        response = await self.client.chat.completions.create(
            model=selected_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是视频画面取证规划器。根据带时间的语音，只列出需要画面"
                        "补充或证明的内容，例如步骤界面、参数、图表、商品细节、"
                        "动作、前后对比和最终结果。不要要求普通口播人脸，也不要"
                        "编造语音未提及的信息。只返回 JSON：{requirements:[{id,"
                        "start_seconds,end_seconds,need,keywords,priority}]}。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"视频时长：{duration_seconds:.3f} 秒\n"
                        f"带时间语音：{source[:60_000]}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        payload = extract_summary_json(raw) or {}
        requirements = normalize_visual_requirements(payload, duration_seconds)
        usage = response.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return requirements, ProviderUsage(
            selected_model,
            input_tokens,
            output_tokens,
            token_cost(selected_model, input_tokens, output_tokens),
            quantity=input_tokens + output_tokens,
        )

    async def select_keyframes(
        self,
        requirements: list[dict],
        candidates: list[dict],
        *,
        max_frames: int,
        model: str | None = None,
    ) -> tuple[list[dict], ProviderUsage]:
        """Select only supplied candidate ids using audio and visual evidence."""

        selected_model = model or self.enrichment_model
        response = await self.client.chat.completions.create(
            model=selected_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是视频关键帧选择器。只能选择候选列表中的 id。优先覆盖"
                        "视觉需求、步骤、参数、图表、文字和结果，同时保持时间分布"
                        "并去除重复。至少保留少量不依赖语音的纯视觉信息。只返回 "
                        "JSON：{selected:[{id,score,reason,requirement_id}]}。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"最多选择 {max_frames} 张。\n"
                        f"视觉需求：{json.dumps(requirements, ensure_ascii=False)}\n"
                        f"候选画面：{json.dumps(candidates, ensure_ascii=False)[:70_000]}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        payload = extract_summary_json(raw) or {}
        selected = normalize_keyframe_selection(
            payload,
            {str(item.get("id")) for item in candidates},
            max_frames,
        )
        usage = response.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return selected, ProviderUsage(
            selected_model,
            input_tokens,
            output_tokens,
            token_cost(selected_model, input_tokens, output_tokens),
            quantity=input_tokens + output_tokens,
        )

    async def enrich(self, content: str) -> tuple[str, ProviderUsage]:
        response = await self.client.chat.completions.create(
            model=self.enrichment_model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "将抖音作品整理成忠于原文、便于检索的中文笔记。保留人物、数字、步骤和结论，不臆测。",
                },
                {"role": "user", "content": content[:40_000]},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return text.strip(), ProviderUsage(
            self.enrichment_model,
            input_tokens,
            output_tokens,
            token_cost(self.enrichment_model, input_tokens, output_tokens),
            quantity=input_tokens + output_tokens,
        )

    async def summarize(
        self,
        content: str,
        *,
        asset_ids: list[str] | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> tuple[dict, ProviderUsage]:
        selected_model = model or self.enrichment_model
        allowed_assets = list(dict.fromkeys(asset_ids or []))[:12]
        source = content[:80_000]
        response = await self.client.chat.completions.create(
            model=selected_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        (system_prompt or DEFAULT_SUMMARY_PROMPT).strip()
                        + "\n\n【固定输出约束】只返回一个 JSON 对象，"
                        "字段必须包含 one_sentence、sections、tags、asset_ids；"
                        "sections 的每项必须包含 kind、title、body。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"允许的图片文件名：{json.dumps(allowed_assets, ensure_ascii=False)}\n\n"
                        "请严格按照系统提示词建立完整记忆链。材料中出现几个概念、观点、"
                        "案例、方法或步骤，就逐项完整复现几个，不得只保留结论。\n\n"
                        f"作品材料开始：\n{source}\n作品材料结束。"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=SUMMARY_MAX_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
        )
        text = (response.choices[0].message.content or "").strip()
        payload = extract_summary_json(text)
        if payload is None:
            payload = {
                "one_sentence": text.splitlines()[0][:300] if text else "内容概览",
                "sections": [
                    {
                        "kind": "content",
                        "title": "总结格式异常",
                        "body": "模型返回内容未能解析为结构化总结，请点击“补齐/更新总结”重试。",
                    }
                ],
                "tags": [],
                "asset_ids": allowed_assets[:1],
            }
        if not isinstance(payload, dict):
            payload = {}
        one_sentence = str(payload.get("one_sentence") or "内容概览")[:500]
        sections = []
        for item in payload.get("sections") or []:
            if not isinstance(item, dict) or not str(item.get("body") or "").strip():
                continue
            sections.append(
                {
                    "kind": str(item.get("kind") or "other")[:30],
                    "title": str(item.get("title") or "内容要点")[:100],
                    "body": str(item.get("body"))[:20_000],
                }
            )
        if not sections:
            sections = [{"kind": "content", "title": "讲了什么", "body": one_sentence}]
        tags = [
            str(item)[:50] for item in (payload.get("tags") or []) if str(item).strip()
        ][:12]
        chosen_assets = [
            str(item)
            for item in (payload.get("asset_ids") or [])
            if str(item) in allowed_assets
        ][:6]
        if not chosen_assets:
            chosen_assets = allowed_assets[: min(3, len(allowed_assets))]
        usage = response.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return {
            "one_sentence": one_sentence,
            "sections": sections,
            "tags": tags,
            "asset_ids": chosen_assets,
        }, ProviderUsage(
            selected_model,
            input_tokens,
            output_tokens,
            token_cost(selected_model, input_tokens, output_tokens),
            quantity=input_tokens + output_tokens,
        )

    async def transcribe(
        self, media_path: Path, duration_seconds: float
    ) -> tuple[str, ProviderUsage]:
        transcript, usage = await self.transcribe_detailed(media_path, duration_seconds)
        return transcript.text, usage

    async def transcribe_detailed(
        self, media_path: Path, duration_seconds: float
    ) -> tuple[TranscriptResult, ProviderUsage]:
        try:
            transcript = await asyncio.wait_for(
                asyncio.to_thread(self._transcribe_detailed_sync, media_path),
                timeout=(
                    ASR_FFMPEG_TIMEOUT_SECONDS
                    + ASR_TASK_TIMEOUT_SECONDS
                    + ASR_HTTP_REQUEST_TIMEOUT_SECONDS
                ),
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("ASR 转写总时长超过安全上限") from exc
        return transcript, ProviderUsage(
            self.asr_model,
            0,
            0,
            asr_cost(self.asr_model, duration_seconds),
            metric="audio_seconds",
            quantity=duration_seconds,
            unit="second",
        )

    def _transcribe_detailed_sync(self, media_path: Path) -> TranscriptResult:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("未找到 ffmpeg")
        # A unique name prevents a briefly locked file from blocking a retry of
        # the same work on Windows.
        audio_root = DATA_DIR / "tmp"
        audio_root.mkdir(parents=True, exist_ok=True)
        audio_path = audio_root / (
            f"{media_path.stem}-{uuid.uuid4().hex[:10]}.asr.opus"
        )
        try:
            import dashscope
            from dashscope.audio.asr import Transcription
            from dashscope.utils.oss_utils import OssUtils
        except ImportError as exc:
            raise RuntimeError("未安装 dashscope SDK") from exc
        try:
            process = subprocess.run(
                asr_audio_command(ffmpeg, media_path, audio_path),
                capture_output=True,
                text=True,
                timeout=ASR_FFMPEG_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("音频提取超时") from exc
        if process.returncode != 0:
            raise RuntimeError(f"音频提取失败: {process.stderr[-500:]}")
        dashscope.api_key = self.api_key
        try:
            oss_url = _upload_asr_audio(
                OssUtils,
                model=self.asr_model,
                file_path=str(audio_path),
                api_key=self.api_key,
            )
            response = Transcription.async_call(
                model=self.asr_model,
                file_urls=[oss_url],
                language_hints=["zh", "en"],
                request_timeout=ASR_HTTP_REQUEST_TIMEOUT_SECONDS,
            )
            task_id = getattr(response.output, "task_id", None) or response.output.get(
                "task_id"
            )
            result = _wait_for_transcription(
                Transcription,
                str(task_id),
                api_key=self.api_key,
            )
            if getattr(result, "status_code", None) != HTTPStatus.OK:
                raise RuntimeError(f"ASR 失败: {getattr(result, 'message', '')}")
            output = result.output
            results = (
                output.get("results", [])
                if isinstance(output, dict)
                else output.results
            )
            texts: list[str] = []
            segments: list[TranscriptSegment] = []
            for item in results or []:
                url = (
                    item.get("transcription_url")
                    if isinstance(item, dict)
                    else item.transcription_url
                )
                if not url:
                    continue
                payload = _download_asr_result(str(url))
                parsed = parse_transcription_payload(payload)
                if parsed.text:
                    texts.append(parsed.text)
                segments.extend(parsed.segments)
            return TranscriptResult(
                text="\n".join(text for text in texts if text).strip(),
                segments=tuple(sorted(segments, key=lambda value: value.start_seconds)),
            )
        finally:
            unlink_with_retries(audio_path)


async def get_provider(
    session: AsyncSession,
    *,
    api_key: str | None = None,
) -> BaseProvider:
    """Build the configured provider (defaults to DashScope-compatible OpenAI API).

    ``provider_type`` selects the implementation:
    - ``"dashscope"`` (default): ``DashScopeProvider`` with all DashScope features.
    - ``"openai"``: plain ``OpenAICompatibleProvider`` pointed at ``provider_base_url``
      (falls back to ``dashscope_base_url``) with optional model overrides.
    """
    from app.services.model_profiles import get_active_profile
    from app.services.secrets import get_secret

    try:
        profile = await get_active_profile(session)
    except (AttributeError, TypeError):
        # Sessions without AppSetting access (e.g. unit-test doubles) fall back
        # to the deployment-config behaviour described below.
        profile = None
    provider_type = str(
        (profile or {}).get("provider_type") or settings.provider_type
    )
    base_url = (
        (profile or {}).get("base_url")
        or settings.provider_base_url
        or settings.dashscope_base_url
    )
    embedding_model = (
        (profile or {}).get("embedding_model")
        or settings.provider_embedding_model
        or None
    )
    chat_model = (
        (profile or {}).get("chat_model")
        or settings.provider_chat_model
        or None
    )
    if api_key is None:
        if profile:
            api_key = await get_secret(session, profile["api_key_ref"])
        if not api_key:
            api_key = await get_secret(session, "dashscope_api_key")
    key = api_key
    if provider_type == "openai":
        return OpenAICompatibleProvider(
            key,
            base_url=base_url,
            embedding_model=embedding_model,
            chat_model=chat_model,
        )
    return DashScopeProvider(
        key,
        base_url=base_url,
        ocr_model=(profile or {}).get("ocr_model") or None,
        asr_model=(profile or {}).get("asr_model") or None,
        enrichment_model=(profile or {}).get("enrichment_model") or None,
        embedding_model=embedding_model,
        chat_model=chat_model,
    )
