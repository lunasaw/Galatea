import { realpath, stat } from 'node:fs/promises'
import { isAbsolute, relative } from 'node:path'
import { parse } from 'yaml'
import {
  loadProjectManifest,
  resolveProjectPath,
  type TrainingProjectManifest,
} from './policies/project.ts'
import {
  GalateaController,
  type GalateaControllerOptions,
} from './tools/controller.ts'

/** One project entry accepted by the registry configuration parser. */
export interface ConfiguredProjectEntry {
  readonly id: string
  readonly projectRoot: string
  readonly releaseRoot: string
  readonly manifestPath?: string
}

/** A legacy single-project configuration that can be adapted before parsing. */
export interface LegacySingleProjectConfig {
  readonly projectRoot: string
  readonly releaseRoot: string
  readonly manifestPath?: string
}

/** A project entry after roots and its manifest have been resolved and validated. */
export interface ResolvedProjectEntry extends ConfiguredProjectEntry {
  readonly projectRoot: string
  readonly releaseRoot: string
  readonly manifestPath: string
  readonly manifest: TrainingProjectManifest
}

/** The service capabilities needed by one Galatea controller. */
export type GalateaControllerServices = Pick<GalateaControllerOptions, 'process' | 'ray' | 'mlflow'>

/** Factory for shared service clients. Returning the same clients for each entry is supported. */
export type GalateaServiceFactory<Services extends GalateaControllerServices = GalateaControllerServices> = (
  entry: ResolvedProjectEntry,
) => Services | Promise<Services>

/** Constructor used by the registry; injectable for a host's compatible controller implementation. */
export type GalateaControllerConstructor = new (options: GalateaControllerOptions) => GalateaController

/** A compact model-facing and diagnostic description of one registered project. */
export interface ProjectSummary {
  readonly id: string
  readonly manifestName: string
  readonly task: string
  readonly objective: TrainingProjectManifest['spec']['objective']
  readonly experimentName: string
  readonly projectRoot: string
  readonly releaseRoot: string
  readonly manifestPath: string
}

/** A Harness Agent session reference. The caller passes `agent.session` or its equivalent. */
export interface ProjectSelectionSession {
  readonly id: string
}

/**
 * Durable per-session selection storage supplied by the host.
 *
 * The registry deliberately does not retain session selections. A host can implement this interface
 * with a Session projection, persistence-backed key/value service, or another durable store. `read`
 * must return `undefined` when no selection exists; stored values are validated before use.
 */
export interface DurableProjectSelectionStore {
  /** Read the selected project id for one durable session identity. */
  read(sessionId: string): unknown | Promise<unknown>
  /** Commit the selected project id for one durable session identity; `null` clears it. */
  write(sessionId: string, projectId: string | null): void | Promise<void>
}

/** A project summary plus its resolved controller, for host-side registration. */
export interface RegisteredProject<Controller extends GalateaController = GalateaController> {
  readonly entry: ResolvedProjectEntry
  readonly summary: ProjectSummary
  readonly controller: Controller
}

const PROJECT_ID = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$/
const DEFAULT_MANIFEST_PATH = 'galatea.project.yaml'

