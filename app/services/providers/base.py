"""Provider interface plus an OpenAI-compatible default implementation.

DashScope's ``/compatible-mode/v1`` endpoint speaks the OpenAI wire format, so
the default provider is simply an OpenAI-compatible client pointed at DashScope
with the configured model names.  ASR/OCR remain DashScope-specific and live on
``DashScopeProvider`` in the package ``__init__``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from collections.abc import AsyncIterator

from loguru import logger
from openai import AsyncOpenAI

from app.config import settings
from app.services.pricing import ASR_CNY_PER_SECOND, TOKEN_PRICES, token_cost


_UNPRICED_MODELS_WARNED: set[tuple[str, str]] = set()
_OPENAI_CLIENTS: dict[tuple[asyncio.AbstractEventLoop, str, str], AsyncOpenAI] = {}


@dataclass(slots=True)
class ProviderUsage:
    model: str
    input_tokens: int
    output_tokens: int
    cost_cny: float
    metric: str = "tokens"
    quantity: float = 0
    unit: str = "token"
    priced: bool | None = None

    def __post_init__(self) -> None:
        if self.priced is None:
            if self.metric == "tokens":
                self.priced = self.model in TOKEN_PRICES
            elif self.metric == "audio_seconds":
                self.priced = self.model in ASR_CNY_PER_SECOND
            else:
                self.priced = False
        if not self.priced:
            warning_key = (self.metric, self.model)
            if warning_key not in _UNPRICED_MODELS_WARNED:
                _UNPRICED_MODELS_WARNED.add(warning_key)
                logger.warning(
                    "模型 {} 的 {} 用量没有已知价格；将记录为 unpriced，不能视为免费",
                    self.model,
                    self.metric,
                )


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    text: str
    segments: tuple[TranscriptSegment, ...] = ()


def _shared_openai_client(api_key: str, base_url: str | None = None) -> AsyncOpenAI:
    resolved = base_url or settings.dashscope_base_url
    key = (asyncio.get_running_loop(), resolved, api_key)
    client = _OPENAI_CLIENTS.get(key)
    if client is None:
        client = AsyncOpenAI(api_key=api_key, base_url=resolved)
        _OPENAI_CLIENTS[key] = client
    return client


async def close_provider_clients() -> None:
    """Close every shared HTTP pool after request workers have stopped."""

    clients = list(dict.fromkeys(_OPENAI_CLIENTS.values()))
    _OPENAI_CLIENTS.clear()
    for client in clients:
        try:  # each client must close independently.
            await client.close()
        except Exception as exc:  # noqa: PERF203
            logger.warning("关闭模型 HTTP 客户端失败: {}", type(exc).__name__)


class BaseProvider:
    """Minimal contract for RAG-facing model calls (embedding + chat).

    ``DashScopeProvider`` in the package ``__init__`` extends this with the
    DashScope-only capabilities (ASR, OCR, keyframe planning, summarization).
    """

    api_key: str
    embedding_model: str
    chat_model: str

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], ProviderUsage]:
        raise NotImplementedError

    async def chat(
        self, question: str, context: str, *, model: str | None = None
    ) -> tuple[str, ProviderUsage]:
        raise NotImplementedError

    async def chat_stream(
        self, question: str, context: str, *, model: str
    ) -> AsyncIterator[tuple[str, str | ProviderUsage]]:
        raise NotImplementedError


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI-compatible chat/embedding calls against any compatible base URL."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        embedding_model: str | None = None,
        chat_model: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("尚未配置模型 API Key")
        self.api_key = api_key
        self.base_url = base_url or settings.dashscope_base_url
        self.embedding_model = embedding_model or settings.embedding_model
        self.chat_model = chat_model or settings.chat_model
        self.client = _shared_openai_client(api_key, self.base_url)

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], ProviderUsage]:
        if not texts:
            return [], ProviderUsage(self.embedding_model, 0, 0, 0)
        vectors: list[list[float]] = []
        input_tokens = 0
        for start in range(0, len(texts), 10):
            response = await self.client.embeddings.create(
                model=self.embedding_model,
                input=texts[start : start + 10],
                dimensions=settings.embedding_dimensions,
            )
            vectors.extend(item.embedding for item in response.data)
            input_tokens += int(getattr(response.usage, "prompt_tokens", 0) or 0)
        return vectors, ProviderUsage(
            self.embedding_model,
            input_tokens,
            0,
            token_cost(self.embedding_model, input_tokens),
            quantity=input_tokens,
        )

    @staticmethod
    def _answer_system_prompt() -> str:
        return (
            "只根据给定收藏知识回答，证据不足时明确说不知道，并使用[来源N]标注依据。"
            "使用清晰、通俗的中文 Markdown；可以使用标题、列表和表格。"
            "行内公式只能使用 $...$，独立公式只能使用 $$...$$；绝对不要使用 \\(...\\) 或 \\[...\\]。"
            "出现数学公式时必须紧接着用普通中文解释公式中每个关键符号和结论，"
            "不要只给公式或堆叠专业术语。"
        )

    async def chat(
        self, question: str, context: str, *, model: str | None = None
    ) -> tuple[str, ProviderUsage]:
        selected_model = model or self.chat_model
        response = await self.client.chat.completions.create(
            model=selected_model,
            messages=[
                {
                    "role": "system",
                    "content": self._answer_system_prompt(),
                },
                {
                    "role": "user",
                    "content": f"问题：{question}\n\n收藏知识：\n{context}",
                },
            ],
            temperature=0.2,
            max_tokens=2048,
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return text.strip(), ProviderUsage(
            selected_model,
            input_tokens,
            output_tokens,
            token_cost(selected_model, input_tokens, output_tokens),
            quantity=input_tokens + output_tokens,
        )

    async def chat_stream(
        self, question: str, context: str, *, model: str
    ) -> AsyncIterator[tuple[str, str | ProviderUsage]]:
        stream = await self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": self._answer_system_prompt()},
                {
                    "role": "user",
                    "content": f"问题：{question}\n\n收藏知识：\n{context}",
                },
            ],
            temperature=0.2,
            max_tokens=2048,
            stream=True,
            stream_options={"include_usage": True},
        )
        input_tokens = 0
        output_tokens = 0
        output_characters = 0
        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            choices = getattr(chunk, "choices", None) or []
            if choices:
                delta = getattr(choices[0].delta, "content", None)
                if delta:
                    output_characters += len(delta)
                    yield "delta", delta
        if input_tokens <= 0:
            input_tokens = max(1, (len(question) + len(context)) // 2)
        if output_tokens <= 0 and output_characters:
            output_tokens = max(1, output_characters // 2)
        yield "usage", ProviderUsage(
            model,
            input_tokens,
            output_tokens,
            token_cost(model, input_tokens, output_tokens),
            quantity=input_tokens + output_tokens,
        )
