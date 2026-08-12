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
      .mockResolvedValueOnce(jsonResponse({ count: 0, runs: [] }))
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

  it('ingests a CSV source and completes a run through ordered polling', async () => {
    const user = userEvent.setup()
    const run = runRecord('a'.repeat(32), 'running')
    const succeeded = { ...run, state: 'succeeded', updated_at: '2026-01-01T00:00:03Z', finished_at: '2026-01-01T00:00:03Z' }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 1, projects: [project] }))
      .mockResolvedValueOnce(jsonResponse(openStatus()))
      .mockResolvedValueOnce(jsonResponse({ count: 0, runs: [] }))
      .mockResolvedValueOnce(jsonResponse({ source_id: 's1', filename: 'standard.csv', content_type: 'text/csv', size_bytes: 7, rows: 2, columns: 2, sha256: 'abc', project_path: 'source/standard.csv' }, { status: 201 }))
      .mockResolvedValueOnce(jsonResponse({ run }, { status: 202 }))
      .mockResolvedValueOnce(jsonResponse({ run: succeeded }))
      .mockResolvedValueOnce(jsonResponse({
        count: 3,
        dropped_event_count: 0,
        latest_sequence: 2,
        terminal_state: 'succeeded',
        events: [
          event(run.id, 2, 'run.completed', 'Pipeline completed'),
          event(run.id, 0, 'run.started', 'Pipeline started'),
          event(run.id, 1, 'run.warning', 'Pipeline warning'),
          event(run.id, 1, 'run.warning', 'Duplicate warning'),
        ],
      }))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await user.click(await screen.findByRole('button', { name: /open/i }))

    const file = new File(['a,b\n1,2\n'], 'standard.csv', { type: 'text/csv' })
    await user.upload(screen.getByLabelText(/standard file/i), file)
    await user.click(screen.getByRole('button', { name: /validate and ingest source/i }))
    expect(await screen.findByText(/Validated standard.csv: 2 rows, 2 columns/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^start run$/i }))

    expect(await screen.findByText(/Run reached Succeeded/i)).toBeInTheDocument()
    expect(screen.getByText(/1 warning reported/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Pipeline warning/i)).toHaveLength(1)
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/sources/upload'))).toBe(true)
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/runs') && String(url).includes('/events'))).toBe(true)
  })

  it('recovers an in-progress run, prevents conflicting mutations, and cancels it', async () => {
    const user = userEvent.setup()
    const running = runRecord('b'.repeat(32), 'running')
    const cancelled = { ...running, state: 'cancelled', updated_at: '2026-01-01T00:00:04Z', finished_at: '2026-01-01T00:00:04Z' }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 1, projects: [project] }))
      .mockResolvedValueOnce(jsonResponse(openStatus()))
      .mockResolvedValueOnce(jsonResponse({ count: 1, runs: [running] }))
      .mockResolvedValueOnce(jsonResponse({ run: running }))
      .mockResolvedValueOnce(jsonResponse({ count: 1, dropped_event_count: 2, latest_sequence: 3, terminal_state: null, events: [event(running.id, 3, 'stage.started', 'Map started')] }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ run: cancelled }))
      .mockResolvedValueOnce(jsonResponse({ count: 1, dropped_event_count: 2, latest_sequence: 4, terminal_state: 'cancelled', events: [event(running.id, 4, 'run.cancelled', 'Pipeline cancelled')] }))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await user.click(await screen.findByRole('button', { name: /open/i }))

    expect(await screen.findByText(/Recovered in-progress run/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^start run$/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /validate and ingest source/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^delete$/i })).toBeDisabled()
    expect(await screen.findByText(/2 older events were dropped/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /cancel run/i }))

    expect(await screen.findByText(/Run reached Cancelled/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^start run$/i })).toBeEnabled()
  })

  it('shows failed run summaries and responsive accessible run UI', async () => {
    window.innerWidth = 390
    window.dispatchEvent(new Event('resize'))
    const user = userEvent.setup()
    const failed = { ...runRecord('c'.repeat(32), 'failed'), error_type: 'RuntimeError', error_message: '[redacted]', finished_at: '2026-01-01T00:00:05Z' }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 1, projects: [project] }))
      .mockResolvedValueOnce(jsonResponse(openStatus()))
      .mockResolvedValueOnce(jsonResponse({ count: 1, runs: [failed] }))
      .mockResolvedValueOnce(jsonResponse({ run: failed }))
      .mockResolvedValueOnce(jsonResponse({ count: 1, dropped_event_count: 0, latest_sequence: 1, terminal_state: 'failed', events: [event(failed.id, 1, 'run.failed', 'Pipeline failed')] }))
    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await user.click(await screen.findByRole('button', { name: /open/i }))

    expect(await screen.findByText(/Failure summary: \[redacted\]/i)).toBeInTheDocument()
    expect(screen.queryByText(/\/home\/runner|Traceback|secret/i)).not.toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
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

function openStatus() {
  return {
    project_id: project.id,
    framework: 'sample',
    display_name: 'Sample Controls',
    store_exists: true,
    has_bundle: false,
    total_mappings: 12,
    pending_review: 3,
  }
}

function runRecord(id: string, state: 'running' | 'succeeded' | 'failed' | 'cancelled') {
  return {
    schema_version: 1,
    id,
    project_id: project.id,
    state,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:01Z',
    started_at: '2026-01-01T00:00:01Z',
    finished_at: state === 'running' ? null : '2026-01-01T00:00:02Z',
    error_type: null,
    error_message: null,
    dropped_event_count: 0,
  }
}

function event(runId: string, sequence: number, type: string, message: string) {
  return {
    schema_version: 1,
    type,
    run_id: runId,
    sequence,
    timestamp: '2026-01-01T00:00:01Z',
    stage: type.startsWith('stage.') ? 'map' : null,
    message,
    summary: type === 'run.warning' ? { kind: 'validation' } : {},
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}