function object(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${path} must be an object`)
  }
  return value as Record<string, unknown>
}

function text(value: unknown, path: string): string {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new TypeError(`${path} must be a non-empty string`)
  }
  if (value !== value.trim() || value.includes('\u0000')) {
    throw new TypeError(`${path} contains unsupported whitespace or NUL characters`)
  }
  return value
}

function projectId(value: unknown, path: string): string {
  const id = text(value, path)
  if (!PROJECT_ID.test(id)) {
    throw new TypeError(`${path} must start with an alphanumeric character and contain only letters, numbers, ., _, or -`)
  }
  return id
}

function absoluteRoot(value: unknown, path: string): string {
  const root = text(value, path)
  if (!isAbsolute(root)) throw new TypeError(`${path} must be an absolute path`)
  return root
}

function relativeManifestPath(value: unknown, path: string): string {
  const manifestPath = text(value, path)
  const normalized = manifestPath.replaceAll('\\', '/')
  if (isAbsolute(manifestPath) || normalized.split('/').includes('..')) {
    throw new TypeError(`${path} must stay below projectRoot`)
  }
  return manifestPath
}

function entry(value: unknown, path: string): ConfiguredProjectEntry {
  const input = object(value, path)
  const allowed = new Set(['id', 'projectRoot', 'releaseRoot', 'manifestPath'])
  const unknown = Object.keys(input).filter(key => !allowed.has(key))
  if (unknown.length > 0) throw new TypeError(`${path} contains unknown fields: ${unknown.join(', ')}`)
  const manifestPath = input['manifestPath'] === undefined
    ? DEFAULT_MANIFEST_PATH
    : relativeManifestPath(input['manifestPath'], `${path}.manifestPath`)
  return {
    id: projectId(input['id'], `${path}.id`),
    projectRoot: absoluteRoot(input['projectRoot'], `${path}.projectRoot`),
    releaseRoot: absoluteRoot(input['releaseRoot'], `${path}.releaseRoot`),
    manifestPath,
  }
}

/**
 * Convert the existing single-project plugin config into one registry entry.
 *
 * The parent should pass a stable id when one is available. The `default` id keeps old configs
 * parseable without inspecting the manifest during configuration parsing; aliases need not equal
 * `metadata.name` and are shown separately in summaries.
 *
 * @param value - legacy projectRoot/releaseRoot configuration.
 * @param id - registry id to assign; defaults to `default`.
 * @returns a normalized registry entry.
 */
export function adaptSingleProjectConfig(value: unknown, id = 'default'): ConfiguredProjectEntry {
  const input = object(value, 'singleProject')
  const allowed = new Set(['projectRoot', 'releaseRoot', 'manifestPath'])
  const unknown = Object.keys(input).filter(key => !allowed.has(key))
  if (unknown.length > 0) throw new TypeError(`singleProject contains unknown fields: ${unknown.join(', ')}`)
  return entry({
    id,
    projectRoot: input['projectRoot'],
    releaseRoot: input['releaseRoot'],
    ...(input['manifestPath'] === undefined ? {} : { manifestPath: input['manifestPath'] }),
  }, 'singleProject')
}

/**
 * Parse and validate a project list or `{ projects: [...] }` registry config.
 *
 * @param value - untrusted plugin configuration value.
 * @returns detached, validated entries in declaration order.
 */
export function parseProjectEntries(value: unknown): readonly ConfiguredProjectEntry[] {
  const items: unknown[] = Array.isArray(value)
    ? value
    : (() => {
        const input = object(value, 'projects')
        const keys = Object.keys(input)
        if (keys.some(key => key !== 'projects')) {
          throw new TypeError(`projects contains unknown fields: ${keys.filter(key => key !== 'projects').join(', ')}`)
        }
        if (!Array.isArray(input['projects'])) throw new TypeError('projects.projects must be an array')
        return input['projects']
      })()
  if (items.length === 0) throw new TypeError('projects must contain at least one entry')
  const entries = items.map((item, index) => entry(item, `projects[${index}]`))
  const ids = new Set<string>()
  for (const item of entries) {
    if (ids.has(item.id)) throw new TypeError(`projects contains duplicate id: ${item.id}`)
    ids.add(item.id)
  }
  return entries
}

async function existingDirectory(path: string, field: string): Promise<string> {
  const canonical = await realpath(path)
  const details = await stat(canonical)
  if (!details.isDirectory()) throw new TypeError(`${field} must resolve to a directory`)
  return canonical
}

function inside(root: string, target: string): boolean {
  const child = relative(root, target)
  return child === '' || (child !== '..' && !child.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`) && !isAbsolute(child))
}

/**
 * Resolve one configured entry, reject symlink escapes, and load its validated manifest.
 *
 * @param configured - parsed project entry.
 * @returns canonical roots, manifest path, and detached manifest.
 */
