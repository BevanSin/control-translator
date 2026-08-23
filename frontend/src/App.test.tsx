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
const projectBeta = {
  id: 'b2b9f47e-9963-4e23-8fd2-0a4f04513e85',
  name: 'Beta framework',
  created_at: '2026-01-03T00:00:00Z',
  updated_at: '2026-01-04T00:00:00Z',
}

describe('project dashboard', () => {
  it('passes accessibility checks for the bootstrap screen', async () => {
    const { container } = render(<App />)

    expect(await axe(container)).toHaveNoViolations()
    expect(screen.getByRole('link', { name: /skip to project dashboard/i })).toHaveAttribute('href', '#main-content')
    expect(screen.getByRole('navigation', { name: /primary navigation/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /from standard to azure policy/i })).toBeInTheDocument()
  })

  it('keeps appearance controls in a compact Config menu', async () => {
    const user = userEvent.setup()
    render(<App />)

    const configSummary = screen.getByText(/^Config$/)
    const configMenu = configSummary.closest('details')
    expect(configMenu).not.toHaveAttribute('open')
    await user.click(configSummary)
    expect(configMenu).toHaveAttribute('open')
    const themeSwitch = screen.getByRole('switch', { name: /dark theme/i })
    await user.click(themeSwitch)

    expect(themeSwitch).toBeChecked()
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
  })

  it('connects once from an in-memory launcher bootstrap token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ count: 0, projects: [] }))
    vi.stubGlobal('fetch', fetchMock)

    render(<App bootstrapToken="fragment-only-token" />)

    expect(await screen.findByText(/No projects yet\. Start with Create/i)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).not.toContain('fragment-only-token')
    expect((fetchMock.mock.calls[0][1]!.headers as Headers).get('X-CT-Session-Token')).toBe('fragment-only-token')
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

    expect(await screen.findByText(/No projects yet\. Start with Create/i)).toBeInTheDocument()
    expect(fetchMock.mock.calls[0][0]).not.toContain('tab-only-token')
    expect((fetchMock.mock.calls[0][1]!.headers as Headers).get('X-CT-Session-Token')).toBe('tab-only-token')

    await goToPage(user, 'Create')
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
    expect(screen.getByRole('heading', { name: 'Create' })).toHaveFocus()
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
        count: 4,
        dropped_event_count: 0,
        latest_sequence: 3,
        terminal_state: 'succeeded',
        events: [
          event(run.id, 3, 'run.completed', 'Pipeline completed'),
          event(run.id, 0, 'run.started', 'Pipeline started'),
          event(run.id, 1, 'stage.completed', '2,312 built-in policies available', {
            catalogue_source: 'bundled',
            snapshot_schema_version: 1,
            snapshot_repository: 'https://github.com/Azure/azure-policy',
            snapshot_commit: 'c9562a455473fb6179680fadbc1919db01c29cfc',
            snapshot_generated_at: '2026-08-14',
            snapshot_sha256: '056c321e451b98359226101061fc59f39e2b5b8e2c856a01c661525debdd8766',
          }, 'catalogue'),
          event(run.id, 2, 'run.warning', 'Pipeline warning'),
          event(run.id, 2, 'run.warning', 'Duplicate warning'),
        ],
      }))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    await user.click(await screen.findByRole('button', { name: /open/i }))

    const file = new File(['a,b\n1,2\n'], 'standard.csv', { type: 'text/csv' })
    await user.upload(screen.getByLabelText(/standard file/i), file)
    await user.click(screen.getByRole('button', { name: /validate and ingest source/i }))
    expect(await screen.findByText(/Validated standard.csv: 2 rows, 2 columns/i)).toBeInTheDocument()

    await goToPage(user, 'Run')
    await user.click(screen.getByRole('button', { name: /^start run$/i }))

    expect(await screen.findByText(/Run reached Succeeded/i)).toBeInTheDocument()
    expect(screen.getByText(/1 warning reported/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Pipeline warning/i)).toHaveLength(1)
    expect(screen.getByText(/Catalogue evidence: schema v1/)).toHaveTextContent(/days old/)
    expect(screen.getByText(/Catalogue evidence: schema v1/)).toHaveTextContent(/Azure\/azure-policy@c9562a455473/)
    expect(screen.getByText(/Catalogue evidence: schema v1/)).toHaveTextContent(/SHA-256 056c321e/)
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
    await goToPage(user, 'Create')
    await user.click(await screen.findByRole('button', { name: /open/i }))

    await goToPage(user, 'Run')
    expect(await screen.findByText(/Recovered in-progress run/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^start run$/i })).toBeDisabled()
    expect(await screen.findByText(/2 older events were dropped/i)).toBeInTheDocument()

    await goToPage(user, 'Create')
    expect(screen.getByRole('button', { name: /validate and ingest source/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^delete$/i })).toBeDisabled()

    await goToPage(user, 'Run')
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
    await goToPage(user, 'Create')
    await user.click(await screen.findByRole('button', { name: /open/i }))
    await goToPage(user, 'Run')

    expect(await screen.findByText(/Failure summary: Details redacted/i)).toBeInTheDocument()
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

    expect(await screen.findByText(/No projects yet\. Start with Create/i)).toBeInTheDocument()
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
    await goToPage(user, 'Create')
    expect(await screen.findByRole('heading', { name: 'Sample framework' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /refresh projects/i }))

    expect(screen.getByRole('button', { name: /refresh projects/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /create project/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^delete$/i })).toBeDisabled()

    refresh.resolve(jsonResponse({ count: 1, projects: [project] }))
    await waitFor(() => expect(screen.getByRole('button', { name: /refresh projects/i })).toBeEnabled())
  })

  it('ignores stale open responses that resolve after another project is opened', async () => {
    const user = userEvent.setup()
    const alphaStatus = deferred<Response>()
    const alphaRuns = deferred<Response>()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 2, projects: [project, projectBeta] }))
      .mockReturnValueOnce(alphaStatus.promise)
      .mockReturnValueOnce(alphaRuns.promise)
      .mockResolvedValueOnce(jsonResponse(openStatus(projectBeta.id, 'Beta Controls')))
      .mockResolvedValueOnce(jsonResponse({ count: 1, runs: [runRecord('d'.repeat(32), 'succeeded', projectBeta.id)] }))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    const openButtons = await screen.findAllByRole('button', { name: /open/i })

    await user.click(openButtons[0])
    await user.click(openButtons[1])
    expect(await screen.findByText('Beta Controls')).toBeInTheDocument()

    alphaStatus.resolve(jsonResponse(openStatus(project.id, 'Alpha Controls')))
    alphaRuns.resolve(jsonResponse({ count: 0, runs: [] }))

    await waitFor(() => expect(screen.getByText('Beta Controls')).toBeInTheDocument())
    expect(screen.queryByText('Alpha Controls')).not.toBeInTheDocument()
    expect(screen.getByText(/Succeeded \(dddddddd\)/i)).toBeInTheDocument()
  })

  it('keeps run starts scoped to the project that requested them', async () => {
    const user = userEvent.setup()
    const pendingStart = deferred<Response>()
    const wrongProjectRun = runRecord('e'.repeat(32), 'running', projectBeta.id)
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 2, projects: [project, projectBeta] }))
      .mockResolvedValueOnce(jsonResponse(openStatus()))
      .mockResolvedValueOnce(jsonResponse({ count: 0, runs: [] }))
      .mockReturnValueOnce(pendingStart.promise)
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    await user.click((await screen.findAllByRole('button', { name: /open/i }))[0])
    await screen.findByText('Sample Controls')

    await goToPage(user, 'Run')
    await user.click(screen.getByRole('button', { name: /^start run$/i }))

    await goToPage(user, 'Create')
    for (const button of screen.getAllByRole('button', { name: /open/i })) {
      expect(button).toBeDisabled()
    }

    pendingStart.resolve(jsonResponse({ run: wrongProjectRun }, { status: 202 }))

    await goToPage(user, 'Run')
    await waitFor(() => expect(screen.getByRole('button', { name: /^start run$/i })).toBeEnabled())
    expect(screen.queryByText(/Run eeeeeeee/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/started/i)).not.toBeInTheDocument()
  })

  it('loads Review and Outputs once on first page entry', async () => {
    const user = userEvent.setup()
    let reviewCalls = 0
    let guidanceCalls = 0
    let artifactCalls = 0
    const fetchMock = vi.fn(async (url: string | URL | Request) => {
      const href = String(url)
      if (href.endsWith('/api/v1/projects')) {
        return jsonResponse({ count: 1, projects: [project] })
      }
      if (href.includes(`/projects/${project.id}/open`)) {
        return jsonResponse(openStatus())
      }
      if (href.includes(`/projects/${project.id}/runs`)) {
        return jsonResponse({ count: 0, runs: [] })
      }
      if (href.includes(`/projects/${project.id}/review`)) {
        reviewCalls += 1
        return jsonResponse({ count: 1, total: 1, page: 1, page_size: 10, items: [reviewMapping('AUTO-1')] })
      }
      if (href.includes(`/projects/${project.id}/guidance`)) {
        guidanceCalls += 1
        return jsonResponse({ count: 0, items: [], affects_future_runs: true })
      }
      if (href.includes(`/projects/${project.id}/artifacts/inventory`)) {
        artifactCalls += 1
        return jsonResponse({ count: 1, items: [{ name: 'policySet.json', size_bytes: 128, content_type: 'application/json', previewable: true }] })
      }
      return jsonResponse({ error: { code: 'unexpected' } }, { status: 500 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    await user.click(await screen.findByRole('button', { name: /open/i }))

    await goToPage(user, 'Review')
    expect(await screen.findByText('AUTO-1')).toBeInTheDocument()
    await goToPage(user, 'Create')
    await goToPage(user, 'Review')
    expect(reviewCalls).toBe(1)
    expect(guidanceCalls).toBe(1)

    await goToPage(user, 'Outputs')
    expect(await screen.findByRole('button', { name: /^preview$/i })).toBeInTheDocument()
    await goToPage(user, 'Create')
    await goToPage(user, 'Outputs')
    expect(artifactCalls).toBe(1)
  })

  it('ignores stale artifact inventory after switching projects', async () => {
    const user = userEvent.setup()
    const staleArtifacts = deferred<Response>()
    const fetchMock = vi.fn(async (url: string | URL | Request) => {
      const href = String(url)
      if (href.endsWith('/api/v1/projects')) {
        return jsonResponse({ count: 2, projects: [project, projectBeta] })
      }
      if (href.includes(`/projects/${project.id}/open`)) {
        return jsonResponse(openStatus(project.id, 'Alpha Controls'))
      }
      if (href.includes(`/projects/${projectBeta.id}/open`)) {
        return jsonResponse(openStatus(projectBeta.id, 'Beta Controls'))
      }
      if (href.includes('/runs')) {
        return jsonResponse({ count: 0, runs: [] })
      }
      if (href.includes(`/projects/${project.id}/artifacts/inventory`)) {
        return staleArtifacts.promise
      }
      if (href.includes(`/projects/${projectBeta.id}/artifacts/inventory`)) {
        return jsonResponse({ count: 1, items: [{ name: 'beta.json', size_bytes: 8, content_type: 'application/json', previewable: true }] })
      }
      return jsonResponse({ error: { code: 'unexpected' } }, { status: 500 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    let openButtons = await screen.findAllByRole('button', { name: /open/i })
    await user.click(openButtons[0])
    await screen.findByText('Alpha Controls')
    await goToPage(user, 'Outputs')

    await goToPage(user, 'Create')
    openButtons = screen.getAllByRole('button', { name: /open/i })
    await user.click(openButtons[1])
    await screen.findByText('Beta Controls')
    await goToPage(user, 'Outputs')
    expect(await screen.findByText('beta.json')).toBeInTheDocument()

    staleArtifacts.resolve(jsonResponse({ count: 1, items: [{ name: 'alpha.json', size_bytes: 8, content_type: 'application/json', previewable: true }] }))
    await waitFor(() => expect(screen.queryByText('alpha.json')).not.toBeInTheDocument())
  })

  it('clears project review state and ignores stale project-scoped responses', async () => {
    const user = userEvent.setup()
    const staleReview = deferred<Response>()
    const staleGuidance = deferred<Response>()
    const staleArtifacts = deferred<Response>()
    let alphaReviewCalls = 0
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const href = String(url)
      const method = init?.method ?? 'GET'
      if (href.endsWith('/api/v1/projects') && method === 'GET') {
        return jsonResponse({ count: 2, projects: [project, projectBeta] })
      }
      if (href.includes(`/projects/${project.id}/open`)) {
        return jsonResponse(openStatus(project.id, 'Alpha Controls'))
      }
      if (href.includes(`/projects/${projectBeta.id}/open`)) {
        return jsonResponse(openStatus(projectBeta.id, 'Beta Controls'))
      }
      if (href.includes(`/projects/${project.id}/runs`) || href.includes(`/projects/${projectBeta.id}/runs`)) {
        return jsonResponse({ count: 0, runs: [] })
      }
      if (href.includes(`/projects/${project.id}/review`)) {
        alphaReviewCalls += 1
        return alphaReviewCalls === 1
          ? jsonResponse({ count: 1, total: 1, page: 1, page_size: 10, items: [reviewMapping('ALPHA-1')] })
          : staleReview.promise
      }
      if (href.includes(`/projects/${project.id}/guidance`)) {
        return alphaReviewCalls === 1
          ? jsonResponse({ count: 0, items: [], affects_future_runs: true })
          : staleGuidance.promise
      }
      if (href.includes(`/projects/${project.id}/artifacts/inventory`)) {
        return alphaReviewCalls === 1
          ? jsonResponse({ count: 0, items: [] })
          : staleArtifacts.promise
      }
      if (href.includes(`/projects/${projectBeta.id}/review`)) {
        return jsonResponse({ count: 1, total: 1, page: 1, page_size: 10, items: [reviewMapping('BETA-1')] })
      }
      if (href.includes(`/projects/${projectBeta.id}/guidance`)) {
        return jsonResponse({ count: 0, items: [], affects_future_runs: true })
      }
      if (href.includes(`/projects/${projectBeta.id}/artifacts/inventory`)) {
        return jsonResponse({ count: 0, items: [] })
      }
      return jsonResponse({ error: { code: 'unexpected' } }, { status: 500 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    let openButtons = await screen.findAllByRole('button', { name: /open/i })
    await user.click(openButtons[0])
    expect(await screen.findByText('Alpha Controls')).toBeInTheDocument()

    await goToPage(user, 'Review')
    expect(await screen.findByText('ALPHA-1')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: /ALPHA-1/i }))
    expect(screen.getByRole('button', { name: /approve selected/i })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: /refresh review/i }))
    await goToPage(user, 'Create')
    openButtons = screen.getAllByRole('button', { name: /open/i })
    await user.click(openButtons[1])
    expect(await screen.findByText('Beta Controls')).toBeInTheDocument()
    await goToPage(user, 'Review')
    expect(screen.queryByText('ALPHA-1')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /approve selected/i })).toBeDisabled()

    staleReview.resolve(jsonResponse({ count: 1, total: 1, page: 1, page_size: 10, items: [reviewMapping('ALPHA-LATE')] }))
    staleGuidance.resolve(jsonResponse({ count: 1, items: [{ id: 'late', control_id: 'ALPHA-LATE', policy_id: 'p', include_reasoning: 'late', source: 'human-review', provenance: 'late' }], affects_future_runs: true }))
    staleArtifacts.resolve(jsonResponse({ count: 1, items: [{ name: 'policySet.json', size_bytes: 1, content_type: 'application/json', previewable: true }] }))
    await waitFor(() => expect(screen.queryByText('ALPHA-LATE')).not.toBeInTheDocument())

    expect(await screen.findByText('BETA-1')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/review/approve'))).toBe(false)
  })

  it('paginates mapping review results accessibly', async () => {
    const user = userEvent.setup()
    const firstPage = Array.from({ length: 10 }, (_value, index) => reviewMapping(`CTRL-${index + 1}`))
    const secondPage = [reviewMapping('CTRL-11')]
    const fetchMock = vi.fn(async (url: string | URL | Request) => {
      const href = String(url)
      if (href.endsWith('/api/v1/projects')) {
        return jsonResponse({ count: 1, projects: [project] })
      }
      if (href.includes(`/projects/${project.id}/open`)) {
        return jsonResponse(openStatus())
      }
      if (href.includes(`/projects/${project.id}/runs`)) {
        return jsonResponse({ count: 0, runs: [] })
      }
      if (href.includes(`/projects/${project.id}/review`)) {
        const page = new URL(href).searchParams.get('page')
        return jsonResponse({ count: page === '2' ? 1 : 10, total: 11, page: Number(page ?? '1'), page_size: 10, items: page === '2' ? secondPage : firstPage })
      }
      if (href.includes(`/projects/${project.id}/guidance`)) {
        return jsonResponse({ count: 0, items: [], affects_future_runs: true })
      }
      if (href.includes(`/projects/${project.id}/artifacts/inventory`)) {
        return jsonResponse({ count: 0, items: [] })
      }
      return jsonResponse({ error: { code: 'unexpected' } }, { status: 500 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    await user.click(await screen.findByRole('button', { name: /open/i }))
    await goToPage(user, 'Review')

    expect(await screen.findByText('CTRL-1')).toBeInTheDocument()
    expect(screen.getByText(/Page 1 of 2; 11 mappings total/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /previous page/i })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /next page/i }))

    expect(await screen.findByText('CTRL-11')).toBeInTheDocument()
    expect(screen.queryByText('CTRL-1')).not.toBeInTheDocument()
    expect(screen.getByText(/Page 2 of 2; 11 mappings total/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /next page/i })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /previous page/i }))
    expect(await screen.findByText('CTRL-1')).toBeInTheDocument()
  })

  it('invalidates an in-flight mapping mutation when disconnecting', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const pendingApprove = deferred<Response>()
    const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const href = String(url)
      const method = init?.method ?? 'GET'
      if (href.endsWith('/api/v1/projects') && method === 'GET') {
        return jsonResponse({ count: 1, projects: [project] })
      }
      if (href.includes(`/projects/${project.id}/open`)) {
        return jsonResponse(openStatus())
      }
      if (href.includes(`/projects/${project.id}/runs`)) {
        return jsonResponse({ count: 0, runs: [] })
      }
      if (href.includes(`/projects/${project.id}/review/approve`)) {
        return pendingApprove.promise
      }
      if (href.includes(`/projects/${project.id}/review`)) {
        return jsonResponse({ count: 1, total: 1, page: 1, page_size: 10, items: [reviewMapping('STALE-1')] })
      }
      if (href.includes(`/projects/${project.id}/guidance`)) {
        return jsonResponse({ count: 0, items: [], affects_future_runs: true })
      }
      if (href.includes(`/projects/${project.id}/artifacts/inventory`)) {
        return jsonResponse({ count: 0, items: [] })
      }
      return jsonResponse({ error: { code: 'unexpected' } }, { status: 500 })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    await user.click(await screen.findByRole('button', { name: /open/i }))
    await goToPage(user, 'Review')
    await user.click(await screen.findByRole('checkbox', { name: /STALE-1/i }))
    await user.click(screen.getByRole('button', { name: /approve selected/i }))

    await user.click(screen.getByRole('button', { name: /disconnect/i }))
    pendingApprove.resolve(jsonResponse({ updated: ['STALE-1'], already_updated: [], not_found: [] }))
    await user.clear(screen.getByLabelText(/session token/i))
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))

    await goToPage(user, 'Create')
    expect(await screen.findByRole('button', { name: /open/i })).toBeEnabled()
    expect(screen.queryByText(/1 updated, 0 already current/i)).not.toBeInTheDocument()
  })

  it.each([
    ['/home/runner/work/private/config.json'],
    ['C:\\Users\\runner\\private\\config.json'],
  ])('redacts failed run backend diagnostics containing local paths: %s', async (errorMessage) => {
    const user = userEvent.setup()
    const failed = {
      ...runRecord('f'.repeat(32), 'failed'),
      error_type: 'RuntimeError',
      error_message: errorMessage,
      finished_at: '2026-01-01T00:00:05Z',
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 1, projects: [project] }))
      .mockResolvedValueOnce(jsonResponse(openStatus()))
      .mockResolvedValueOnce(jsonResponse({ count: 1, runs: [failed] }))
      .mockResolvedValueOnce(jsonResponse({ run: failed }))
      .mockResolvedValueOnce(jsonResponse({ count: 1, dropped_event_count: 0, latest_sequence: 1, terminal_state: 'failed', events: [event(failed.id, 1, 'run.failed', 'Pipeline failed')] }))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    await user.click(await screen.findByRole('button', { name: /open/i }))
    await goToPage(user, 'Run')

    expect(await screen.findByText(/Failure summary: Details redacted/i)).toBeInTheDocument()
    expect(screen.queryByText(errorMessage)).not.toBeInTheDocument()
  })

  it('rejects oversized uploads before reading or submitting the file', async () => {
    const user = userEvent.setup()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 1, projects: [project] }))
      .mockResolvedValueOnce(jsonResponse(openStatus()))
      .mockResolvedValueOnce(jsonResponse({ count: 0, runs: [] }))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    await user.click(await screen.findByRole('button', { name: /open/i }))

    const file = new File(['x'], 'too-large.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'size', { value: 2 * 1024 * 1024 + 1 })
    const arrayBuffer = vi.spyOn(file, 'arrayBuffer')
    await user.upload(screen.getByLabelText(/standard file/i), file)
    await user.click(screen.getByRole('button', { name: /validate and ingest source/i }))

    expect(await screen.findByText(/2 MiB or smaller/i)).toBeInTheDocument()
    expect(arrayBuffer).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/sources/upload'))).toBe(false)
  })

  it('reviews mappings, manages guidance, and safely previews/downloads artifacts', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const createObjectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:artifact')
    const revokeObjectUrl = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const mapping = {
      control_id: 'SAMPLE-LM-1',
      decision: 'review',
      confidence: 0.87,
      source: 'auto',
      rationale: 'Offline rationale',
      policies: [{ id: 'policy-1', name: 'Audit policy' }],
    }
    const guidance = {
      id: 'guidance-1',
      control_id: 'SAMPLE-LM-1',
      policy_id: 'policy-1',
      display_name: 'Audit policy',
      include_reasoning: 'Use this local pattern later.',
      source: 'human-review',
      provenance: 'offline-test',
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ count: 1, projects: [project] }))
      .mockResolvedValueOnce(jsonResponse(openStatus()))
      .mockResolvedValueOnce(jsonResponse({ count: 0, runs: [] }))
      .mockResolvedValueOnce(jsonResponse({ count: 1, total: 1, page: 1, page_size: 10, items: [mapping] }))
      .mockResolvedValueOnce(jsonResponse({ count: 0, items: [], affects_future_runs: true }))
      .mockResolvedValueOnce(jsonResponse({ added: ['policy-1'] }))
      .mockResolvedValueOnce(jsonResponse({ updated: ['SAMPLE-LM-1'], already_updated: [], not_found: [] }))
      .mockResolvedValueOnce(jsonResponse({ count: 0, total: 0, page: 1, page_size: 10, items: [] }))
      .mockResolvedValueOnce(jsonResponse({ count: 0, items: [], affects_future_runs: true }))
      .mockResolvedValueOnce(jsonResponse({ guidance, deleted: [], affects_future_runs: true }))
      .mockResolvedValueOnce(jsonResponse({ guidance: null, deleted: ['guidance-1'], affects_future_runs: true }))
      .mockResolvedValueOnce(jsonResponse({ count: 1, items: [{ name: 'policySet.json', size_bytes: 128, content_type: 'application/json', previewable: true }] }))
      .mockResolvedValueOnce(jsonResponse({ name: 'policySet.json', content_type: 'application/json', text: '{"type":"Microsoft.Authorization/policySetDefinitions"}', truncated: false }))
      .mockResolvedValueOnce(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(<App />)
    await user.type(screen.getByLabelText(/session token/i), 'token')
    await user.click(screen.getByRole('button', { name: /connect/i }))
    await goToPage(user, 'Create')
    await user.click(await screen.findByRole('button', { name: /open/i }))

    await goToPage(user, 'Review')
    expect(await screen.findByText('SAMPLE-LM-1')).toBeInTheDocument()
    expect(screen.queryByText(/token|secret|password/i)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /promote first policy to oos candidate/i }))
    expect(await screen.findByText(/Added 1 policy to the OOS register/i)).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: /SAMPLE-LM-1/i }))
    await user.click(screen.getByRole('button', { name: /approve selected/i }))
    expect(await screen.findByText(/1 updated, 0 already current, 0 conflicted or missing/i)).toBeInTheDocument()

    await user.type(screen.getByLabelText(/control id/i), 'SAMPLE-LM-1')
    await user.type(screen.getByLabelText(/policy id/i), 'policy-1')
    await user.type(screen.getByLabelText(/source\/provenance/i), 'offline-test')
    await user.type(screen.getByLabelText(/guidance rationale/i), 'Use this local pattern later.')
    await user.click(screen.getByRole('button', { name: /save guidance/i }))
    expect(await screen.findByText(/affects future mapping runs/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /delete guidance/i }))
    expect(await screen.findByText(/Guidance deleted/i)).toBeInTheDocument()

    await goToPage(user, 'Outputs')
    await user.click(await screen.findByRole('button', { name: /^preview$/i }))
    expect(await screen.findByText(/Microsoft.Authorization\/policySetDefinitions/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^download$/i }))
    expect(await screen.findByText(/Downloaded policySet.json/i)).toBeInTheDocument()
    expect(createObjectUrl).toHaveBeenCalled()
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:artifact')
    expect(await axe(container)).toHaveNoViolations()
  })
})

