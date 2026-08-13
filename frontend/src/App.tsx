import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent, KeyboardEvent as ReactKeyboardEvent } from 'react'
import { ApiClient, ApiClientError } from './api/client'
import type { IngestSourceResponse, PipelineEvent, Project, ProjectStatus, RunRecord } from './api/contracts'
import { useTheme } from './theme/useTheme'
import './App.css'

const DEFAULT_API_BASE = ''
const DEFAULT_CONFIG_PATH = 'config/nzism-azure.json'
const POLL_INTERVAL_MS = 750
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024
const BASE64_CHUNK_SIZE = 0x8000

type LoadState = 'idle' | 'loading' | 'ready' | 'error'
type SourceMode = 'upload' | 'url'

function App() {
  const { preference, resolved, setPreference } = useTheme()
  const [apiBaseUrl, setApiBaseUrl] = useState(DEFAULT_API_BASE)
  const [sessionToken, setSessionToken] = useState('')
  const [isConnected, setIsConnected] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const [loadState, setLoadState] = useState<LoadState>('idle')
  const [message, setMessage] = useState('')
  const [status, setStatus] = useState<ProjectStatus | null>(null)
  const [activeProject, setActiveProject] = useState<Project | null>(null)
  const [configPath, setConfigPath] = useState(DEFAULT_CONFIG_PATH)
  const [newProjectName, setNewProjectName] = useState('')
  const [createConfigPath, setCreateConfigPath] = useState(DEFAULT_CONFIG_PATH)
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null)
  const [isMutating, setIsMutating] = useState(false)
  const [sourceMode, setSourceMode] = useState<SourceMode>('upload')
  const [sourceFile, setSourceFile] = useState<File | null>(null)
  const [sourceUrl, setSourceUrl] = useState('')
  const [sourceResult, setSourceResult] = useState<IngestSourceResponse | null>(null)
  const [runHistory, setRunHistory] = useState<RunRecord[]>([])
  const [selectedRun, setSelectedRun] = useState<RunRecord | null>(null)
  const [runEvents, setRunEvents] = useState<PipelineEvent[]>([])
  const [droppedEventCount, setDroppedEventCount] = useState(0)
  const [isPollingRun, setIsPollingRun] = useState(false)
  const [isRunActionPending, setIsRunActionPending] = useState(false)
  const [pollMessage, setPollMessage] = useState('')
  const projectsRequestVersion = useRef(0)
  const openProjectRequestVersion = useRef(0)
  const runRequestVersion = useRef(0)
  const activeProjectIdRef = useRef<string | null>(null)
  const latestSequenceRef = useRef<number | undefined>(undefined)
  const terminalRunIdsRef = useRef<Set<string>>(new Set())
  const deleteDialogRef = useRef<HTMLElement>(null)
  const deleteCancelRef = useRef<HTMLButtonElement>(null)
  const deleteTriggerRef = useRef<HTMLButtonElement>(null)
  const dashboardHeadingRef = useRef<HTMLHeadingElement>(null)

  const client = useMemo(
    () => new ApiClient({ baseUrl: apiBaseUrl, getSessionToken: () => sessionToken }),
    [apiBaseUrl, sessionToken],
  )

  const activeRun = selectedRun && !isTerminalRun(selectedRun) ? selectedRun : runHistory.find((run) => !isTerminalRun(run)) ?? null
  const projectLocked = activeRun !== null
  const isProjectsBusy = loadState === 'loading' || isMutating || isRunActionPending || projectLocked
  const selectedRunId = selectedRun?.id
  const selectedRunIsTerminal = selectedRun ? isTerminalRun(selectedRun) : false

  const loadProjects = useCallback(async (): Promise<boolean> => {
    const requestVersion = ++projectsRequestVersion.current
    setLoadState('loading')
    setMessage('')
    try {
      const nextProjects = await client.listProjects()
      if (requestVersion !== projectsRequestVersion.current) {
        return false
      }
      setProjects(nextProjects)
      setLoadState('ready')
      return true
    } catch (error) {
      if (requestVersion !== projectsRequestVersion.current) {
        return false
      }
      setLoadState('error')
      setMessage(safeMessage(error))
      return false
    }
  }, [client])

  useEffect(() => {
    activeProjectIdRef.current = activeProject?.id ?? null
  }, [activeProject])

  const refreshRunHistory = useCallback(async (project: Project, options: { selectLatest?: boolean } = {}) => {
    const runs = sortRuns(await client.listRuns(project.id))
    setRunHistory(runs)
    if (options.selectLatest) {
      const latest = runs.at(0) ?? null
      setSelectedRun(latest)
      resetEvents()
    } else {
      setSelectedRun((current) => runs.find((run) => run.id === current?.id) ?? runs.at(0) ?? null)
    }
    return runs
  }, [client])

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (await loadProjects()) {
      setIsConnected(true)
    }
  }

  function disconnect() {
    projectsRequestVersion.current += 1
    openProjectRequestVersion.current += 1
    runRequestVersion.current += 1
    setIsConnected(false)
    setSessionToken('')
    setProjects([])
    setLoadState('idle')
    setIsMutating(false)
    setMessage('')
    setStatus(null)
    setActiveProject(null)
    setRunHistory([])
    setSelectedRun(null)
    resetEvents()
  }

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const requestVersion = ++projectsRequestVersion.current
    setMessage('')
    setIsMutating(true)
    try {
      const project = await client.createProject({
        name: newProjectName,
        config_path: toOptionalPath(createConfigPath),
      })
      if (requestVersion !== projectsRequestVersion.current) {
        return
      }
      setNewProjectName('')
      setProjects((current) => sortProjects([project, ...current.filter((item) => item.id !== project.id)]))
      setMessage(`Created ${project.name}.`)
    } catch (error) {
      if (requestVersion === projectsRequestVersion.current) {
        setMessage(safeMessage(error))
      }
    } finally {
      if (requestVersion === projectsRequestVersion.current) {
        setIsMutating(false)
      }
    }
  }

  async function openProject(project: Project) {
    const requestVersion = ++openProjectRequestVersion.current
    runRequestVersion.current += 1
    setMessage('')
    setPollMessage('')
    setStatus(null)
    setActiveProject(null)
    setRunHistory([])
    setSelectedRun(null)
    resetEvents()
    try {
      const [projectStatus, runs] = await Promise.all([
        client.openProject(project.id, { config_path: toOptionalPath(configPath) }),
        client.listRuns(project.id).then(sortRuns),
      ])
      if (requestVersion !== openProjectRequestVersion.current || projectStatus.project_id !== project.id) {
        return
      }
      setStatus(projectStatus)
      setActiveProject(project)
      setRunHistory(runs)
      setSelectedRun(runs.at(0) ?? null)
      resetEvents()
      const recoverable = runs.find((run) => !isTerminalRun(run))
      if (recoverable) {
        setSelectedRun(recoverable)
        setMessage(`Recovered in-progress run ${shortRunId(recoverable.id)}. Monitoring has resumed.`)
      }
    } catch (error) {
      if (requestVersion === openProjectRequestVersion.current) {
        setMessage(safeMessage(error))
      }
    }
  }

  async function deleteProject() {
    if (!deleteTarget) {
      return
    }
    const requestVersion = ++projectsRequestVersion.current
    setMessage('')
    setIsMutating(true)
    try {
      await client.deleteProject(deleteTarget.id)
      if (requestVersion !== projectsRequestVersion.current) {
        return
      }
      setProjects((current) => current.filter((project) => project.id !== deleteTarget.id))
      if (status?.project_id === deleteTarget.id) {
        setStatus(null)
        setActiveProject(null)
        setRunHistory([])
        setSelectedRun(null)
        resetEvents()
      }
      setMessage(`Deleted ${deleteTarget.name}.`)
      deleteTriggerRef.current = null
      setDeleteTarget(null)
    } catch (error) {
      if (requestVersion === projectsRequestVersion.current) {
        setMessage(safeMessage(error))
      }
    } finally {
      if (requestVersion === projectsRequestVersion.current) {
        setIsMutating(false)
      }
    }
  }

  async function ingestSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!activeProject || projectLocked) {
      return
    }
    setMessage('')
    setSourceResult(null)
    setIsMutating(true)
    try {
      const result = sourceMode === 'upload'
        ? await client.uploadSource(activeProject.id, {
            config_path: toOptionalPath(configPath),
            filename: sourceFile?.name ?? '',
            content_type: sourceFile?.type || null,
            content: await fileToBase64(sourceFile),
          })
        : await client.ingestUrlSource(activeProject.id, {
            config_path: toOptionalPath(configPath),
            url: sourceUrl,
            timeout_seconds: 10,
          })
      setSourceResult(result)
      setMessage(`Validated ${result.filename}: ${result.rows} rows, ${result.columns} columns.`)
    } catch (error) {
      setMessage(safeMessage(error))
    } finally {
      setIsMutating(false)
    }
  }

  async function startRun(distribute: boolean) {
    if (!activeProject || projectLocked) {
      return
    }
    const project = activeProject
    const requestVersion = ++runRequestVersion.current
    setMessage('')
    setPollMessage('')
    setIsRunActionPending(true)
    try {
      const run = await client.startRun(project.id, { config_path: toOptionalPath(configPath), distribute })
      if (
        requestVersion !== runRequestVersion.current
        || activeProjectIdRef.current !== project.id
        || run.project_id !== project.id
      ) {
        return
      }
      setSelectedRun(run)
      resetEvents()
      setRunHistory((current) => sortRuns([run, ...current.filter((item) => item.id !== run.id)]))
      setMessage(`${distribute ? 'Run' : 'Review refresh'} ${shortRunId(run.id)} started.`)
    } catch (error) {
      if (requestVersion === runRequestVersion.current) {
        setMessage(safeMessage(error))
      }
    } finally {
      if (requestVersion === runRequestVersion.current) {
        setIsRunActionPending(false)
      }
    }
  }

  async function cancelRun() {
    if (!activeProject || !selectedRun || isTerminalRun(selectedRun)) {
      return
    }
    setMessage('')
    setIsRunActionPending(true)
    try {
      await client.cancelRun(activeProject.id, selectedRun.id)
      setMessage(`Cancellation requested for run ${shortRunId(selectedRun.id)}.`)
      await pollSelectedRun(activeProject, selectedRun.id)
    } catch (error) {
      setMessage(safeMessage(error))
    } finally {
      setIsRunActionPending(false)
    }
  }

  const pollSelectedRun = useCallback(async (project: Project, runId: string) => {
    const requestVersion = runRequestVersion.current
    try {
      const [run, response] = await Promise.all([
        client.getRun(project.id, runId),
        client.getRunEvents(project.id, runId, latestSequenceRef.current),
      ])
      if (requestVersion !== runRequestVersion.current) {
        return null
      }
      setSelectedRun(run)
      setRunHistory((current) => sortRuns([run, ...current.filter((item) => item.id !== run.id)]))
      setDroppedEventCount(response.dropped_event_count)
      if (typeof response.latest_sequence === 'number') {
        latestSequenceRef.current = response.latest_sequence
      }
      if (response.events.length > 0) {
        setRunEvents((current) => mergeEvents(current, response.events))
      }
      setPollMessage(response.terminal_state ? `Run reached ${labelRunState(response.terminal_state)}.` : '')
      if (response.terminal_state || isTerminalRun(run)) {
        terminalRunIdsRef.current.add(run.id)
        runRequestVersion.current += 1
      }
      return run
    } catch (error) {
      if (requestVersion === runRequestVersion.current && !terminalRunIdsRef.current.has(runId)) {
        setPollMessage(`${safeMessage(error)} Retrying polling without duplicating events.`)
      }
      return null
    }
  }, [client])

  function selectRun(run: RunRecord) {
    runRequestVersion.current += 1
    latestSequenceRef.current = undefined
    setSelectedRun(run)
    setRunEvents([])
    setDroppedEventCount(run.dropped_event_count)
    setPollMessage(isTerminalRun(run) ? `Viewing ${labelRunState(run.state)} run ${shortRunId(run.id)}.` : 'Monitoring resumed for this run.')
  }

  function resetEvents() {
    latestSequenceRef.current = undefined
    setRunEvents([])
    setDroppedEventCount(0)
  }

  useEffect(() => {
    if (!activeProject || !selectedRunId) {
      setIsPollingRun(false)
      return
    }
    runRequestVersion.current += 1
    const requestVersion = runRequestVersion.current
    const project = activeProject
    const runId = selectedRunId
    let stopped = false
    async function tick() {
      setIsPollingRun(!selectedRunIsTerminal)
      const run = await pollSelectedRun(project, runId)
      if (stopped || requestVersion !== runRequestVersion.current || run?.state && isTerminalRun(run)) {
        setIsPollingRun(false)
        return
      }
      window.setTimeout(tick, POLL_INTERVAL_MS)
    }
    void tick()
    return () => {
      stopped = true
    }
  }, [activeProject, selectedRunId, selectedRunIsTerminal, pollSelectedRun])

  useEffect(() => {
    if (!deleteTarget) {
      return
    }
    const trigger = deleteTriggerRef.current
    const fallback = dashboardHeadingRef.current
    deleteCancelRef.current?.focus()
    return () => {
      if (trigger?.isConnected) {
        trigger.focus()
      } else {
        fallback?.focus()
      }
    }
  }, [deleteTarget])

  function closeDeleteDialog() {
    setDeleteTarget(null)
  }

  function handleDeleteDialogKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeDeleteDialog()
      return
    }
    if (event.key !== 'Tab' || !deleteDialogRef.current) {
      return
    }

    const focusable = Array.from(deleteDialogRef.current.querySelectorAll<HTMLElement>('button:not([disabled])'))
    const first = focusable.at(0)
    const last = focusable.at(-1)
    if (!first || !last) {
      return
    }
    if (event.shiftKey && (document.activeElement === first || !deleteDialogRef.current.contains(document.activeElement))) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <div className="app-shell">
      <div className="app-content" inert={deleteTarget !== null}>
        <a className="skip-link" href="#main-content">Skip to project dashboard</a>
        <header className="app-header">
        <div>
          <p className="eyebrow">Control Translator</p>
          <h1>Local project dashboard</h1>
          <p className="lede">Ingest standards, configure projects, and monitor durable pipeline runs through the authenticated loopback API.</p>
        </div>
        <label className="theme-picker">
          <span>Theme</span>
          <select
            aria-label={`Theme preference, currently ${preference}; resolved ${resolved}`}
            value={preference}
            onChange={(event) => setPreference(event.target.value as 'system' | 'light' | 'dark')}
          >
            <option value="system">System</option>
            <option value="dark">Dark</option>
            <option value="light">Light</option>
          </select>
        </label>
        </header>

        <nav className="app-nav" aria-label="Project sections">
        <a href="#projects">Projects</a>
        <a href="#create-project">Create</a>
        <a href="#source-setup">Source</a>
        <a href="#runs">Runs</a>
        <a href="#project-status">Status</a>
        </nav>

        <main id="main-content" className="dashboard" tabIndex={-1}>
        {!isConnected ? (
          <section className="panel bootstrap" aria-labelledby="connect-heading">
            <div>
              <p className="eyebrow">Session bootstrap</p>
              <h2 id="connect-heading">Connect to your local API</h2>
              <p>
                Start <code>ct-api</code>, then paste the ephemeral session token shown in that terminal. The token stays only in memory for this browser tab.
              </p>
            </div>
            <form className="form-grid" onSubmit={connect}>
              <label>
                <span>API origin</span>
                <input
                  value={apiBaseUrl}
                  onChange={(event) => setApiBaseUrl(event.target.value)}
                  inputMode="url"
                  placeholder="Same origin (recommended)"
                  disabled={loadState === 'loading'}
                />
              </label>
              <label>
                <span>Session token</span>
                <input
                  value={sessionToken}
                  onChange={(event) => setSessionToken(event.target.value)}
                  type="password"
                  autoComplete="off"
                  required
                  disabled={loadState === 'loading'}
                />
              </label>
              <button type="submit" disabled={loadState === 'loading'}>
                {loadState === 'loading' ? 'Connecting…' : 'Connect'}
              </button>
            </form>
            {message ? <div className="notice" role="alert">{message}</div> : null}
          </section>
        ) : (
          <>
            <section className="panel controls" aria-labelledby="dashboard-heading">
              <div>
                <p className="eyebrow">Authenticated session</p>
                <h2 ref={dashboardHeadingRef} id="dashboard-heading" tabIndex={-1}>Projects</h2>
                {projectLocked ? <p className="lock-text">Run {shortRunId(activeRun.id)} owns the project lock; source and delete mutations are disabled.</p> : null}
              </div>
              <div className="card-actions">
                <button type="button" className="secondary" onClick={loadProjects} disabled={isProjectsBusy}>Refresh projects</button>
                <button type="button" className="secondary" onClick={disconnect}>Disconnect</button>
              </div>
            </section>

            {message ? <div className="notice" role="status">{message}</div> : null}
            {pollMessage ? <div className="notice subtle" role="status">{pollMessage}</div> : null}

            <section className="grid-layout">
              <div className="stack">
                <section id="create-project" className="panel" aria-labelledby="create-heading">
                  <h2 id="create-heading">Create project</h2>
                  <form className="form-grid" onSubmit={createProject}>
                    <label>
                      <span>Project name</span>
                      <input value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)} maxLength={200} required disabled={isMutating} />
                    </label>
                    <label>
                      <span>Configuration path</span>
                      <input value={createConfigPath} onChange={(event) => setCreateConfigPath(event.target.value)} maxLength={4096} disabled={isMutating} />
                    </label>
                    <button type="submit" disabled={loadState === 'loading' || isMutating}>Create project</button>
                  </form>
                </section>

                <section id="projects" className="panel" aria-labelledby="projects-heading">
                  <div className="section-heading">
                    <h2 id="projects-heading">Project list</h2>
                    <span className="badge">{projects.length} total</span>
                  </div>
                  {loadState === 'loading' ? <StateCard title="Loading projects" text="Contacting the local API…" /> : null}
                  {loadState === 'error' ? <StateCard title="Projects unavailable" text={message || 'Try refreshing the dashboard.'} /> : null}
                  {loadState === 'ready' && projects.length === 0 ? (
                    <StateCard title="No projects yet" text="Create a project to initialize a local workspace." />
                  ) : null}
                  {projects.length > 0 ? (
                    <ul className="project-list">
                      {projects.map((project) => (
                        <li key={project.id}>
                          <article className="project-card">
                            <div>
                              <h3>{project.name}</h3>
                              <p>Updated {formatDate(project.updated_at)}</p>
                            </div>
                            <div className="card-actions">
                              <button type="button" onClick={() => openProject(project)} disabled={isMutating || isRunActionPending}>Open</button>
                              <button
                                type="button"
                                className="danger"
                                disabled={isProjectsBusy || (activeProject?.id === project.id && projectLocked)}
                                onClick={(event) => {
                                  deleteTriggerRef.current = event.currentTarget
                                  setDeleteTarget(project)
                                }}
                              >
                                Delete
                              </button>
                            </div>
                          </article>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </section>

                <section id="source-setup" className="panel" aria-labelledby="source-heading">
                  <div className="section-heading">
                    <h2 id="source-heading">Standard source</h2>
                    <span className="badge">CSV / XLSX / URL</span>
                  </div>
                  {activeProject ? (
                    <form className="form-grid" onSubmit={ingestSource}>
                      <fieldset className="radio-row" disabled={projectLocked || isMutating}>
                        <legend>Source type</legend>
                        <label><input type="radio" name="source-mode" checked={sourceMode === 'upload'} onChange={() => setSourceMode('upload')} /> CSV or XLSX file</label>
                        <label><input type="radio" name="source-mode" checked={sourceMode === 'url'} onChange={() => setSourceMode('url')} /> HTTPS URL</label>
                      </fieldset>
                      {sourceMode === 'upload' ? (
                        <label>
                          <span>Standard file</span>
                          <input type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event: ChangeEvent<HTMLInputElement>) => setSourceFile(event.target.files?.[0] ?? null)} disabled={projectLocked || isMutating} />
                        </label>
                      ) : (
                        <label>
                          <span>Source URL</span>
                          <input value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} inputMode="url" placeholder="https://example.invalid/standard.csv" disabled={projectLocked || isMutating} required />
                        </label>
                      )}
                      <button type="submit" disabled={projectLocked || isMutating || (sourceMode === 'upload' && !sourceFile)}>Validate and ingest source</button>
                    </form>
                  ) : <StateCard title="Open a project first" text="Select a project before uploading or fetching a standard." />}
                  {sourceResult ? <SourceSummary source={sourceResult} /> : null}
                </section>

                <section id="runs" className="panel" aria-labelledby="runs-heading">
                  <div className="section-heading">
                    <h2 id="runs-heading">Pipeline runs</h2>
                    {isPollingRun ? <span className="badge live">Polling</span> : <span className="badge">{runHistory.length} stored</span>}
                  </div>
                  {activeProject ? (
                    <>
                      <div className="card-actions left-actions">
                        <button type="button" onClick={() => startRun(true)} disabled={projectLocked || isRunActionPending}>Start run</button>
                        <button type="button" className="secondary" onClick={() => startRun(false)} disabled={projectLocked || isRunActionPending}>Start review refresh</button>
                        <button type="button" className="secondary" onClick={() => refreshRunHistory(activeProject, { selectLatest: true })} disabled={isRunActionPending}>Refresh history</button>
                      </div>
                      {runHistory.length > 0 ? (
                        <ul className="run-list" aria-label="Run history">
                          {runHistory.map((run) => (
                            <li key={run.id}>
                              <button type="button" className={`run-card ${selectedRun?.id === run.id ? 'selected' : ''}`} onClick={() => selectRun(run)}>
                                <span>{shortRunId(run.id)}</span>
                                <span className={`state-pill ${run.state}`}>{labelRunState(run.state)}</span>
                                <span>{formatDate(run.updated_at)}</span>
                              </button>
                            </li>
                          ))}
                        </ul>
                      ) : <StateCard title="No run history" text="Start a run to create durable history and event logs." />}
                      {selectedRun ? (
                        <RunDetail
                          run={selectedRun}
                          events={runEvents}
                          droppedEventCount={droppedEventCount}
                          onCancel={cancelRun}
                          cancelDisabled={isRunActionPending || isTerminalRun(selectedRun)}
                        />
                      ) : null}
                    </>
                  ) : <StateCard title="Open a project first" text="Run controls appear after a project is opened with a safe config path." />}
                </section>
              </div>

              <aside id="project-status" className="panel status-panel" aria-labelledby="status-heading">
                <h2 id="status-heading">Open project</h2>
                <label>
                  <span>Configuration path for open/status</span>
                  <input value={configPath} onChange={(event) => setConfigPath(event.target.value)} maxLength={4096} disabled={projectLocked} />
                </label>
                {status ? (
                  <dl className="status-list">
                    <div>
                      <dt>Project</dt>
                      <dd>{activeProject?.name}</dd>
                    </div>
                    <div>
                      <dt>Framework</dt>
                      <dd>{status.display_name || status.framework}</dd>
                    </div>
                    <div>
                      <dt>Mappings</dt>
                      <dd>{formatCount(status.total_mappings)}</dd>
                    </div>
                    <div>
                      <dt>Pending review</dt>
                      <dd>{formatCount(status.pending_review)}</dd>
                    </div>
                    <div>
                      <dt>Artifacts</dt>
                      <dd>{status.has_bundle ? 'Available' : 'Not built yet'}</dd>
                    </div>
                    <div>
                      <dt>Last run</dt>
                      <dd>{selectedRun ? `${labelRunState(selectedRun.state)} (${shortRunId(selectedRun.id)})` : 'No run selected'}</dd>
                    </div>
                  </dl>
                ) : (
                  <StateCard title="Nothing open" text="Choose Open on a project to view framework metadata and safe summaries." />
                )}
              </aside>
            </section>
          </>
        )}
        </main>
      </div>

      {deleteTarget ? (
        <div className="modal-backdrop" role="presentation">
          <section
            ref={deleteDialogRef}
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-heading"
            onKeyDown={handleDeleteDialogKeyDown}
          >
            <h2 id="delete-heading">Delete {deleteTarget.name}?</h2>
            <p>This removes the local project workspace. This action cannot be undone from the dashboard.</p>
            <div className="card-actions">
              <button ref={deleteCancelRef} type="button" className="secondary" onClick={closeDeleteDialog}>Cancel</button>
              <button type="button" className="danger" onClick={deleteProject} disabled={isMutating}>Delete project</button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  )
}

