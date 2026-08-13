import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent, KeyboardEvent as ReactKeyboardEvent } from 'react'
import { ApiClient, ApiClientError } from './api/client'
import type {
  ArtifactInventoryItem,
  ArtifactPreviewResponse,
  GuidanceItem,
  IngestSourceResponse,
  MappingReviewItem,
  PipelineEvent,
  Project,
  ProjectStatus,
  RunRecord,
} from './api/contracts'
import { useTheme } from './theme/useTheme'
import './App.css'

const DEFAULT_API_BASE = ''
const DEFAULT_CONFIG_PATH = 'config/nzism-azure.json'
const POLL_INTERVAL_MS = 750
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024
const BASE64_CHUNK_SIZE = 0x8000
const REVIEW_PAGE_SIZE = 10

type LoadState = 'idle' | 'loading' | 'ready' | 'error'
type SourceMode = 'upload' | 'url'
type PortalPage = 'home' | 'create' | 'run' | 'review' | 'outputs'

const PORTAL_PAGES: Array<{ id: PortalPage; label: string }> = [
  { id: 'home', label: 'Home' },
  { id: 'create', label: 'Create' },
  { id: 'run', label: 'Run' },
  { id: 'review', label: 'Review' },
  { id: 'outputs', label: 'Outputs' },
]

