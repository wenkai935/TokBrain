export type Probe = {
  probe: string;
  status: "healthy" | "degraded" | "down";
  message: string;
  details: Record<string, unknown> & {
    coordinators?: Array<{
      name: string;
      alive: boolean;
      workers_alive: number;
      workers_expected: number;
      last_error: string | null;
    }>;
  };
};

export type Health = {
  overall: "healthy" | "degraded" | "down";
  summary: string;
  checked_at: string;
  probes: Probe[];
};

export type RuntimeSettings = {
  daily_media_minutes_limit: number;
  daily_llm_token_limit: number;
  monthly_warning_cny: number;
  scene_threshold: number;
  max_scene_candidates: number;
  max_keyframes: number;
  min_keyframe_gap_seconds: number;
  dpapi_warning: string;
  has_dashscope_key: boolean;
  has_bss_credentials: boolean;
  has_f2_cookie: boolean;
  security_cleanup_required: boolean;
  security_cleanup_message: string;
  summary_prompt: string;
  default_summary_prompt: string;
  processing_model: string;
  processing_model_options: string[];
  chat_fast_model: string;
  chat_deep_model: string;
  chat_model_options: string[];
  default_answer_format: string;
  ocr_model: string;
  asr_model: string;
  embedding_model: string;
  import_batch_limit: number;
  import_daily_limit: number;
  import_worker_count: number;
  import_network_concurrency: number;
  import_cooldown_min_seconds: number;
  import_cooldown_max_seconds: number;
};

export type RuntimeSettingsUpdate = Partial<Pick<RuntimeSettings,
  | "daily_media_minutes_limit"
  | "daily_llm_token_limit"
  | "monthly_warning_cny"
  | "scene_threshold"
  | "max_scene_candidates"
  | "max_keyframes"
  | "min_keyframe_gap_seconds"
  | "summary_prompt"
  | "processing_model"
  | "chat_fast_model"
  | "chat_deep_model"
  | "default_answer_format"
>> & {
  dashscope_api_key?: string;
  bss_access_key_id?: string;
  bss_access_key_secret?: string;
  f2_cookie?: string;
  clear_f2_cookie?: boolean;
};

export type ModelProviderType = "dashscope" | "openai";

export type ModelProfile = {
  id: string;
  name: string;
  provider_type: ModelProviderType;
  base_url: string | null;
  builtin: boolean;
  ocr_model: string;
  asr_model: string;
  embedding_model: string;
  enrichment_model: string;
  chat_model: string;
  rerank_model: string;
  has_api_key: boolean;
  is_active: boolean;
  created_at: string;
};

export type ModelProfilesView = {
  active_id: string;
  items: ModelProfile[];
  default_profile: ModelProfile;
};

export type ModelProfilePayload = {
  name?: string;
  provider_type?: ModelProviderType;
  base_url?: string;
  api_key?: string;
  ocr_model?: string;
  asr_model?: string;
  embedding_model?: string;
  enrichment_model?: string;
  chat_model?: string;
  rerank_model?: string;
};

export type Usage = {
  month_estimated_cny: number;
  official_billed_cny: number | null;
  official_data_as_of: string | null;
  official_status: string;
  daily_works_used: number;
  daily_works_reserved: number;
  daily_links_used: number;
  daily_links_limit: number;
  daily_media_minutes_used: number;
  daily_media_minutes_reserved: number;
  daily_media_minutes_limit: number;
  daily_llm_tokens_used: number;
  daily_llm_tokens_reserved: number;
  daily_llm_tokens_limit: number;
  warning_reached: boolean;
  estimate_notice: string;
};

export type Job = {
  id: string;
  job_type: string;
  state: string;
  message: string;
  total_items: number;
  processed_items: number;
  failed_items: number;
  cancelled_items: number;
  deferred_items: number;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  progress: Record<string, unknown>;
};

export type ImportItem = {
  id: number;
  client_item_id: string | null;
  platform: string;
  ordinal: number;
  input_url: string;
  normalized_url: string;
  canonical_url: string | null;
  platform_work_id: string | null;
  kind: string | null;
  title: string | null;
  author_name: string | null;
  duration_seconds: number;
  cover_url: string | null;
  status: string;
  error_code: string | null;
  error_message: string | null;
  existing_work_id: number | null;
  worker_id: number | null;
  local_asset_count: number;
  has_public_media: boolean;
  download_permission: "allowed" | "denied" | "unknown";
  processing_mode: "full_media" | "subtitle_or_audio";
  has_audio_or_subtitle: boolean;
  target_collection_id: number | null;
};

export type ImportBatch = {
  id: string;
  source_type: "link" | "local_upload" | "external_batch" | "package_upload";
  job_id: string;
  state: string;
  total_items: number;
  cancel_requested: boolean;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  progress: Record<string, number>;
  workers: Array<{ worker_id: number; item_id: number; title: string }>;
  remaining_daily: number;
  daily_limit: number;
  circuit: {
    active: boolean;
    expires_at: string | null;
    error_code: string | null;
    message: string | null;
  };
  package?: {
    upload_mode: "folder" | "zip";
    analysis_state: string;
    uploaded_count: number;
    file_count: number;
    target_collection_id: number | null;
  };
  package_files?: PackageImportFile[];
  items: ImportItem[];
};

