export interface Project {
  id: string
  name: string
  created_at: string
  updated_at: string
}

export interface ProjectListResponse {
  count: number
  projects: Project[]
}

export interface CreateProjectRequest {
  name: string
  config_path?: string | null
}

export interface OpenProjectRequest {
  config_path?: string | null
}

export interface ProjectStatus {
  project_id: string
  framework: string
  display_name: string
  store_exists: boolean
  has_bundle: boolean
  total_mappings?: number | null
  approved?: number | null
  pending_review?: number | null
  ignored?: number | null
  last_run?: Record<string, unknown> | null
}

export type RunState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface RunRecord {
  schema_version: number
  id: string
  project_id: string
  state: RunState
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at?: string | null
  error_type?: string | null
  error_message?: string | null
  dropped_event_count: number
}

export interface StartRunRequest {
  config_path?: string | null
  distribute: boolean
}

export interface RunResponse {
  run: RunRecord
}

export interface RunListResponse {
  count: number
  runs: RunRecord[]
}

export interface PipelineEvent {
  schema_version: number
  type: string
  run_id: string
  sequence: number
  timestamp: string
  stage?: string | null
  message: string
  summary: Record<string, boolean | number | string | null>
}

export interface RunEventsResponse {
  count: number
  events: PipelineEvent[]
  dropped_event_count: number
  latest_sequence?: number | null
  terminal_state?: RunState | null
}

export interface UploadSourceRequest {
  config_path?: string | null
  filename: string
  content_type?: string | null
  content: string
}

export interface IngestUrlRequest {
  config_path?: string | null
  url: string
  timeout_seconds?: number
}

export interface IngestSourceResponse {
  source_id: string
  filename: string
  content_type: string
  size_bytes: number
  rows: number
  columns: number
  sha256: string
  project_path: string
}
