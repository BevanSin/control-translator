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

    const deleteTrigger = screen.getByRole('button', { name: /^delete$/i })
    await user.click(deleteTrigger)
    expect(screen.getByRole('dialog', { name: /delete sample framework/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /cancel/i })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: /delete project/i })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: /cancel/i })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(deleteTrigger).toHaveFocus()

    await user.click(deleteTrigger)
    await user.click(screen.getByRole('button', { name: /delete project/i }))

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Sample framework' })).not.toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Projects' })).toHaveFocus()
  })

  it('keeps connection controls available after a rejected token and permits retry', async () => {
    const user = userEvent.setup()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ error: { code: 'unauthorized', message: 'Traceback /home/runner/work/private.py' } }, { status: 401 }))
      .mockResolvedValueOnce(jsonResponse({ count: 0, projects: [] }))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    const tokenInput = screen.getByLabelText(/session token/i)
    await user.type(tokenInput, 'expired-token')
    await user.click(screen.getByRole('button', { name: /connect/i }))

    expect(await screen.findByText(/session token was not accepted/i)).toBeInTheDocument()
    expect(screen.queryByText(/Traceback|\/home\/runner/i)).not.toBeInTheDocument()
    expect(tokenInput).toBeInTheDocument()

    await user.clear(tokenInput)
    await user.type(tokenInput, 'current-token')
    await user.click(screen.getByRole('button', { name: /connect/i }))

    expect(await screen.findByText(/no projects yet/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /disconnect/i })).toBeInTheDocument()
  })

  it('serializes project list refreshes against mutations', async () => {
    const user = userEvent.setup()
    const refresh = deferred<Response>()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 1, projects: [project] }))
      .mockReturnValueOnce(refresh.promise)
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    expect(await screen.findByRole('heading', { name: 'Sample framework' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /refresh projects/i }))

    expect(screen.getByRole('button', { name: /refresh projects/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /create project/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^delete$/i })).toBeDisabled()

    refresh.resolve(jsonResponse({ count: 1, projects: [project] }))
    await waitFor(() => expect(screen.getByRole('button', { name: /refresh projects/i })).toBeEnabled())
  })
})

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}