export async function resolveProjectEntry(configured: ConfiguredProjectEntry): Promise<ResolvedProjectEntry> {
  const projectRoot = await existingDirectory(configured.projectRoot, 'projectRoot')
  const releaseRoot = await existingDirectory(configured.releaseRoot, 'releaseRoot')
  const configuredManifestPath = configured.manifestPath ?? DEFAULT_MANIFEST_PATH
  const manifestPath = await resolveProjectPath(projectRoot, configuredManifestPath)
  if (!inside(projectRoot, manifestPath)) throw new Error(`manifestPath resolves outside projectRoot: ${configuredManifestPath}`)
  const manifest = await loadProjectManifest(manifestPath)
  return { ...configured, projectRoot, releaseRoot, manifestPath, manifest }
}

/** Resolve all entries concurrently and reject duplicate resolved manifest identities. */
export async function resolveProjectEntries(
  configured: readonly ConfiguredProjectEntry[],
): Promise<readonly ResolvedProjectEntry[]> {
  const resolved = await Promise.all(configured.map(resolveProjectEntry))
  const manifestNames = new Set<string>()
  for (const item of resolved) {
    if (manifestNames.has(item.manifest.metadata.name)) {
      throw new TypeError(`projects resolve to duplicate manifest name: ${item.manifest.metadata.name}`)
    }
    manifestNames.add(item.manifest.metadata.name)
  }
  return resolved
}

function summary(entry: ResolvedProjectEntry): ProjectSummary {
  return {
    id: entry.id,
    manifestName: entry.manifest.metadata.name,
    task: entry.manifest.spec.task,
    objective: { ...entry.manifest.spec.objective },
    experimentName: entry.manifest.spec.mlflow.experimentName,
    projectRoot: entry.projectRoot,
    releaseRoot: entry.releaseRoot,
    manifestPath: entry.manifestPath,
  }
}

/**
 * Construct the controller for one resolved project from shared service capabilities.
 *
 * @param entry - resolved registry project.
 * @param serviceFactory - host-owned factory for process, Ray, and MLflow clients.
 * @param controllerConstructor - compatible controller constructor.
 * @returns a configured Galatea controller.
 */
export async function createProjectController<
  Services extends GalateaControllerServices = GalateaControllerServices,
  Controller extends GalateaController = GalateaController,
>(
  entry: ResolvedProjectEntry,
  serviceFactory: GalateaServiceFactory<Services>,
  controllerConstructor: new (options: GalateaControllerOptions) => Controller = GalateaController as new (options: GalateaControllerOptions) => Controller,
): Promise<Controller> {
  const services = await serviceFactory(entry)
  return new controllerConstructor({
    projectRoot: entry.projectRoot,
    releaseRoot: entry.releaseRoot,
    manifest: entry.manifest,
    ...services,
  })
}

/**
 * Secure registry of resolved Galatea projects and lazily constructed controllers.
 * Session selections are intentionally kept outside this registry.
 */
export class GalateaProjectRegistry<
  Services extends GalateaControllerServices = GalateaControllerServices,
  Controller extends GalateaController = GalateaController,
