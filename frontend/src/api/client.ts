import type {
  CreateProjectRequest,
  OpenProjectRequest,
  Project,
  ProjectListResponse,
  ProjectStatus,
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
    this.fetchImpl = fetchImpl
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
