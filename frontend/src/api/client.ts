import type {
  ArtifactInventoryResponse,
  ArtifactPreviewResponse,
  CreateProjectRequest,
  GuidanceRequest,
  GuidanceResponse,
  IngestSourceResponse,
  IngestUrlRequest,
  MappingMutationResponse,
  OpenProjectRequest,
  PipelineEvent,
  Project,
  ProjectListResponse,
  ProjectStatus,
  RunEventsResponse,
  RunListResponse,
  RunRecord,
  RunResponse,
  StartRunRequest,
  UploadSourceRequest,
  ReviewResponse,
} from './contracts'

const SESSION_HEADER = 'X-CT-Session-Token'
const SAFE_ERROR_MESSAGES: Record<number, string> = {
  400: 'The request could not be processed. Check the project details and try again.',
  401: 'The session token was not accepted. Paste the current local API token and reconnect.',
  404: 'The project could not be found. Refresh the dashboard and try again.',
  409: 'The project is busy. Wait for the current operation to finish and try again.',
  413: 'The request was too large.',
  422: 'Some project details were invalid. Check the form and try again.',
}

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message)
    this.name = 'ApiClientError'
  }
}

export interface ApiClientOptions {
  baseUrl: string
  getSessionToken: () => string
  fetchImpl?: typeof fetch
}

export class ApiClient {
  private readonly baseUrl: string
  private readonly getSessionToken: () => string
  private readonly fetchImpl: typeof fetch

  constructor({ baseUrl, getSessionToken, fetchImpl = fetch }: ApiClientOptions) {
    this.baseUrl = baseUrl
    this.getSessionToken = getSessionToken
    this.fetchImpl = (input, init) => fetchImpl(input, init)
  }

  async listProjects(): Promise<Project[]> {
    const body = await this.request<ProjectListResponse>('/api/v1/projects')
    return body.projects
  }

  async createProject(request: CreateProjectRequest): Promise<Project> {
    return this.request<Project>('/api/v1/projects', {
      method: 'POST',
      body: request,
    })
  }

  async openProject(projectId: string, request: OpenProjectRequest): Promise<ProjectStatus> {
    return this.request<ProjectStatus>(`/api/v1/projects/${encodeURIComponent(projectId)}/open`, {
      method: 'POST',
      body: request,
    })
  }

