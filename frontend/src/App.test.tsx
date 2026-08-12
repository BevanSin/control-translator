import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import App from './App'

const project = {
  id: 'a1a9f47e-9963-4e23-8fd2-0a4f04513e85',
  name: 'Sample framework',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

describe('project dashboard', () => {
  it('passes accessibility checks for the bootstrap screen', async () => {
    const { container } = render(<App />)

    expect(await axe(container)).toHaveNoViolations()
    expect(screen.getByRole('link', { name: /skip to project dashboard/i })).toHaveAttribute('href', '#main-content')
    expect(screen.getByRole('navigation', { name: /project sections/i })).toBeInTheDocument()
  })

  it('handles loading, empty, create, open, and delete project flows', async () => {
    const user = userEvent.setup()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 0, projects: [] }))
      .mockResolvedValueOnce(jsonResponse(project, { status: 201 }))
      .mockResolvedValueOnce(jsonResponse({
        project_id: project.id,
        framework: 'sample',
        display_name: 'Sample Controls',
        store_exists: true,
        has_bundle: false,
        total_mappings: 12,
        pending_review: 3,
      }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.clear(screen.getByLabelText(/api origin/i))
    await user.type(screen.getByLabelText(/api origin/i), 'http://127.0.0.1:8756')
    await user.type(screen.getByLabelText(/session token/i), 'tab-only-token')
    await user.click(screen.getByRole('button', { name: /connect/i }))

    expect(await screen.findByText(/no projects yet/i)).toBeInTheDocument()
    expect(fetchMock.mock.calls[0][0]).not.toContain('tab-only-token')
    expect((fetchMock.mock.calls[0][1]!.headers as Headers).get('X-CT-Session-Token')).toBe('tab-only-token')

    await user.type(screen.getByLabelText(/project name/i), 'Sample framework')
    await user.click(screen.getByRole('button', { name: /create project/i }))

    expect(await screen.findByRole('heading', { name: 'Sample framework' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /open/i }))

    const statusPanel = screen.getByRole('complementary', { name: /open project/i })
    expect(await within(statusPanel).findByText('Sample Controls')).toBeInTheDocument()
    expect(within(statusPanel).getByText('12')).toBeInTheDocument()
    expect(within(statusPanel).getByText('3')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /delete/i }))
    expect(screen.getByRole('dialog', { name: /delete sample framework/i })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /delete project/i }))

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Sample framework' })).not.toBeInTheDocument())
  })

  it('renders safe API errors rather than raw backend details', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ error: { code: 'server_error', message: 'Traceback /home/runner/work/private.py' } }, { status: 500 })))

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))

    expect(await screen.findAllByText('The local API returned an error.')).toHaveLength(2)
    expect(screen.queryByText(/Traceback|\/home\/runner/i)).not.toBeInTheDocument()
  })
})

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}