async function goToPage(user: ReturnType<typeof userEvent.setup>, page: string) {
  await user.click(screen.getByRole('button', { name: page }))
}

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function openStatus(projectId = project.id, displayName = 'Sample Controls') {
  return {
    project_id: projectId,
    framework: 'sample',
    display_name: displayName,
    store_exists: true,
    has_bundle: false,
    total_mappings: 12,
    pending_review: 3,
  }
}

function runRecord(id: string, state: 'running' | 'succeeded' | 'failed' | 'cancelled', projectId = project.id) {
  return {
    schema_version: 1,
    id,
    project_id: projectId,
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

function reviewMapping(controlId: string) {
  return {
    control_id: controlId,
    decision: 'review',
    confidence: 0.87,
    source: 'auto',
    rationale: 'Offline rationale',
    policies: [{ id: `${controlId.toLowerCase()}-policy`, name: `${controlId} policy` }],
  }
}

function event(
  runId: string,
  sequence: number,
  type: string,
  message: string,
  summary?: Record<string, boolean | number | string | null>,
  stage?: string | null,
) {
  return {
    schema_version: 1,
    type,
    run_id: runId,
    sequence,
    timestamp: '2026-01-01T00:00:01Z',
    stage: stage === undefined ? (type.startsWith('stage.') ? 'map' : null) : stage,
    message,
    summary: summary ?? (type === 'run.warning' ? { kind: 'validation' } : {}),
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}