  async deleteProject(projectId: string): Promise<void> {
    await this.request<void>(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
      method: 'DELETE',
      expectJson: false,
    })
  }

  async uploadSource(projectId: string, request: UploadSourceRequest): Promise<IngestSourceResponse> {
    return this.request<IngestSourceResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/sources/upload`, {
      method: 'POST',
      body: request,
    })
  }

  async ingestUrlSource(projectId: string, request: IngestUrlRequest): Promise<IngestSourceResponse> {
    return this.request<IngestSourceResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/sources/url`, {
      method: 'POST',
      body: request,
    })
  }

  async startRun(projectId: string, request: StartRunRequest): Promise<RunRecord> {
    const body = await this.request<RunResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/runs`, {
      method: 'POST',
      body: request,
    })
    return body.run
  }

  async listRuns(projectId: string): Promise<RunRecord[]> {
    const body = await this.request<RunListResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/runs`)
    return body.runs
  }

  async getRun(projectId: string, runId: string): Promise<RunRecord> {
    const body = await this.request<RunResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}`,
    )
    return body.run
  }

  async getRunEvents(projectId: string, runId: string, afterSequence?: number): Promise<RunEventsResponse> {
    const query = typeof afterSequence === 'number' ? `?after_sequence=${afterSequence}` : ''
    const body = await this.request<RunEventsResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/events${query}`,
    )
    return {
      ...body,
      events: orderedUniqueEvents(body.events),
    }
  }

  async cancelRun(projectId: string, runId: string): Promise<void> {
    await this.request<void>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/cancel`,
      {
        method: 'POST',
        expectJson: false,
      },
    )
  }

  async reviewMappings(projectId: string, configPath: string | null, query = '', status = 'review', page = 1, pageSize = 10): Promise<ReviewResponse> {
    return this.request<ReviewResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/review${queryString({ config_path: configPath, query, status, page: String(page), page_size: String(pageSize) })}`,
    )
  }

  async mutateMappings(projectId: string, configPath: string | null, action: 'approve' | 'reject', controlIds: string[]): Promise<MappingMutationResponse> {
    return this.request<MappingMutationResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/review/${action}${queryString({ config_path: configPath })}`,
      { method: 'POST', body: { control_ids: controlIds } },
    )
  }

  async listGuidance(projectId: string, configPath: string | null): Promise<GuidanceResponse> {
    return this.request<GuidanceResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/guidance${queryString({ config_path: configPath })}`)
  }

  async saveGuidance(projectId: string, configPath: string | null, request: GuidanceRequest): Promise<GuidanceResponse> {
    return this.request<GuidanceResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/guidance${queryString({ config_path: configPath })}`,
      { method: 'POST', body: request },
    )
  }

  async deleteGuidance(projectId: string, configPath: string | null, ids: string[]): Promise<GuidanceResponse> {
    return this.request<GuidanceResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/guidance/delete${queryString({ config_path: configPath })}`,
      { method: 'POST', body: { ids } },
    )
  }

  async addToOos(projectId: string, configPath: string | null, policyIds: string[], reasons: string[]): Promise<{ added: string[] }> {
    return this.request<{ added: string[] }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/oos${queryString({ config_path: configPath })}`,
      { method: 'POST', body: { policy_ids: policyIds, reasons, register_name: 'global' } },
    )
  }

  async reconsiderOos(projectId: string, configPath: string | null, policyIds: string[]): Promise<{ removed: string[]; not_found: string[] }> {
    return this.request<{ removed: string[]; not_found: string[] }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/oos/reconsider${queryString({ config_path: configPath })}`,
      { method: 'POST', body: { policy_ids: policyIds } },
    )
  }

  async artifactInventory(projectId: string, configPath: string | null): Promise<ArtifactInventoryResponse> {
    return this.request<ArtifactInventoryResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/artifacts/inventory${queryString({ config_path: configPath })}`)
  }

  async artifactPreview(projectId: string, configPath: string | null, name: string): Promise<ArtifactPreviewResponse> {
    return this.request<ArtifactPreviewResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(name)}/preview${queryString({ config_path: configPath })}`,
    )
  }

  async downloadArtifact(projectId: string, configPath: string | null, name: string): Promise<Blob> {
    const token = this.getSessionToken().trim()
    if (!token) {
      throw new ApiClientError('Connect with the local session token before using the dashboard.', 401)
    }
    const baseUrl = normalizeBaseUrl(this.baseUrl)
    const headers = new Headers()
    headers.set(SESSION_HEADER, token)
    const response = await this.fetchImpl(
      `${baseUrl}/api/v1/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(name)}/download${queryString({ config_path: configPath })}`,
      { headers },
    )
    if (!response.ok) {
      throw await toSafeError(response)
    }
    return response.blob()
  }

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const token = this.getSessionToken().trim()
    if (!token) {
      throw new ApiClientError('Connect with the local session token before using the dashboard.', 401)
    }
    const baseUrl = normalizeBaseUrl(this.baseUrl)

    const headers = new Headers(options.body === undefined ? undefined : { 'Content-Type': 'application/json' })
    headers.set(SESSION_HEADER, token)

    let response: Response
    try {
      response = await this.fetchImpl(`${baseUrl}${path}`, {
        method: options.method ?? 'GET',
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      })
    } catch {
      throw new ApiClientError('The local API is not reachable. Start ct-api and try again.', 0)
    }

    if (!response.ok) {
      throw await toSafeError(response)
    }

    if (options.expectJson === false || response.status === 204) {
      return undefined as T
    }

    return (await response.json()) as T
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'DELETE'
  body?: unknown
  expectJson?: boolean
}

async function toSafeError(response: Response): Promise<ApiClientError> {
  let code: string | undefined
  try {
    const payload = (await response.json()) as { error?: { code?: unknown } }
    code = typeof payload.error?.code === 'string' ? payload.error.code : undefined
  } catch {
    code = undefined
  }
  return new ApiClientError(SAFE_ERROR_MESSAGES[response.status] ?? 'The local API returned an error.', response.status, code)
}

function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, '')
  const candidate = trimmed || window.location.origin
  let url: URL
  try {
    url = new URL(candidate)
  } catch {
    throw invalidOriginError()
  }

  const isLoopback = url.hostname === '127.0.0.1' || url.hostname === 'localhost' || url.hostname === '[::1]'
  const isOriginOnly = url.pathname === '/' && !url.search && !url.hash
  if (url.protocol !== 'http:' || !isLoopback || !isOriginOnly || url.username || url.password) {
    throw invalidOriginError()
  }
  return url.origin
}

function invalidOriginError(): ApiClientError {
  return new ApiClientError(
    'Use the same-origin connection or an HTTP loopback origin such as http://127.0.0.1:8756.',
    0,
    'invalid_api_origin',
  )
}

function orderedUniqueEvents(events: PipelineEvent[]): PipelineEvent[] {
  const seen = new Set<number>()
  return [...events]
    .sort((left, right) => left.sequence - right.sequence)
    .filter((event) => {
      if (seen.has(event.sequence)) {
        return false
      }
      seen.add(event.sequence)
      return true
    })
}

function queryString(values: Record<string, string | number | null | undefined>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== null && value !== undefined && String(value).trim() !== '') {
      params.set(key, String(value))
    }
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}
