"""Public API schemas for the local import application."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SupplementState = Literal["none", "required", "uploaded", "processing", "failed"]
EvidenceState = Literal["unverified", "sufficient", "insufficient"]


class SettingsView(BaseModel):
    daily_media_minutes_limit: float
    daily_llm_token_limit: int
    monthly_warning_cny: float
    scene_threshold: float
    max_scene_candidates: int
    max_keyframes: int
    min_keyframe_gap_seconds: float
    dpapi_warning: str
    has_dashscope_key: bool
    has_bss_credentials: bool
    has_f2_cookie: bool = False  # deprecated: Douyin discovery moved to zhicui (douyin_cookie)
    has_douyin_cookie: bool = False
    has_bilibili_cookie: bool = False
    has_xhs_cookie: bool = False
    security_cleanup_required: bool = False
    security_cleanup_message: str = ""
    summary_prompt: str
    default_summary_prompt: str
    processing_model: str
    processing_model_options: list[str] = Field(default_factory=list)
    chat_fast_model: str
    chat_deep_model: str
    chat_model_options: list[str] = Field(default_factory=list)
    default_answer_format: str
    ocr_model: str
    asr_model: str
    embedding_model: str
    import_batch_limit: int = 100
    import_daily_limit: int = 150
    import_worker_count: int = 3
    import_network_concurrency: int = 1
    import_cooldown_min_seconds: int = 4
    import_cooldown_max_seconds: int = 8


class SettingsUpdate(BaseModel):
    daily_media_minutes_limit: float | None = Field(default=None, ge=1, le=100000)
    daily_llm_token_limit: int | None = Field(default=None, ge=1000)
    monthly_warning_cny: float | None = Field(default=None, ge=0)
    scene_threshold: float | None = Field(default=None, ge=0.05, le=0.95)
    max_scene_candidates: int | None = Field(default=None, ge=12, le=1000)
    max_keyframes: int | None = Field(default=None, ge=1, le=48)
    min_keyframe_gap_seconds: float | None = Field(default=None, ge=0.2, le=60)
    dashscope_api_key: str | None = Field(default=None, max_length=500)
    bss_access_key_id: str | None = Field(default=None, max_length=200)
    bss_access_key_secret: str | None = Field(default=None, max_length=500)
    f2_cookie: str | None = Field(default=None, max_length=20000)  # deprecated
    clear_f2_cookie: bool = False
    douyin_cookie: str | None = Field(default=None, max_length=20000)
    clear_douyin_cookie: bool = False
    bilibili_cookie: str | None = Field(default=None, max_length=20000)
    clear_bilibili_cookie: bool = False
    xhs_cookie: str | None = Field(default=None, max_length=20000)
    clear_xhs_cookie: bool = False
    summary_prompt: str | None = Field(default=None, min_length=1, max_length=12000)
    processing_model: str | None = Field(default=None, min_length=1, max_length=100)
    chat_fast_model: str | None = Field(default=None, min_length=1, max_length=100)
    chat_deep_model: str | None = Field(default=None, min_length=1, max_length=100)
    default_answer_format: str | None = Field(default=None, max_length=20)
    import_batch_limit: int | None = Field(default=None, ge=1, le=1000)
    import_daily_limit: int | None = Field(default=None, ge=1, le=100000)
    inbox_batch_limit: int | None = Field(default=None, ge=1, le=1000)


class SystemProbe(BaseModel):
    probe: str
    status: Literal["healthy", "degraded", "down"]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class SystemHealth(BaseModel):
    overall: Literal["healthy", "degraded", "down"]
    summary: str
    checked_at: datetime
    probes: list[SystemProbe]


class UsageSummary(BaseModel):
    month_estimated_cny: float
    official_billed_cny: float | None = None
    official_data_as_of: datetime | None = None
    official_status: str
    daily_works_used: int
    daily_works_reserved: int
    daily_links_used: int
    daily_links_limit: int
    daily_media_minutes_used: float
    daily_media_minutes_reserved: float
    daily_media_minutes_limit: float
    daily_llm_tokens_used: int
    daily_llm_tokens_reserved: int
    daily_llm_tokens_limit: int
    warning_reached: bool
    estimate_notice: str


class ImportBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=100000)
    # 仅链接入库（不访问抖音，避免风控熔断）：跳过预检网络解析，直接用长链 +
    # 可选元数据建 Work，后续可在网页补件。飞书→TokBrain 链路默认使用。
    metadata_only: bool = False
    title: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=200)


class ImportSelection(BaseModel):
    item_id: int
    collection_id: int


class ImportConfirm(BaseModel):
    # Kept for compatibility. Items without an explicit collection use 手动导入.
    item_ids: list[int] = Field(default_factory=list, max_length=100)
    items: list[ImportSelection] = Field(default_factory=list, max_length=100)


CLIENT_ITEM_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
DOUYIN_WORK_ID_PATTERN = r"^[0-9]{1,64}$"
SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"


class StrictImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalImportItemCreate(StrictImportRequest):
    client_item_id: str = Field(pattern=CLIENT_ITEM_PATTERN)
    filename: str = Field(min_length=1, max_length=260)
    size_bytes: int = Field(gt=0, le=1024 * 1024 * 1024)
    target_collection_id: int | None = Field(default=None, ge=1)


class LocalImportBatchCreate(StrictImportRequest):
    rights_attested: Literal[True]
    items: list[LocalImportItemCreate] = Field(min_length=1, max_length=10)


class PackageImportFileCreate(StrictImportRequest):
    client_file_id: str = Field(pattern=CLIENT_ITEM_PATTERN)
    relative_path: str = Field(min_length=1, max_length=1000)
    size_bytes: int = Field(gt=0, le=20 * 1024 * 1024 * 1024)


class PackageImportBatchCreate(StrictImportRequest):
    rights_attested: Literal[True]
    upload_mode: Literal["folder", "zip"]
    target_collection_id: int | None = Field(default=None, ge=1)
    files: list[PackageImportFileCreate] = Field(min_length=1, max_length=1000)


class ImportItemUpdate(StrictImportRequest):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20_000)
    target_collection_id: int | None = Field(default=None, ge=1)


class ImportItemsRetryRequest(StrictImportRequest):
    item_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=100)


class ExternalImportItemCreate(StrictImportRequest):
    client_item_id: str = Field(pattern=CLIENT_ITEM_PATTERN)
    platform_work_id: str = Field(pattern=DOUYIN_WORK_ID_PATTERN)
    video_pending: Literal[True]
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=20_000)
    author_id: str | None = Field(default=None, max_length=120)
    author_name: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    source_url: str | None = Field(default=None, max_length=2_000)
    duration_seconds: float | None = Field(default=None, ge=0, le=7_200)
    target_collection_id: int | None = Field(default=None, ge=1)
    expected_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    extra_metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalImportBatchCreate(StrictImportRequest):
    rights_attested: Literal[True]
    items: list[ExternalImportItemCreate] = Field(min_length=1, max_length=100)


class ExternalImportCommit(StrictImportRequest):
    start_processing: bool = False


class ImportErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool
    field: str | None = None


class BudgetEstimateView(BaseModel):
    media_minutes: float
    llm_tokens: int


class ExternalImportItemView(BaseModel):
    client_item_id: str
    item_id: int
    platform_work_id: str
    status: str
    existing_work_id: int | None = None
    expected_sha256: str | None = None
    upload_url: str | None = None
    error: ImportErrorDetail | None = None


class ExternalImportBatchView(BaseModel):
    batch_id: str
    state: str
    source_type: Literal["external_batch"]
    items: list[ExternalImportItemView]


class ExternalImportBatchCreated(ExternalImportBatchView):
    replayed: bool


class ExternalAssetUploadView(BaseModel):
    batch_id: str
    client_item_id: str
    item_id: int
    status: str
    duration_seconds: float
    sha256: str
    budget_estimate: BudgetEstimateView
    idempotent: bool


class ExternalCommitItemView(BaseModel):
    client_item_id: str
    status: Literal["imported", "duplicate", "invalid", "missing_video"]
    work_id: int | None = None
    error: ImportErrorDetail | None = None


class ExternalCommitJobView(BaseModel):
    job_id: str
    state: str


class ExternalCommitView(BaseModel):
    batch_id: str
    results: list[ExternalCommitItemView]
    work_ids: list[int]
    job: ExternalCommitJobView | None = None


class IntegrationTokenView(BaseModel):
    configured: bool
    prefix: str | None = None
    created_at: datetime | None = None


class IntegrationTokenCreated(IntegrationTokenView):
    token: str


class JobView(BaseModel):
    id: str
    job_type: str
    state: Literal[
        "uploading",
        "queued",
        "running",
        "cancelling",
        "cancelled",
        "succeeded",
        "partial",
        "failed",
    ]
    message: str
    total_items: int
    processed_items: int
    failed_items: int
    cancelled_items: int
    deferred_items: int
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    progress: dict[str, Any] = Field(default_factory=dict)


class WorkSupplementUploadView(BaseModel):
    id: int
    library_state: str
    supplement_state: SupplementState
    supplement_reason: str | None = None
    reason: str | None = None
    evidence_state: EvidenceState
    track_report: dict[str, Any] = Field(default_factory=dict)
    asset_count: int
    sha256: list[str] = Field(default_factory=list)
    duration_seconds: float = 0
    idempotent: bool = False
    job: JobView


class SummaryCreate(BaseModel):
    work_ids: list[int] = Field(min_length=1, max_length=1000)


class CollectionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class CollectionUpdate(BaseModel):
    summary_prompt: str | None = Field(default=None, max_length=12000)


class CollectionAssignment(BaseModel):
    work_ids: list[int] = Field(min_length=1, max_length=1000)


class IngestCreate(BaseModel):
    work_ids: list[int] = Field(min_length=1, max_length=1000)


class RetryBatchRequest(BaseModel):
    work_ids: list[int] | None = Field(default=None, max_length=1000)
    error_code: str | None = Field(default=None, max_length=50)
    collection_id: int | None = None


class ObsidianManifestRequest(BaseModel):
    work_ids: list[int] = Field(min_length=1, max_length=1000)


# ---------------------------------------------------------------------------
# Inbound inbox API (/api/integrations/v1/inbox/items)
# ---------------------------------------------------------------------------
INBOX_SOURCES = ("douyin_share", "local_text")


class InboxItemCreate(StrictImportRequest):
    model_config = ConfigDict(extra="forbid")

    # link defaults to "" so a missing link is reported as an invalid link
    # (HTTP 400) by the endpoint rather than a 422 schema error (TC-P4-10).
    link: str = Field(default="", max_length=2_000)
    tags: str = Field(default="", max_length=2_000)
    source: str = Field(default="douyin_share", pattern=r"^[a-z][a-z0-9_]{0,31}$")
    title: str | None = Field(default=None, max_length=500)


class InboxIngestCreate(StrictImportRequest):
    # rights_attested is intentionally a plain bool (default False) so the
    # endpoint can return a 400 with a clear message rather than a 422 when the
    # compliance flag is missing (TC-P4-06). It must be explicitly True.
    rights_attested: bool = False
    # The >100 cap is enforced in the endpoint (returns 400, atomic) rather than
    # via pydantic max_length, so the documented rejection shape is a 400.
    items: list[InboxItemCreate] = Field(min_length=1)


class InboxItemResult(BaseModel):
    record_id: str
    link: str
    normalized_link: str
    source: str
    status: str
    error: ImportErrorDetail | None = None


class InboxIngestResult(BaseModel):
    accepted: int
    duplicates: int = 0
    rejected: int
    items: list[InboxItemResult]
    replayed: bool = False