function App({ bootstrapToken = '' }: { bootstrapToken?: string }) {
  const { preference, resolved, setPreference } = useTheme()
  const [activePage, setActivePage] = useState<PortalPage>('home')
  const [apiBaseUrl, setApiBaseUrl] = useState(DEFAULT_API_BASE)
  const [sessionToken, setSessionToken] = useState(bootstrapToken)
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
  const [reviewItems, setReviewItems] = useState<MappingReviewItem[]>([])
  const [reviewQuery, setReviewQuery] = useState('')
  const [reviewStatus, setReviewStatus] = useState('review')
  const [reviewPage, setReviewPage] = useState(1)
  const [reviewTotal, setReviewTotal] = useState(0)
  const [selectedControls, setSelectedControls] = useState<Set<string>>(new Set())
  const [guidanceItems, setGuidanceItems] = useState<GuidanceItem[]>([])
  const [guidanceAffectsRuns, setGuidanceAffectsRuns] = useState(false)
  const [guidanceForm, setGuidanceForm] = useState({ control_id: '', policy_id: '', display_name: '', guidance: '', source: 'human-review', provenance: '' })
  const [artifacts, setArtifacts] = useState<ArtifactInventoryItem[]>([])
  const [artifactPreview, setArtifactPreview] = useState<ArtifactPreviewResponse | null>(null)
  const projectsRequestVersion = useRef(0)
  const openProjectRequestVersion = useRef(0)
  const projectDataVersion = useRef(0)
  const reviewRequestVersion = useRef(0)
  const artifactPreviewRequestVersion = useRef(0)
  const runRequestVersion = useRef(0)
  const activeProjectIdRef = useRef<string | null>(null)
  const latestSequenceRef = useRef<number | undefined>(undefined)
  const terminalRunIdsRef = useRef<Set<string>>(new Set())
  const bootstrapAttemptedRef = useRef(false)
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
  const reviewPageCount = Math.max(1, Math.ceil(reviewTotal / REVIEW_PAGE_SIZE))

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
    if (!bootstrapToken || bootstrapAttemptedRef.current) {
      return
    }
    bootstrapAttemptedRef.current = true
    void loadProjects().then((connected) => {
      if (connected) {
        setIsConnected(true)
      }
    })
  }, [bootstrapToken, loadProjects])

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
    resetProjectScopedData()
    setIsConnected(false)
    setActivePage('home')
    setSessionToken('')
    setProjects([])
    setLoadState('idle')
    setIsMutating(false)
    setMessage('')
    setStatus(null)
    activeProjectIdRef.current = null
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
    resetProjectScopedData()
    setMessage('')
    setPollMessage('')
    setStatus(null)
    activeProjectIdRef.current = null
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
      activeProjectIdRef.current = project.id
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

  async function refreshReviewData(project = activeProject, page = reviewPage) {
        if (!project) {
          return
        }
        const requestVersion = ++reviewRequestVersion.current
        const dataVersion = projectDataVersion.current
        const requestedConfigPath = toOptionalPath(configPath)
        const requestedQuery = reviewQuery
        const requestedStatus = reviewStatus
        try {
          const [review, guidance, artifactInventory] = await Promise.all([
            client.reviewMappings(project.id, requestedConfigPath, requestedQuery, requestedStatus, page, REVIEW_PAGE_SIZE),
            client.listGuidance(project.id, requestedConfigPath),
            client.artifactInventory(project.id, requestedConfigPath).catch(() => ({ count: 0, items: [] })),
          ])
          if (
            requestVersion !== reviewRequestVersion.current
            || dataVersion !== projectDataVersion.current
            || activeProjectIdRef.current !== project.id
          ) {
            return
          }
          setReviewItems(Array.isArray(review.items) ? review.items : [])
          setReviewPage(review.page ?? page)
          setReviewTotal(review.total ?? review.count)
          setGuidanceItems(Array.isArray(guidance.items) ? guidance.items : [])
          setGuidanceAffectsRuns(guidance.affects_future_runs)
          setArtifacts(artifactInventory.items)
          setSelectedControls(new Set())
        } catch (error) {
          if (requestVersion === reviewRequestVersion.current && dataVersion === projectDataVersion.current) {
            setMessage(safeMessage(error))
          }
        }
  }

  function toggleControl(controlId: string) {
        setSelectedControls((current) => {
          const next = new Set(current)
          if (next.has(controlId)) {
            next.delete(controlId)
          } else {
            next.add(controlId)
          }
          return next
        })
  }

  async function mutateSelectedMappings(action: 'approve' | 'reject') {
        if (!activeProject || projectLocked || selectedControls.size === 0) {
          return
        }
        const project = activeProject
        const dataVersion = projectDataVersion.current
        const selectedIds = [...selectedControls]
        const visibleIds = new Set(reviewItems.map((item) => item.control_id))
        if (selectedIds.some((controlId) => !visibleIds.has(controlId))) {
          setSelectedControls(new Set())
          setMessage('Selection was reset because the review results changed. Select mappings again before mutating.')
          return
        }
        const verb = action === 'approve' ? 'approve' : 'reject'
        if (!window.confirm(`Confirm bulk ${verb} for ${selectedIds.length} selected mapping${selectedIds.length === 1 ? '' : 's'}?`)) {
          return
        }
        setIsMutating(true)
        try {
          const result = await client.mutateMappings(project.id, toOptionalPath(configPath), action, selectedIds)
          if (dataVersion !== projectDataVersion.current || activeProjectIdRef.current !== project.id) {
            return
          }
          setMessage(`${result.updated.length} updated, ${result.already_updated.length} already current, ${result.not_found.length} conflicted or missing.`)
          setSelectedControls(new Set())
          await refreshReviewData(project)
        } catch (error) {
          if (dataVersion === projectDataVersion.current && activeProjectIdRef.current === project.id) {
            setMessage(safeMessage(error))
          }
        } finally {
          if (dataVersion === projectDataVersion.current && activeProjectIdRef.current === project.id) {
            setIsMutating(false)
          }
        }
  }

  async function submitGuidance(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        if (!activeProject || projectLocked) {
          return
        }
        const project = activeProject
        const dataVersion = projectDataVersion.current
        setIsMutating(true)
        try {
          const result = await client.saveGuidance(project.id, toOptionalPath(configPath), guidanceForm)
          if (dataVersion !== projectDataVersion.current || activeProjectIdRef.current !== project.id) {
            return
          }
          setGuidanceItems((current) => [result.guidance!, ...current.filter((item) => item.id !== result.guidance?.id)])
          setGuidanceAffectsRuns(result.affects_future_runs)
          setGuidanceForm({ control_id: '', policy_id: '', display_name: '', guidance: '', source: 'human-review', provenance: '' })
          setMessage(result.affects_future_runs ? 'Guidance saved. It affects future mapping runs, not the already-built artifacts.' : 'Guidance saved locally.')
        } catch (error) {
          if (dataVersion === projectDataVersion.current && activeProjectIdRef.current === project.id) {
            setMessage(safeMessage(error))
          }
        } finally {
          if (dataVersion === projectDataVersion.current && activeProjectIdRef.current === project.id) {
            setIsMutating(false)
          }
        }
  }

  async function deleteGuidance(id: string) {
        if (!activeProject || projectLocked || !window.confirm('Delete this local guidance entry? Future runs will stop using it.')) {
          return
        }
        const project = activeProject
        const dataVersion = projectDataVersion.current
        setIsMutating(true)
        try {
          const result = await client.deleteGuidance(project.id, toOptionalPath(configPath), [id])
          if (dataVersion !== projectDataVersion.current || activeProjectIdRef.current !== project.id) {
            return
          }
          setGuidanceItems((current) => current.filter((item) => !result.deleted?.includes(item.id)))
          setMessage('Guidance deleted.')
        } catch (error) {
          if (dataVersion === projectDataVersion.current && activeProjectIdRef.current === project.id) {
            setMessage(safeMessage(error))
          }
        } finally {
          if (dataVersion === projectDataVersion.current && activeProjectIdRef.current === project.id) {
            setIsMutating(false)
          }
        }
  }

  async function addFirstPolicyToOos(item: MappingReviewItem) {
        const policy = item.policies[0]
        if (!activeProject || !policy || projectLocked || !window.confirm(`Add ${policy.name || policy.id} to the OOS register?`)) {
          return
        }
        setIsMutating(true)
        try {
          const result = await client.addToOos(activeProject.id, toOptionalPath(configPath), [policy.id], [`OOS candidate from review of ${item.control_id}`])
          setMessage(`Added ${result.added.length} policy to the OOS register. Re-run the pipeline for artifacts to reflect it.`)
        } catch (error) {
          setMessage(safeMessage(error))
        } finally {
          setIsMutating(false)
        }
  }

  async function previewArtifact(name: string) {
        if (!activeProject) {
          return
        }
        const project = activeProject
        const requestVersion = ++artifactPreviewRequestVersion.current
        const dataVersion = projectDataVersion.current
        try {
          const preview = await client.artifactPreview(project.id, toOptionalPath(configPath), name)
          if (
            requestVersion !== artifactPreviewRequestVersion.current
            || dataVersion !== projectDataVersion.current
            || activeProjectIdRef.current !== project.id
          ) {
            return
          }
          setArtifactPreview(preview)
        } catch (error) {
          if (requestVersion === artifactPreviewRequestVersion.current && dataVersion === projectDataVersion.current) {
            setMessage(safeMessage(error))
          }
        }
  }

  async function downloadArtifact(name: string) {
        if (!activeProject) {
          return
        }
        try {
          const blob = await client.downloadArtifact(activeProject.id, toOptionalPath(configPath), name)
          const url = URL.createObjectURL(blob)
          const anchor = document.createElement('a')
          anchor.href = url
          anchor.download = name
          anchor.rel = 'noopener'
          anchor.click()
          URL.revokeObjectURL(url)
          setMessage(`Downloaded ${name}.`)
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
        resetProjectScopedData()
        setStatus(null)
        activeProjectIdRef.current = null
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

  function resetProjectScopedData() {
    projectDataVersion.current += 1
    reviewRequestVersion.current += 1
    artifactPreviewRequestVersion.current += 1
    setIsMutating(false)
    setReviewItems([])
    setReviewPage(1)
    setReviewTotal(0)
    setSelectedControls(new Set())
    setGuidanceItems([])
    setGuidanceAffectsRuns(false)
    setGuidanceForm({ control_id: '', policy_id: '', display_name: '', guidance: '', source: 'human-review', provenance: '' })
    setArtifacts([])
    setArtifactPreview(null)
    setSourceResult(null)
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
            <p className="lede">Turn a published standard into reviewable Azure Policy artifacts through one guided local workflow.</p>
          </div>
          <details className="config-menu">
            <summary>Config</summary>
            <div className="config-popover">
              <div>
                <strong>Appearance</strong>
                <p>System-wide portal preferences live here.</p>
              </div>
              <label className="switch-row">
                <span>Dark theme</span>
                <input
                  type="checkbox"
                  role="switch"
                  aria-label={`Dark theme; currently ${resolved}`}
                  checked={resolved === 'dark'}
                  onChange={(event) => setPreference(event.target.checked ? 'dark' : 'light')}
                />
              </label>
              <button type="button" className="secondary compact" onClick={() => setPreference('system')} disabled={preference === 'system'}>
                Use system setting
              </button>
            </div>
          </details>
        </header>

        <nav className="app-nav" aria-label="Primary navigation">
          {PORTAL_PAGES.map((page) => (
            <button
              key={page.id}
              type="button"
              aria-current={activePage === page.id ? 'page' : undefined}
              disabled={!isConnected && page.id !== 'home'}
              onClick={() => setActivePage(page.id)}
            >
              {page.label}
            </button>
          ))}
        </nav>

        <main id="main-content" className="dashboard" tabIndex={-1}>
        {activePage === 'home' ? (
          <section className="panel home-intro" aria-labelledby="welcome-heading">
            <div className="home-hero">
              <div>
                <p className="eyebrow">Start here</p>
                <h2 id="welcome-heading">From standard to Azure Policy, step by step</h2>
                <p className="lede">Control Translator keeps source material, mapping decisions, guidance, runs, and generated outputs together in a local project.</p>
              </div>
              <span className="badge">{isConnected ? 'Local API connected' : 'Connect to begin'}</span>
            </div>
            <ol className="getting-started">
              <li><strong>Create</strong><span>Create or open a project, then add a CSV, XLSX, or trusted HTTPS standard.</span></li>
              <li><strong>Run</strong><span>Translate the standard through the six-stage pipeline and monitor durable progress.</span></li>
              <li><strong>Review</strong><span>Approve mappings, reject mismatches, manage OOS candidates, and record reusable guidance.</span></li>
              <li><strong>Outputs</strong><span>Preview and download the allow-listed Azure Policy artifacts produced by successful runs.</span></li>
            </ol>
            {isConnected ? (
              <div className="card-actions left-actions">
                <button type="button" onClick={() => setActivePage('create')}>{projects.length === 0 ? 'Create your first project' : 'Open project setup'}</button>
                {activeProject ? <button type="button" className="secondary" onClick={() => setActivePage('run')}>Continue with {activeProject.name}</button> : null}
              </div>
            ) : null}
            {isConnected && projects.length === 0 ? <p className="notice subtle">No projects yet. Start with Create to initialize a local workspace.</p> : null}
          </section>
        ) : null}

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
                <h2 ref={dashboardHeadingRef} id="dashboard-heading" tabIndex={-1}>{PORTAL_PAGES.find((page) => page.id === activePage)?.label}</h2>
                {projectLocked ? <p className="lock-text">Run {shortRunId(activeRun.id)} owns the project lock; source and delete mutations are disabled.</p> : null}
              </div>
              <div className="card-actions">
                <button type="button" className="secondary" onClick={loadProjects} disabled={isProjectsBusy}>Refresh projects</button>
                <button type="button" className="secondary" onClick={disconnect}>Disconnect</button>
              </div>
            </section>

            {message ? <div className="notice" role="status">{message}</div> : null}
            {pollMessage ? <div className="notice subtle" role="status">{pollMessage}</div> : null}

            <section className={activePage === 'create' ? 'grid-layout' : 'stack'}>
              <div className="stack">
                <section id="create-project" className="panel" aria-labelledby="create-heading" hidden={activePage !== 'create'}>
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

                <section id="projects" className="panel" aria-labelledby="projects-heading" hidden={activePage !== 'create'}>
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

                <section id="source-setup" className="panel" aria-labelledby="source-heading" hidden={activePage !== 'create'}>
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

                <section id="runs" className="panel" aria-labelledby="runs-heading" hidden={activePage !== 'run'}>
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

                <section id="mapping-review" className="panel" aria-labelledby="review-heading" hidden={activePage !== 'review'}>
                  <div className="section-heading">
                    <h2 id="review-heading">Mapping review</h2>
                    <span className="badge">{reviewItems.length} shown</span>
                  </div>
                  {activeProject ? (
                    <>
                      <form className="filter-row" onSubmit={(event) => { event.preventDefault(); setReviewPage(1); void refreshReviewData(activeProject, 1) }}>
                        <label>
                          <span>Search mappings</span>
                          <input value={reviewQuery} onChange={(event) => { setReviewQuery(event.target.value); setReviewPage(1) }} maxLength={200} />
                        </label>
                        <label>
                          <span>Status</span>
                          <select value={reviewStatus} onChange={(event) => { setReviewStatus(event.target.value); setReviewPage(1) }}>
                            <option value="review">Pending review</option>
                            <option value="include">Approved</option>
                            <option value="ignore">Rejected</option>
                            <option value="">All</option>
                          </select>
                        </label>
                        <button type="submit">Refresh review</button>
                      </form>
                      <div className="card-actions left-actions">
                        <button type="button" onClick={() => mutateSelectedMappings('approve')} disabled={projectLocked || isMutating || selectedControls.size === 0}>Approve selected</button>
                        <button type="button" className="danger" onClick={() => mutateSelectedMappings('reject')} disabled={projectLocked || isMutating || selectedControls.size === 0}>Reject selected</button>
                      </div>
                      <nav className="pagination-row" aria-label="Mapping review pages">
                        <button type="button" className="secondary" onClick={() => { const page = Math.max(1, reviewPage - 1); setReviewPage(page); void refreshReviewData(activeProject, page) }} disabled={isMutating || reviewPage <= 1}>Previous page</button>
                        <span aria-live="polite">Page {reviewPage} of {reviewPageCount}; {reviewTotal} mapping{reviewTotal === 1 ? '' : 's'} total</span>
                        <button type="button" className="secondary" onClick={() => { const page = Math.min(reviewPageCount, reviewPage + 1); setReviewPage(page); void refreshReviewData(activeProject, page) }} disabled={isMutating || reviewPage >= reviewPageCount}>Next page</button>
                      </nav>
                      <ul className="review-list" aria-label="Mapping review results">
                        {reviewItems.map((item) => (
                          <li key={item.control_id}>
                            <article className="review-card">
                              <label className="check-row">
                                <input type="checkbox" checked={selectedControls.has(item.control_id)} onChange={() => toggleControl(item.control_id)} disabled={projectLocked || isMutating} />
                                <span><strong>{item.control_id}</strong> <span className={`state-pill ${item.decision}`}>{item.decision}</span></span>
                              </label>
                              <p>Confidence {(item.confidence * 100).toFixed(0)}%. {item.rationale}</p>
                              <ul>
                                {item.policies.map((policy) => <li key={policy.id}>{policy.name || policy.id}</li>)}
                              </ul>
                              <button type="button" className="secondary" onClick={() => addFirstPolicyToOos(item)} disabled={projectLocked || isMutating || item.policies.length === 0}>Promote first policy to OOS candidate</button>
                            </article>
                          </li>
                        ))}
                      </ul>
                      {reviewItems.length === 0 ? <StateCard title="No mappings match" text="Refresh after a run or change the filters." /> : null}
                    </>
                  ) : <StateCard title="Open a project first" text="Mapping review loads after a project is opened." />}
                </section>

                <section id="guidance" className="panel" aria-labelledby="guidance-heading" hidden={activePage !== 'review'}>
                  <div className="section-heading">
                    <h2 id="guidance-heading">Local guidance</h2>
                    <span className="badge">{guidanceAffectsRuns ? 'Affects future runs' : 'Stored locally'}</span>
                  </div>
                  {activeProject ? (
                    <>
                      <p className="lock-text">Guidance is project-local calibration for future mapping runs; it does not rewrite current decisions or artifacts.</p>
                      <form className="form-grid" onSubmit={submitGuidance}>
                        <label><span>Control ID</span><input value={guidanceForm.control_id} onChange={(event) => setGuidanceForm({ ...guidanceForm, control_id: event.target.value })} required disabled={projectLocked || isMutating} /></label>
                        <label><span>Policy ID</span><input value={guidanceForm.policy_id} onChange={(event) => setGuidanceForm({ ...guidanceForm, policy_id: event.target.value })} required disabled={projectLocked || isMutating} /></label>
                        <label><span>Policy display name</span><input value={guidanceForm.display_name} onChange={(event) => setGuidanceForm({ ...guidanceForm, display_name: event.target.value })} disabled={projectLocked || isMutating} /></label>
                        <label><span>Source/provenance</span><input value={guidanceForm.provenance} onChange={(event) => setGuidanceForm({ ...guidanceForm, provenance: event.target.value })} placeholder="review meeting, ticket, authority note" required disabled={projectLocked || isMutating} /></label>
                        <label><span>Guidance rationale</span><textarea value={guidanceForm.guidance} onChange={(event) => setGuidanceForm({ ...guidanceForm, guidance: event.target.value })} maxLength={2000} required disabled={projectLocked || isMutating} /></label>
                        <button type="submit" disabled={projectLocked || isMutating}>Save guidance</button>
                      </form>
                      <ul className="review-list" aria-label="Local guidance entries">
                        {guidanceItems.map((item) => (
                          <li key={item.id}>
                            <article className="review-card">
                              <h3>{item.control_id} → {item.display_name || item.policy_id}</h3>
                              <p>{item.include_reasoning}</p>
                              <p className="lock-text">Source: {item.source}; provenance: {item.provenance}</p>
                              <button type="button" className="danger" onClick={() => deleteGuidance(item.id)} disabled={projectLocked || isMutating}>Delete guidance</button>
                            </article>
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : <StateCard title="Open a project first" text="Guidance is isolated to the active project workspace." />}
                </section>

                <section id="artifacts" className="panel" aria-labelledby="artifacts-heading" hidden={activePage !== 'outputs'}>
                  <div className="section-heading">
                    <h2 id="artifacts-heading">Generated artifacts</h2>
                    <span className="badge">{artifacts.length} available</span>
                  </div>
                  {activeProject ? (
                    <>
                      <button type="button" className="secondary" onClick={() => refreshReviewData()} disabled={isMutating}>Refresh artifact inventory</button>
                      <ul className="review-list" aria-label="Generated artifact inventory">
                        {artifacts.map((artifact) => (
                          <li key={artifact.name}>
                            <article className="review-card">
                              <h3>{artifact.name}</h3>
                              <p>{artifact.content_type}; {artifact.size_bytes.toLocaleString()} bytes</p>
                              <div className="card-actions left-actions">
                                <button type="button" onClick={() => previewArtifact(artifact.name)} disabled={!artifact.previewable}>Preview</button>
                                <button type="button" className="secondary" onClick={() => downloadArtifact(artifact.name)}>Download</button>
                              </div>
                            </article>
                          </li>
                        ))}
                      </ul>
                      {artifactPreview ? (
                        <article className="artifact-preview" aria-live="polite">
                          <h3>Preview: {artifactPreview.name}</h3>
                          {artifactPreview.truncated ? <p className="lock-text">Preview truncated to the safe bounded limit.</p> : null}
                          <pre>{artifactPreview.text}</pre>
                        </article>
                      ) : null}
                      {artifacts.length === 0 ? <StateCard title="No artifacts yet" text="Run the pipeline with distribution enabled to generate allow-listed Azure Policy files." /> : null}
                    </>
                  ) : <StateCard title="Open a project first" text="Artifacts are scoped to the opened project and exposed only by allow-listed names." />}
                </section>
              </div>

              <aside id="project-status" className="panel status-panel" aria-labelledby="status-heading" hidden={activePage !== 'create'}>
                <h2 id="status-heading">Open project</h2>
                <label>
                  <span>Configuration path for open/status</span>
                  <input value={configPath} onChange={(event) => { setConfigPath(event.target.value); setReviewPage(1) }} maxLength={4096} disabled={projectLocked} />
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