export type ImportBatchCreateRequest = {
  text: string;
};

export type PackageImportFile = {
  id: string;
  client_file_id: string;
  relative_path: string;
  role: "video" | "metadata" | "archive" | "unknown";
  status: string;
  declared_size: number;
  size_bytes: number;
  sha256: string | null;
  error_code: string | null;
  error_message: string | null;
  upload_url: string | null;
};

export type PackageImportBatchRequest = {
  rights_attested: true;
  upload_mode: "folder" | "zip";
  target_collection_id?: number;
  files: Array<{
    client_file_id: string;
    relative_path: string;
    size_bytes: number;
  }>;
};

export type ImportBatchCreated = {
  batch_id: string;
  job_id: string;
  accepted_count: number;
  rejected_count: number;
  queued_count: number;
  duplicate_count: number;
};

export type LocalImportBatchRequest = {
  rights_attested: true;
  items: Array<{
    client_item_id: string;
    filename: string;
    size_bytes: number;
    target_collection_id?: number;
  }>;
};

export type LocalVideoUploadResult = {
  batch_id: string;
  item_id: number;
  status: string;
  kind: string;
  duration_seconds: number;
  sha256: string;
  budget_estimate?: { media_minutes: number; llm_tokens: number } | null;
  existing_work_id?: number | null;
};

export type ImportItemUpdate = {
  title?: string;
  description?: string;
  target_collection_id?: number | null;
};

export type ImportItemUpdateResult = {
  item_id: number;
  title: string | null;
  description: string | null;
  target_collection_id: number | null;
};

export type IntegrationTokenStatus = {
  configured: boolean;
  prefix: string | null;
  created_at: string | null;
};

export type PlatformId = "douyin" | "bilibili" | "xiaohongshu";

export type PlatformStatus = {
  platform: PlatformId;
  connected: boolean;
};

export type PlatformPullResult = {
  platform: PlatformId;
  pulled_count: number;
  items?: Array<{
    platform_work_id: string;
    source_url: string;
    title?: string | null;
    author_name?: string | null;
    cover_url?: string | null;
  }>;
  batch_id?: string;
  enqueued?: boolean;
  note?: string;
};

export type IntegrationTokenCreated = IntegrationTokenStatus & {
  token: string;
};

export type Collection = {
  id: number;
  key: string;
  title: string;
  cover_url?: string | null;
  summary_prompt?: string | null;
  item_count: number;
  local_item_count: number;
  pending_count: number;
  issue_count: number;
};

export type ImportConfirmation = {
  confirmed_count: number;
  work_ids: number[];
  library_state: "pending";
};

export type LibrarySummary = {
  candidate_count: number;
  selected_count: number;
  local_item_count: number;
  issue_count: number;
  archived_count: number;
  supplement_count?: number;
  known_distinct_count: number;
  remote_folder_item_sum: number;
};

export type SupplementState =
  | "none"
  | "required"
  | "uploaded"
  | "processing"
  | "failed";

export type EvidenceState = "unverified" | "sufficient" | "insufficient";

export type TrackReport = Record<string, unknown>;

export type Work = {
  id: number;
  platform: string;
  platform_work_id: string;
  kind: string;
  import_source?: string | null;
  title: string;
  author_name?: string | null;
  duration_seconds: number;
  cover_url?: string | null;
  source_url?: string | null;
  processing_state: string;
  library_state: string;
  selected: boolean;
  process_error?: string | null;
  error_code?: string | null;
  last_seen_at: string;
  collections: string[];
  summary_state: "missing" | "ready" | "failed" | "generating";
  summary_excerpt?: string | null;
  supplement_state?: SupplementState;
  supplement_reason?: string | null;
  evidence_state?: EvidenceState;
  track_report?: TrackReport | null;
};

export type WorkSupplementResult = {
  id: number;
  library_state: string;
  supplement_state: SupplementState;
  supplement_reason?: string | null;
  reason?: string | null;
  evidence_state: EvidenceState;
  track_report: TrackReport;
  asset_count: number;
  sha256: string[];
  duration_seconds: number;
  idempotent: boolean;
  job: Job;
};

export type WorksPage = {
  items: Work[];
  total: number;
  selected_count: number;
  account_selected_count: number;
  next_offset: number | null;
};

export type WorkSummaryDetail = {
  work: {
    id: number;
    platform_work_id: string;
    title: string;
    author_name?: string | null;
    cover_url?: string | null;
    source_url?: string | null;
    kind: string;
    duration_seconds: number;
    collections: string[];
  };
  summary: {
    status: string;
    one_sentence: string;
    sections: Array<{ kind: string; title: string; body: string }>;
    tags: string[];
    asset_ids: string[];
    generated_at?: string | null;
    model: string;
  };
  assets: Array<{ name: string; url: string }>;
};

export type ObsidianManifest = {
  items: Array<{
    work_id: number;
    platform_work_id: string;
    filename: string;
    markdown: string;
    assets: Array<{ name: string; export_name: string; url: string }>;
  }>;
};
