import { describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiClientError } from './client'

const project = {
  id: 'a1a9f47e-9963-4e23-8fd2-0a4f04513e85',
  name: 'NZISM',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

describe('ApiClient', () => {
  it('uses the current loopback origin when no explicit origin is configured', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ count: 0, projects: [] }))
    const client = new ApiClient({ baseUrl: '', getSessionToken: () => 'token', fetchImpl: fetchImpl as unknown as typeof fetch })

    await client.listProjects()

    const [url] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe(`${window.location.origin}/api/v1/projects`)
  })

  it('invokes browser fetch without rebinding its receiver', async () => {
    const fetchImpl = vi.fn(function (this: unknown) {
      if (this !== undefined) {
        throw new TypeError('Illegal invocation')
      }
      return Promise.resolve(jsonResponse({ count: 0, projects: [] }))
    })
    const client = new ApiClient({
      baseUrl: '',
      getSessionToken: () => 'token',
      fetchImpl: fetchImpl as unknown as typeof fetch,
    })

    await expect(client.listProjects()).resolves.toEqual([])
  })

  it('uses typed project endpoints with the session token header only', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ count: 1, projects: [project] }))
    const client = new ApiClient({ baseUrl: 'http://127.0.0.1:8756/', getSessionToken: () => 'secret-token', fetchImpl: fetchImpl as unknown as typeof fetch })

    await expect(client.listProjects()).resolves.toEqual([project])

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://127.0.0.1:8756/api/v1/projects')
    expect(String(url)).not.toContain('secret-token')
    expect((init.headers as Headers).get('X-CT-Session-Token')).toBe('secret-token')
  })

  it('sends create, open, and delete requests using the API contract', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(project, { status: 201 }))
      .mockResolvedValueOnce(jsonResponse({ project_id: project.id, framework: 'sample', display_name: 'Sample', store_exists: true, has_bundle: false }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    const client = new ApiClient({ baseUrl: 'http://127.0.0.1:8756', getSessionToken: () => 'token', fetchImpl: fetchImpl as unknown as typeof fetch })

    await client.createProject({ name: 'NZISM', config_path: 'config/sample.json' })
    await client.openProject(project.id, { config_path: 'config/sample.json' })
    await client.deleteProject(project.id)

    expect(fetchImpl.mock.calls[0][1]!.method).toBe('POST')
    expect(fetchImpl.mock.calls[0][1]!.body).toBe(JSON.stringify({ name: 'NZISM', config_path: 'config/sample.json' }))
    expect(fetchImpl.mock.calls[1][0]).toContain(`/api/v1/projects/${project.id}/open`)
    expect(fetchImpl.mock.calls[2][1]!.method).toBe('DELETE')
  })

  it('uses typed source, run, event, and cancellation endpoints', async () => {
    const run = {
      schema_version: 1,
      id: 'f'.repeat(32),
      project_id: project.id,
      state: 'running',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:01Z',
      started_at: '2026-01-01T00:00:01Z',
      finished_at: null,
      error_type: null,
      error_message: null,
      dropped_event_count: 0,
    }
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ source_id: 's1', filename: 'standard.csv', content_type: 'text/csv', size_bytes: 3, rows: 1, columns: 2, sha256: 'abc', project_path: 'source/standard.csv' }, { status: 201 }))
      .mockResolvedValueOnce(jsonResponse({ run }, { status: 202 }))
      .mockResolvedValueOnce(jsonResponse({ count: 1, runs: [run] }))
      .mockResolvedValueOnce(jsonResponse({ run }))
      .mockResolvedValueOnce(jsonResponse({
        count: 2,
        dropped_event_count: 0,
        latest_sequence: 2,
        terminal_state: null,
        events: [
          { schema_version: 1, type: 'stage.completed', run_id: run.id, sequence: 2, timestamp: run.updated_at, stage: 'ingest', message: 'Ingest completed', summary: {} },
          { schema_version: 1, type: 'stage.started', run_id: run.id, sequence: 1, timestamp: run.updated_at, stage: 'ingest', message: 'Ingest started', summary: {} },
          { schema_version: 1, type: 'stage.started', run_id: run.id, sequence: 1, timestamp: run.updated_at, stage: 'ingest', message: 'Duplicate', summary: {} },
        ],
      }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    const client = new ApiClient({ baseUrl: 'http://127.0.0.1:8756', getSessionToken: () => 'token', fetchImpl: fetchImpl as unknown as typeof fetch })

    await client.uploadSource(project.id, { config_path: 'config/sample.json', filename: 'standard.csv', content_type: 'text/csv', content: 'YSxi' })
    await client.startRun(project.id, { config_path: 'config/sample.json', distribute: true })
    await client.listRuns(project.id)
    await client.getRun(project.id, run.id)
    const events = await client.getRunEvents(project.id, run.id, 0)
    await client.cancelRun(project.id, run.id)

    expect(fetchImpl.mock.calls[0][0]).toContain(`/api/v1/projects/${project.id}/sources/upload`)
    expect(fetchImpl.mock.calls[1][0]).toContain(`/api/v1/projects/${project.id}/runs`)
    expect(fetchImpl.mock.calls[4][0]).toContain(`after_sequence=0`)
    expect(events.events.map((event) => event.sequence)).toEqual([1, 2])
    expect(fetchImpl.mock.calls[5][0]).toContain(`/cancel`)
  })

  it('maps backend errors to safe messages without rendering raw details', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ error: { code: 'invalid_project_or_config', message: '/home/user/private/config.json missing' } }, { status: 400 }))
    const client = new ApiClient({ baseUrl: 'http://127.0.0.1:8756', getSessionToken: () => 'token', fetchImpl: fetchImpl as unknown as typeof fetch })

    await expect(client.listProjects()).rejects.toMatchObject({
      status: 400,
      code: 'invalid_project_or_config',
      message: 'The request could not be processed. Check the project details and try again.',
    } satisfies Partial<ApiClientError>)
  })

  it.each([
    'https://127.0.0.1:8756',
    'http://example.com:8756',
    'http://127.0.0.1.evil.example:8756',
    'http://user:password@127.0.0.1:8756',
    'http://127.0.0.1:8756/prefix',
    'http://127.0.0.1:8756?redirect=evil',
  ])('rejects unsafe API origin %s before sending the token', async (baseUrl) => {
    const fetchImpl = vi.fn()
    const client = new ApiClient({ baseUrl, getSessionToken: () => 'secret-token', fetchImpl: fetchImpl as unknown as typeof fetch })

    await expect(client.listProjects()).rejects.toMatchObject({
      code: 'invalid_api_origin',
    } satisfies Partial<ApiClientError>)
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}