function StateCard({ title, text }: { title: string; text: string }) {
  return (
    <div className="state-card">
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  )
}

function SourceSummary({ source }: { source: IngestSourceResponse }) {
  return (
    <dl className="status-list source-summary" aria-label="Source validation result">
      <div><dt>Stored source</dt><dd>{source.filename}</dd></div>
      <div><dt>Shape</dt><dd>{source.rows} rows × {source.columns} columns</dd></div>
      <div><dt>Size</dt><dd>{source.size_bytes.toLocaleString()} bytes</dd></div>
    </dl>
  )
}

function RunDetail({ run, events, droppedEventCount, onCancel, cancelDisabled }: {
  run: RunRecord
  events: PipelineEvent[]
  droppedEventCount: number
  onCancel: () => void
  cancelDisabled: boolean
}) {
  const warnings = events.filter((event) => event.type === 'run.warning')
  return (
    <article className="run-detail" aria-labelledby="run-detail-heading">
      <div className="section-heading">
        <div>
          <h3 id="run-detail-heading">Run {shortRunId(run.id)}</h3>
          <p>{run.error_message ? `Failure summary: ${failureSummary(run)}` : `Updated ${formatDate(run.updated_at)}`}</p>
        </div>
        <span className={`state-pill ${run.state}`}>{labelRunState(run.state)}</span>
      </div>
      <div className="card-actions left-actions">
        <button type="button" className="danger" onClick={onCancel} disabled={cancelDisabled}>Cancel run</button>
      </div>
      {droppedEventCount > 0 ? <div className="notice subtle" role="status">{droppedEventCount} older events were dropped from the bounded history. Resume continues from the first available event.</div> : null}
      {warnings.length > 0 ? <div className="warning-box" role="status">{warnings.length} warning{warnings.length === 1 ? '' : 's'} reported during this run.</div> : null}
      <ol className="event-list" aria-label="Pipeline events">
        {events.map((event) => (
          <li key={event.sequence} className={event.type === 'run.warning' ? 'warning-event' : ''}>
            <span className="event-sequence">#{event.sequence}</span>
            <span>{event.message}</span>
            {event.stage ? <span className="event-stage">{event.stage}</span> : null}
          </li>
        ))}
      </ol>
      {events.length === 0 ? <StateCard title="No events loaded yet" text="Polling will append ordered events without duplicating terminal state." /> : null}
    </article>
  )
}

function safeMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message
  }
  return 'Something went wrong. Try the action again.'
}

function failureSummary(run: RunRecord): string {
  if (run.error_type === 'RunInterrupted') {
    return 'Run interrupted before completion.'
  }
  return 'Details redacted. Check the local API logs for diagnostics.'
}

function toOptionalPath(value: string): string | null {
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

function sortProjects(items: Project[]): Project[] {
  return [...items].sort((left, right) => right.updated_at.localeCompare(left.updated_at))
}

function sortRuns(items: RunRecord[]): RunRecord[] {
  return [...items].sort((left, right) => right.updated_at.localeCompare(left.updated_at))
}

function mergeEvents(current: PipelineEvent[], next: PipelineEvent[]): PipelineEvent[] {
  const bySequence = new Map<number, PipelineEvent>()
  for (const event of [...current, ...next]) {
    bySequence.set(event.sequence, event)
  }
  return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence)
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return 'recently'
  }
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function formatCount(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toLocaleString() : 'Not available'
}

function isTerminalRun(run: RunRecord): boolean {
  return run.state === 'succeeded' || run.state === 'failed' || run.state === 'cancelled'
}

function labelRunState(state: RunRecord['state']): string {
  return state[0].toUpperCase() + state.slice(1)
}

function shortRunId(runId: string): string {
  return runId.slice(0, 8)
}

async function fileToBase64(file: File | null): Promise<string> {
  if (!file) {
    return ''
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    throw new ApiClientError('The selected file must be 2 MiB or smaller.', 413, 'payload_too_large')
  }
  const bytes = new Uint8Array(await file.arrayBuffer())
  const chunks: string[] = []
  for (let index = 0; index < bytes.length; index += BASE64_CHUNK_SIZE) {
    chunks.push(String.fromCharCode(...bytes.subarray(index, index + BASE64_CHUNK_SIZE)))
  }
  return window.btoa(chunks.join(''))
}

export default App
