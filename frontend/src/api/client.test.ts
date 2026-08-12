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

    expect(fetchImpl.mock.calls[0][0]).toBe(`${window.location.origin}/api/v1/projects`)
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
