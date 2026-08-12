import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from 'react'
import { ApiClient, ApiClientError } from './api/client'
import type { Project, ProjectStatus } from './api/contracts'
import { useTheme } from './theme/useTheme'
import './App.css'

const DEFAULT_API_BASE = ''
const DEFAULT_CONFIG_PATH = 'config/nzism-azure.json'

type LoadState = 'idle' | 'loading' | 'ready' | 'error'

function App() {
  const { preference, resolved, setPreference } = useTheme()
  const [apiBaseUrl, setApiBaseUrl] = useState(DEFAULT_API_BASE)
  const [sessionToken, setSessionToken] = useState('')
  const [isConnected, setIsConnected] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const [loadState, setLoadState] = useState<LoadState>('idle')
  const [message, setMessage] = useState('')
  const [status, setStatus] = useState<ProjectStatus | null>(null)
  const [activeProjectName, setActiveProjectName] = useState('')
  const [configPath, setConfigPath] = useState(DEFAULT_CONFIG_PATH)
  const [newProjectName, setNewProjectName] = useState('')
  const [createConfigPath, setCreateConfigPath] = useState(DEFAULT_CONFIG_PATH)
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null)
  const [isMutating, setIsMutating] = useState(false)
  const projectsRequestVersion = useRef(0)
  const deleteDialogRef = useRef<HTMLElement>(null)
  const deleteCancelRef = useRef<HTMLButtonElement>(null)
  const deleteTriggerRef = useRef<HTMLButtonElement>(null)
  const dashboardHeadingRef = useRef<HTMLHeadingElement>(null)

  const client = useMemo(
    () => new ApiClient({ baseUrl: apiBaseUrl, getSessionToken: () => sessionToken }),
    [apiBaseUrl, sessionToken],
  )

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

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (await loadProjects()) {
      setIsConnected(true)
    }
  }

  function disconnect() {
    projectsRequestVersion.current += 1
    setIsConnected(false)
    setSessionToken('')
    setProjects([])
    setLoadState('idle')
    setIsMutating(false)
    setMessage('')
    setStatus(null)
    setActiveProjectName('')
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
    setMessage('')
    setStatus(null)
    try {
      const projectStatus = await client.openProject(project.id, { config_path: toOptionalPath(configPath) })
      setStatus(projectStatus)
      setActiveProjectName(project.name)
    } catch (error) {
      setMessage(safeMessage(error))
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
        setActiveProjectName('')
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

  useEffect(() => {
    if (!deleteTarget) {
      return
    }
    deleteCancelRef.current?.focus()
    return () => {
      const trigger = deleteTriggerRef.current
      if (trigger?.isConnected) {
        trigger.focus()
      } else {
        dashboardHeadingRef.current?.focus()
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

  const isProjectsBusy = loadState === 'loading' || isMutating

  return (
    <div className="app-shell">
      <div className="app-content" inert={deleteTarget !== null}>
        <a className="skip-link" href="#main-content">Skip to project dashboard</a>
        <header className="app-header">
        <div>
          <p className="eyebrow">Control Translator</p>
          <h1>Local project dashboard</h1>
          <p className="lede">Manage offline project workspaces through the authenticated loopback API.</p>
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
              </div>
              <div className="card-actions">
                <button type="button" className="secondary" onClick={loadProjects} disabled={isProjectsBusy}>Refresh projects</button>
                <button type="button" className="secondary" onClick={disconnect}>Disconnect</button>
              </div>
            </section>

            {message ? <div className="notice" role="status">{message}</div> : null}

            <section className="grid-layout">
              <div className="stack">
                <section id="create-project" className="panel" aria-labelledby="create-heading">
                  <h2 id="create-heading">Create project</h2>
                  <form className="form-grid" onSubmit={createProject}>
                    <label>
                      <span>Project name</span>
                      <input value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)} maxLength={200} required />
                    </label>
                    <label>
                      <span>Configuration path</span>
                      <input value={createConfigPath} onChange={(event) => setCreateConfigPath(event.target.value)} maxLength={4096} />
                    </label>
                    <button type="submit" disabled={isProjectsBusy}>Create project</button>
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
                              <button type="button" onClick={() => openProject(project)}>Open</button>
                              <button
                                type="button"
                                className="danger"
                                disabled={isProjectsBusy}
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
              </div>

              <aside id="project-status" className="panel status-panel" aria-labelledby="status-heading">
                <h2 id="status-heading">Open project</h2>
                <label>
                  <span>Configuration path for open/status</span>
                  <input value={configPath} onChange={(event) => setConfigPath(event.target.value)} maxLength={4096} />
                </label>
                {status ? (
                  <dl className="status-list">
                    <div>
                      <dt>Project</dt>
                      <dd>{activeProjectName}</dd>
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
                  </dl>
                ) : (
                  <StateCard title="Nothing open" text="Choose Open on a project to view its safe summary." />
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

function safeMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    return error.message
  }
  return 'Something went wrong. Try the action again.'
}

function toOptionalPath(value: string): string | null {
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

function sortProjects(items: Project[]): Project[] {
  return [...items].sort((left, right) => right.updated_at.localeCompare(left.updated_at))
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

export default App
