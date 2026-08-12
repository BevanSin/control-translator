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