> {
  private readonly byId: ReadonlyMap<string, ResolvedProjectEntry>
  private readonly serviceFactory: GalateaServiceFactory<Services>
  private readonly controllerConstructor: new (options: GalateaControllerOptions) => Controller
  private readonly controllers = new Map<string, Promise<Controller>>()

  private constructor(
    entries: readonly ResolvedProjectEntry[],
    serviceFactory: GalateaServiceFactory<Services>,
    controllerConstructor: new (options: GalateaControllerOptions) => Controller,
  ) {
    this.byId = new Map(entries.map(item => [item.id, item]))
    this.serviceFactory = serviceFactory
    this.controllerConstructor = controllerConstructor
  }

  /** Load and construct a registry from untrusted configured entries. */
  static async create<
    Services extends GalateaControllerServices = GalateaControllerServices,
    Controller extends GalateaController = GalateaController,
  >(
    value: unknown,
    serviceFactory: GalateaServiceFactory<Services>,
    controllerConstructor: new (options: GalateaControllerOptions) => Controller = GalateaController as new (options: GalateaControllerOptions) => Controller,
  ): Promise<GalateaProjectRegistry<Services, Controller>> {
    const configured = parseProjectEntries(value)
    const entries = await resolveProjectEntries(configured)
    return new GalateaProjectRegistry(entries, serviceFactory, controllerConstructor)
  }

  /** Return summaries in stable configuration order. */
  listSummaries(): readonly ProjectSummary[] {
    return [...this.byId.values()].map(summary)
  }

  /** Return a detached resolved entry by id, or undefined when absent. */
  getEntry(id: string): ResolvedProjectEntry | undefined {
    const value = this.byId.get(id)
    return value === undefined ? undefined : { ...value, manifest: value.manifest }
  }

  /** Return a project summary by id, or undefined when absent. */
  getSummary(id: string): ProjectSummary | undefined {
    const value = this.byId.get(id)
    return value === undefined ? undefined : summary(value)
  }

  /** Return the lazily constructed controller for a known project id. */
  async getController(id: string): Promise<Controller> {
    const entry = this.byId.get(id)
    if (entry === undefined) throw new Error(`unknown Galatea project id: ${id}`)
    const existing = this.controllers.get(id)
    if (existing !== undefined) return await existing
    const pending = createProjectController(entry, this.serviceFactory, this.controllerConstructor)
    this.controllers.set(id, pending)
    try {
      return await pending
    } catch (error: unknown) {
      this.controllers.delete(id)
      throw error
    }
  }

  /** Construct a selector backed by host-owned durable per-session storage. */
  selector(store: DurableProjectSelectionStore): ProjectSessionSelector<Services, Controller> {
    return new ProjectSessionSelector(this, store)
  }
}

function sessionId(session: ProjectSelectionSession): string {
  const id = session.id
  if (typeof id !== 'string' || id.trim() === '' || id !== id.trim()) {
    throw new TypeError('session.id must be a non-empty string')
  }
  return id
}

/** Per-session project selection facade backed exclusively by durable host storage. */
export class ProjectSessionSelector<
  Services extends GalateaControllerServices = GalateaControllerServices,
  Controller extends GalateaController = GalateaController,
> {
  private readonly registry: GalateaProjectRegistry<Services, Controller>
  private readonly store: DurableProjectSelectionStore

  constructor(
    registry: GalateaProjectRegistry<Services, Controller>,
    store: DurableProjectSelectionStore,
  ) {
    this.registry = registry
    this.store = store
  }

  /** Read and validate the selected project for a session, if one has been committed. */
  async selectedProject(session: ProjectSelectionSession): Promise<ProjectSummary | undefined> {
    const selected = await this.store.read(sessionId(session))
    if (selected === undefined || selected === null) return undefined
    const id = projectId(selected, 'stored project selection')
    const result = this.registry.getSummary(id)
    if (result === undefined) throw new Error(`session selects unknown Galatea project id: ${id}`)
    return result
  }

  /** Commit a known project id to durable storage and return its summary. */
  async selectProject(session: ProjectSelectionSession, id: string): Promise<ProjectSummary> {
    const normalized = projectId(id, 'project id')
    const result = this.registry.getSummary(normalized)
    if (result === undefined) throw new Error(`unknown Galatea project id: ${normalized}`)
    await this.store.write(sessionId(session), normalized)
    return result
  }

  /** Clear the current session selection through the store's null value. */
  async clearProject(session: ProjectSelectionSession): Promise<void> {
    await this.store.write(sessionId(session), null)
  }
}

/** Parse a YAML project-entry document before passing it to {@link parseProjectEntries}. */
export function parseProjectEntriesYaml(source: string): readonly ConfiguredProjectEntry[] {
  return parseProjectEntries(parse(source))
}
