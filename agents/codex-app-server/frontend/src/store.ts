import { nativeNotice, invalidatesNativeResources, type NativeNotice } from "./native-notifications";
import { exactSlashCommand, initPrompt } from "./slash-commands";
import { visibleEfforts } from "./configuration-data";
import type {
  Capabilities,
  EventPage,
  Health,
  NativeEvent,
  Session,
  Snapshot,
  NativeRequest,
  NativeProtocol,
} from "./types";
import { NativeProjects, projectPages } from "./native-projects";
import {
  emptyLayout,
  mergeProjects,
  parseProjectLayout,
  normalizeProjectPath,
  projectForPath,
  projectName,
  type ProjectLayout,
} from "./project-state";
import {
  permissionSettings,
  type PermissionPreset,
} from "./permission-presets";

declare global {
  var ConsoleTransport: {
    consumeSSE(
      body: ReadableStream<Uint8Array>,
      callback: (frame: {
        event: string;
        data: string;
      }) => Promise<void> | void,
    ): Promise<void>;
    compareCursor(a: string, b: string): number;
  };
}
export class ApiError extends Error {
  constructor(
    public code: string,
    public status: number,
  ) {
    super(`${status} · ${code}`);
  }
}
export async function api<T>(
  path: string,
  body?: unknown,
  key?: string,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    signal,
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "Idempotency-Key": key! },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok)
    throw new ApiError(data.error || "请求失败", response.status);
  return data;
}
export const requestKey = () =>
  "web-" +
  Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
export const busyStatuses = new Set([
  "starting",
  "running",
  "interrupting",
  "closing",
]);
function mergeSessionSettings(previous: Record<string, any> | undefined, update: Record<string, unknown>) {
  const next = { ...previous };
  if (update.permissions) { delete next.sandbox; delete next.sandboxPolicy; }
  else if (update.sandboxPolicy) delete next.permissions;
  return { ...next, ...update };
}
export const statusLabel = (status: string) =>
  ({
    ready: "就绪",
    starting: "正在启动",
    running: "正在回复",
    interrupted: "已中断",
    interrupting: "正在停止",
    closed: "已关闭",
    closing: "正在关闭",
    recovery_required: "需要恢复",
    completed: "已完成",
    failed: "失败",
    inProgress: "进行中",
    executing: "执行中",
  })[status] || status;
export type ConsoleState = {
  sessions: Session[];
  active: string | null;
  snapshot: Snapshot | null;
  health: Health | null;
  capabilities: Capabilities | null;
  events: NativeEvent[];
  eventPage: EventPage | null;
  sessionsLoading: boolean;
  sessionRefreshing: boolean;
  loading: boolean;
  creating: boolean;
  submitting: boolean;
  error: string;
  streamError: string;
  nativeNotices: NativeNotice[];
  reset: boolean;
  titles: Record<string, string>;
  history: boolean;
  older: boolean;
  eventLive: boolean;
  global: boolean;
  nativeRequests: NativeRequest[];
  nativeProtocol: NativeProtocol | null;
  models: Array<{
    model: string;
    displayName?: string;
    supportedReasoningEfforts?: Array<{ reasoningEffort: string }>;
    defaultReasoningEffort?: string;
  }>;
  preferences: { model: string; effort: string; mode: string };
  settingsBusy: boolean;
  goalInputs: Record<string, boolean>;
  goalRevision: number;
  queueRevision: number;
  projectLayout: ProjectLayout;
  threadSections: Array<{ id: string; name: string }>;
  projectBusy: boolean;
  draftCwd: string;
  draftProjectId: string;
  draftPermission: PermissionPreset | null;
  runtimeDefaults: Record<string, any>;
  hiddenReasoningEfforts: string[];
};

export class ConsoleStore {
  private nativeProjects = new NativeProjects((method, params) => this.nativeCall(method, params));
  private nativeProjectMode = false;
  private projectCatalogReady = false;
  private projectWrites: Promise<boolean> = Promise.resolve(true);
  private pendingProjectWrites = 0;
  private defaults = { model: "", effort: "medium", mode: "default" };
  private draftModelOverrides = new Set<string>();
  private listeners = new Set<() => void>();
  private controller?: AbortController;
  private initController?: AbortController;
  private generation = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private refreshQueued = false;
  private refreshing = false;
  private actions = new Map<string, string>();
  private pending?: {
    id: string;
    text: string;
    key: string;
    input?: Record<string, unknown>[];
    options?: Record<string, unknown>;
  };
  private goalSubmissions = new Map<string, { id?: string; objective: string; key: string }>();
  private historyPage: string | null = null;
  private historyDepth = 0;
  private paging = false;
  private titleSequences = new Map<string, bigint>();
  private filter = { method: "", direction: "", kind: "" };
  private filterVersion = 0;
  state: ConsoleState = {
    sessions: [],
    active: null,
    snapshot: null,
    health: null,
    capabilities: null,
    events: [],
    eventPage: null,
    sessionsLoading: true,
    sessionRefreshing: false,
    loading: false,
    creating: false,
    submitting: false,
    error: "",
    streamError: "",
    nativeNotices: [],
    reset: false,
    titles: {},
    history: false,
    older: false,
    eventLive: true,
    global: false,
    nativeRequests: [],
    nativeProtocol: null,
    models: [],
    preferences: { model: "", effort: "medium", mode: "default" },
    settingsBusy: false,
    goalInputs: {},
    goalRevision: 0,
    queueRevision: 0,
    projectLayout: emptyLayout(),
    threadSections: [],
    projectBusy: false,
    draftCwd: "",
    draftProjectId: "",
    draftPermission: null,
    runtimeDefaults: {},
    hiddenReasoningEfforts: [],
  };
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = () => this.state;
  patch(update: Partial<ConsoleState>) {
    this.state = { ...this.state, ...update };
    this.listeners.forEach((fn) => fn());
  }
  fail = (error: unknown) => {
    if (!(error instanceof DOMException && error.name === "AbortError"))
      this.patch({
        error: error instanceof Error ? error.message : String(error),
      });
  };
  async init() {
    this.initController?.abort();
    const controller = new AbortController();
    this.initController = controller;
    const { signal } = controller;
    const selectionVersion = this.generation;
    const linkedSession = typeof window === "undefined" ? null : new URL(window.location.href).searchParams.get("session");
    const previous = linkedSession || localStorage.getItem("codex-agent.active");
    const report = (error: unknown) => { if (!signal.aborted) this.fail(error); };
    try {
      const hidden = JSON.parse(localStorage.getItem("codex-agent.hidden-efforts") || "[]");
      if (Array.isArray(hidden)) this.patch({ hiddenReasoningEfforts: hidden.filter((v): v is string => typeof v === "string") });
      const permission = localStorage.getItem("codex-agent.draft-permission");
      this.patch({
        projectLayout: parseProjectLayout(
          localStorage.getItem("codex-agent.projects"),
        ),
        draftCwd: localStorage.getItem("codex-agent.project") || "",
        draftProjectId: localStorage.getItem("codex-agent.project-id") || "",
        draftPermission: ["ask", "auto", "full"].includes(permission || "")
          ? (permission as PermissionPreset)
          : null,
      });
      const titles = localStorage.getItem("codex-agent.titles");
      if (titles) this.patch({ titles: JSON.parse(titles) });
    } catch (error) {
      report(error);
    }
    // Publish each surface as soon as it arrives. Only project migration needs
    // both the protocol and session catalog; it must not gate chat restoration.
    const sessionsReady = api<Session[]>("/api/sessions", undefined, undefined, signal)
      .then((sessions) => {
        if (!signal.aborted) this.patch({ sessions });
        return sessions;
      }).finally(() => { if (!signal.aborted) this.patch({ sessionsLoading: false }); });
    const protocolReady = api<NativeProtocol>("/api/native/protocol", undefined, undefined, signal)
      .then((nativeProtocol) => {
        if (!signal.aborted) this.patch({ nativeProtocol });
        return nativeProtocol;
      });
    const restore = sessionsReady.then(async (sessions) => {
      if (!signal.aborted && selectionVersion === this.generation && linkedSession && !sessions.some(s => s.id === linkedSession))
        report(new Error("链接中的会话不存在或无法访问"));
      if (!signal.aborted && selectionVersion === this.generation && previous && sessions.some((s) => s.id === previous))
        await this.select(previous, false, { refreshMetadata: false, clearError: false });
    });
    const projects = Promise.all([sessionsReady, protocolReady]).then(async ([sessions, nativeProtocol]) => {
      if (signal.aborted) return;
      if (nativeProtocol.methods?.includes("project/list")) {
        const old = { ...this.state.projectLayout };
        // Bind legacy memberships before switching the authoritative catalog.
        old.threadProjects = Object.fromEntries(sessions.map((s) => [s.id, this.projectForSession(s.id)?.id || ""]));
        this.nativeProjectMode = true;
        // Invalidate both sidebar groups and memoized thread memberships together.
        this.patch({ projectLayout: { ...this.state.projectLayout } });
        const loaded = await this.nativeProjects.load();
        if (signal.aborted) return;
        const marker = "codex-agent.projects-native:" + nativeProtocol.cwd;
        const mapping = localStorage.getItem(marker) ? new Map<string, string>() : await this.nativeProjects.migrate(old, sessions, loaded.projects);
        if (signal.aborted) return;
        await this.refreshProjects(mapping.size || old.sections.length ? undefined : loaded);
        if (signal.aborted) return;
        localStorage.setItem(marker, "1");
        const selected = mapping.get(this.state.draftProjectId);
        if (selected) this.patch({ draftProjectId: selected });
      } else this.saveProjectLayout({});
    });
    await Promise.all([
      sessionsReady, protocolReady, restore, projects,
      api<Health>("/api/console/status", undefined, undefined, signal)
        .then((health) => { if (!signal.aborted) this.patch({ health }); }),
      api<Capabilities>("/api/capabilities", undefined, undefined, signal)
        .then((capabilities) => { if (!signal.aborted) this.patch({ capabilities }); }),
      api<NativeRequest[]>("/api/native/requests", undefined, undefined, signal)
        .then((nativeRequests) => { if (!signal.aborted) this.patch({ nativeRequests }); }),
      this.loadDefaults(signal),
    ].map((task) => task.catch(report)));
  }
  private async loadDefaults(signal: AbortSignal) {
    const preferences = this.state.preferences;
    const revision = ++this.defaultsRevision;
    await Promise.all([
      (this.nativeCall("model/list", {}, signal) as Promise<any>).then((models) => {
        if (!signal.aborted) this.patch({ models: models.data || [] });
      }),
      (this.nativeCall("config/read", { includeLayers: false, ...(this.cwd ? { cwd: this.cwd } : {}) }, signal) as Promise<any>).then((config) => {
        if (signal.aborted || revision !== this.defaultsRevision) return;
        this.defaults = {
          model: config.config?.model || "",
          effort: config.config?.model_reasoning_effort || "medium",
          mode: "default",
        };
        this.patch({ runtimeDefaults: config.config || {},
          ...(this.state.preferences === preferences ? { preferences: this.defaults } : {}) });
      }),
    ]);
  }
  private defaultsRevision = 0;
  async refreshRuntimeDefaults(cwd = this.cwd) {
    const revision = ++this.defaultsRevision;
    const previous = this.defaults;
    const result = await this.nativeCall("config/read", { includeLayers: false, ...(cwd ? { cwd } : {}) }) as any;
    if (revision !== this.defaultsRevision) return;
    this.defaults = { model: result.config?.model || "", effort: result.config?.model_reasoning_effort || "medium", mode: "default" };
    const preferences = this.state.preferences;
    this.patch({ runtimeDefaults: result.config || {}, preferences: { ...preferences,
      ...(!this.draftModelOverrides.has("model") && preferences.model === previous.model ? { model: this.defaults.model } : {}),
      ...(!this.draftModelOverrides.has("effort") && preferences.effort === previous.effort ? { effort: this.defaults.effort } : {}),
    } });
  }
  setHiddenReasoningEfforts(hidden: string[]) {
    localStorage.setItem("codex-agent.hidden-efforts", JSON.stringify(hidden));
    this.patch({ hiddenReasoningEfforts: hidden });
  }
  reasoningEfforts(model: ConsoleState["models"][number] | undefined, current?: string) {
    return visibleEfforts(model?.supportedReasoningEfforts?.map((row) => row.reasoningEffort) || [], this.state.hiddenReasoningEfforts, current);
  }
  async refreshNativeRequests() {
    try {
      this.patch({
        nativeRequests: await api<NativeRequest[]>("/api/native/requests"),
      });
    } catch (error) {
      this.fail(error);
    }
  }
  async respondNative(id: string, result: unknown) {
    const key = requestKey();
    await api(
      `/api/native/requests/${encodeURIComponent(id)}`,
      { result },
      key,
    );
    await this.refreshNativeRequests();
  }
  async nativeCall(method: string, params: unknown, signal?: AbortSignal) {
    const key = requestKey();
    return api(`/api/native/call`, { method, params }, key, signal);
  }
  get cwd() {
    const session = this.state.sessions.find((s) => s.id === this.state.active);
    return (
      session?.cwd ||
      session?.settings?.cwd ||
      this.state.draftCwd ||
      this.state.nativeProtocol?.cwd ||
      ""
    );
  }
  get projects() {
    if (this.nativeProjectMode) return this.state.projectLayout.projects;
    return mergeProjects(this.state.projectLayout, [
      this.state.nativeProtocol?.cwd || "",
      this.state.draftCwd,
      ...this.state.sessions
        .filter((s) => !s.archived)
        .map((s) => s.cwd || s.settings?.cwd || ""),
    ]);
  }
  projectForSession(id: string) {
    const session = this.state.sessions.find((s) => s.id === id);
    if (!session) return undefined;
    if (this.nativeProjectMode) return this.projects.find((p) => p.id === session.settings?.projectId);
    if (this.state.projectLayout.threadProjects[id] === "") return undefined;
    const path = session.cwd || session.settings?.cwd || "";
    const projects = this.projects;
    const assigned = projects.find(
      (p) => p.id === this.state.projectLayout.threadProjects[id],
    );
    return assigned?.path === path ? assigned : projectForPath(projects, path);
  }
  get currentProject() {
    if (this.state.active) return this.projectForSession(this.state.active);
    return (
      this.projects.find((p) => p.id === this.state.draftProjectId) ||
      projectForPath(this.projects, this.cwd)
    );
  }
  saveProjectLayout(update: Partial<ProjectLayout>) {
    if (this.nativeProjectMode) {
      if (!Object.keys(update).length) return Promise.resolve(true);
      if (!this.projectCatalogReady) { this.fail(new Error("项目列表尚未加载，请刷新后重试")); return Promise.resolve(false); }
      const local = { collapsed: update.collapsed || this.state.projectLayout.collapsed,
        threadOrder: update.threadOrder || this.state.projectLayout.threadOrder };
      if (!update.projects && !update.sections && !update.threadProjects) {
        this.patch({ projectLayout: { ...this.state.projectLayout, ...local } });
        localStorage.setItem("codex-agent.projects", JSON.stringify(this.state.projectLayout));
        return Promise.resolve(true);
      }
      this.pendingProjectWrites++;
      this.patch({ projectBusy: true });
      const previous = structuredClone(this.state.projectLayout);
      this.projectWrites = this.projectWrites.then(async () => {
        try {
          await this.nativeProjects.update(previous, update, this.state.sessions);
          await this.refreshProjects();
          this.patch({ projectLayout: { ...this.state.projectLayout, ...local } });
          return true;
        } catch (error) { this.fail(error); await this.refreshProjects().catch(this.fail); return false; }
        finally { this.pendingProjectWrites--; this.patch({ projectBusy: this.pendingProjectWrites > 0 }); }
      });
      return this.projectWrites;
    }
    const threadProjects = { ...this.state.projectLayout.threadProjects };
    // Bind older sessions before a new project or a reorder can change any fallback.
    for (const session of this.state.sessions) {
      const project = this.projectForSession(session.id);
      if (project) threadProjects[session.id] = project.id;
    }
    const projectLayout = {
      ...this.state.projectLayout,
      projects: this.projects,
      ...update,
      threadProjects: { ...threadProjects, ...update.threadProjects },
    };
    localStorage.setItem("codex-agent.projects", JSON.stringify(projectLayout));
    this.patch({ projectLayout });
    return Promise.resolve(true);
  }
  addProject(path: string, name = "", section = "") {
    path = normalizeProjectPath(path);
    const baseName = name.trim() || projectName(path);
    let label = baseName;
    if (!name.trim()) {
      let index = 2;
      while (this.projects.some((p) => p.name === label))
        label = `${baseName} (${index++})`;
    }
    const project = { id: requestKey(), path, name: label, section };
    this.saveProjectLayout({ projects: [...this.projects, project] });
    return project;
  }
  async addProjectPersistent(path: string, name = "", section = "") {
    if (!this.nativeProjectMode) return this.addProject(path, name, section);
    if (!this.projectCatalogReady) throw new Error("项目列表尚未加载，请刷新后重试");
    const project = await this.nativeProjects.create(normalizeProjectPath(path), name.trim() || projectName(path), section);
    await this.refreshProjects(); return project;
  }
  async refreshProjects(loaded?: Awaited<ReturnType<NativeProjects["load"]>>) {
    const catalog = loaded || await this.nativeProjects.load();
    // The catalog can render while section memberships and ordering load.
    this.patch({ projectLayout: { ...this.state.projectLayout, projects: catalog.projects, sections: catalog.sections },
      threadSections: catalog.threadSections });
    // Read membership from native storage too, including edits made by another client.
    // Section-position ordering requires an actual section ID. Unsectioned
    // threads support the regular timestamp order only.
    const unsectioned = await projectPages((method, params) => this.nativeCall(method, params),
      "thread/list", { sectionId: null, sortKey: "updated_at", sortDirection: "desc" });
    const threads = [...unsectioned];
    for (const sectionId of catalog.threadSections.map((section) => section.id)) {
      threads.push(...await projectPages((method, params) => this.nativeCall(method, params),
        "thread/list", { sectionId, sortKey: "section_position", sortDirection: "asc" }));
    }
    const byId = new Map(threads.map((thread) => [thread.id, thread]));
    const sessionIds = new Map(this.state.sessions.map((s) => [s.thread_id, s.id]));
    const unsectionedIds = new Set(unsectioned.map((thread) => sessionIds.get(thread.id)).filter(Boolean));
    this.projectCatalogReady = true;
    this.patch({ projectLayout: { ...this.state.projectLayout, projects: catalog.projects, sections: catalog.sections,
        threadOrder: [...new Set([
          ...this.state.projectLayout.threadOrder.filter((id) => unsectionedIds.has(id)),
          ...threads.flatMap((t) => sessionIds.has(t.id) ? [sessionIds.get(t.id)!] : []),
        ])] },
      threadSections: catalog.threadSections,
      sessions: this.state.sessions.map((s) => byId.has(s.thread_id) ? { ...s, section_id: byId.get(s.thread_id)?.section?.id,
        settings: { ...s.settings, projectId: byId.get(s.thread_id)?.projectId || "" } } : s) });
  }
  async orderThread(id: string, beforeId?: string) {
    const session = this.state.sessions.find((s) => s.id === id);
    const before = this.state.sessions.find((s) => s.id === beforeId);
    if (!this.nativeProjectMode || !session?.thread_id) return false;
    const sectionId = before?.section_id || session.section_id;
    if (!sectionId) return false; // Unsectioned manual order is a Web display preference.
    await this.nativeCall("thread/section/move", { threadId: session.thread_id, sectionId, beforeThreadId: before?.thread_id || null });
    await this.refreshProjects(); return true;
  }
  async moveToThreadSection(id: string, sectionId: string | null, beforeId?: string) {
    const session = this.state.sessions.find((s) => s.id === id);
    const before = this.state.sessions.find((s) => s.id === beforeId);
    if (!session?.thread_id) return;
    if (session.settings?.projectId) await this.nativeCall("thread/metadata/update", { threadId: session.thread_id, projectId: "" });
    await this.nativeCall("thread/section/move", { threadId: session.thread_id, sectionId, beforeThreadId: before?.thread_id || null });
    await this.refreshProjects();
  }
  removeProject(id: string) {
    const project = this.projects.find((p) => p.id === id);
    if (!project) return;
    const threadProjects = Object.fromEntries(
      this.state.sessions
        .filter((s) => this.projectForSession(s.id)?.id === id)
        .map((s) => [s.id, ""]),
    );
    const saved = this.saveProjectLayout({
      projects: this.projects.filter((p) => p.id !== id),
      hiddenPaths: [
        ...new Set([...this.state.projectLayout.hiddenPaths, project.path]),
      ],
      threadProjects,
    });
    if (!this.nativeProjectMode && this.state.draftProjectId === id)
      this.setDraftProject({ id: "", path: "" });
    return saved.then((accepted) => {
      if (accepted && this.state.draftProjectId === id) this.setDraftProject({ id: "", path: "" });
      return accepted;
    });
  }
  private setDraftProject(project: { id: string; path: string }) {
    localStorage.setItem("codex-agent.project", project.path);
    localStorage.setItem("codex-agent.project-id", project.id);
    this.patch({ draftCwd: project.path, draftProjectId: project.id });
  }
  async startProject(projectId: string) {
    if (this.state.creating || this.state.submitting || this.state.settingsBusy)
      return;
    const project = this.projects.find((p) => p.id === projectId);
    if (!project) return;
    this.saveProjectLayout({});
    this.setDraftProject(project);
    await this.select(null);
  }
  async moveSessionToProject(id: string, projectId: string) {
    const project = this.projects.find((p) => p.id === projectId);
    const session = this.state.sessions.find((s) => s.id === id);
    if (
      (!project && projectId !== "") ||
      !session ||
      this.state.settingsBusy ||
      this.state.submitting ||
      this.state.creating
    )
      return false;
    if (
      project && (session.cwd || session.settings?.cwd) !== project.path &&
      !(await this.updateSessionSettings(id, { cwd: project.path }))
    )
      return false;
    if (this.nativeProjectMode) {
      try { await this.nativeCall("thread/metadata/update", { threadId: session.thread_id, projectId }); await this.refreshProjects(); }
      catch (error) { this.fail(error); return false; }
    } else this.saveProjectLayout({ threadProjects: { [id]: projectId } });
    return true;
  }
  async updateSessionSettings(id: string, settings: Record<string, unknown>) {
    const session = this.state.sessions.find((s) => s.id === id);
    if (this.state.settingsBusy || this.state.submitting || this.state.creating)
      return false;
    if (
      !session?.thread_id ||
      session.active_turn_id ||
      busyStatuses.has(session.status)
    ) {
      this.fail(new Error("请在当前回复结束后更改会话设置"));
      return false;
    }
    this.patch({ settingsBusy: true, error: "" });
    try {
      await this.nativeCall("thread/settings/update", {
        threadId: session.thread_id,
        ...settings,
      });
      this.patch({
        sessions: this.state.sessions.map((s) =>
          s.id === id
            ? {
                ...s,
                ...(typeof settings.cwd === "string"
                  ? { cwd: settings.cwd }
                  : {}),
                settings: mergeSessionSettings(s.settings, settings),
              }
            : s,
        ),
      });
      return true;
    } catch (error) {
      this.fail(error);
      return false;
    } finally {
      this.patch({ settingsBusy: false });
    }
  }
  async chooseProject(projectId: string) {
    if (this.state.settingsBusy || this.state.submitting || this.state.creating)
      return false;
    const project = this.projects.find((p) => p.id === projectId);
    if (!project) return false;
    const id = this.state.active;
    if (id && !(await this.moveSessionToProject(id, project.id))) return false;
    this.saveProjectLayout({});
    this.setDraftProject(project);
    return true;
  }
  get permissionConfiguration(): Record<string, any> {
    const settings = this.state.sessions.find(
      (s) => s.id === this.state.active,
    )?.settings;
    const defaults = this.state.runtimeDefaults;
    if (!this.state.active && this.state.draftPermission)
      return permissionSettings(this.state.draftPermission);
    return {
      sandbox: defaults.sandbox_mode || "workspace-write",
      approvalPolicy: defaults.approval_policy || "on-request",
      approvalsReviewer: defaults.approvals_reviewer || "user",
      ...(defaults.permissions ? { permissions: defaults.permissions } : {}),
      ...settings,
    };
  }
  async choosePermission(preset: PermissionPreset) {
    if (this.state.settingsBusy || this.state.submitting || this.state.creating)
      return false;
    if (this.state.active)
      return this.updateSessionSettings(this.state.active, {
        ...permissionSettings(preset),
        permissions: null,
      });
    localStorage.setItem("codex-agent.draft-permission", preset);
    this.patch({ draftPermission: preset, error: "" });
    return true;
  }
  get composerSettings() {
    const session = this.state.sessions.find(
      (s) => s.id === this.state.active,
    );
    const settings = session?.settings;
    const live = session?.active_turn_id ? session.turn_settings : undefined;
    const fallback = this.state.active ? this.defaults : this.state.preferences;
    return {
      model:
        live?.model ||
        settings?.collaborationMode?.settings?.model ||
        settings?.model ||
        fallback.model,
      effort:
        live?.effort ||
        settings?.collaborationMode?.settings?.reasoning_effort ||
        settings?.effort ||
        settings?.config?.model_reasoning_effort ||
        fallback.effort,
      mode: settings?.collaborationMode?.mode || fallback.mode,
    };
  }
  async updateComposerSettings(update: Partial<ConsoleState["preferences"]>) {
    if (this.state.settingsBusy) return false;
    const next = { ...this.composerSettings, ...update };
    const session = this.state.sessions.find((s) => s.id === this.state.active);
    if (!session) {
      for (const key of ["model", "effort"]) if (key in update) this.draftModelOverrides.add(key);
      this.patch({ preferences: next, ...(update.mode === "plan" ? { goalInputs: { ...this.state.goalInputs, new: false } } : {}) });
      return true;
    }
    if (session.active_turn_id) {
      if (update.mode !== undefined) {
        this.fail(new Error("工作模式将在当前回复结束后才能切换"));
        return false;
      }
      return this.updateTurnSettings(session.id, {
        ...(update.model !== undefined ? { model: update.model } : {}),
        ...(update.effort !== undefined ? { effort: update.effort } : {}),
      });
    }
    this.patch({ settingsBusy: true });
    try {
      if (update.mode === "plan" && update.effort === undefined && this.state.nativeProtocol?.methods.includes("config/read")) {
        const config = await this.nativeCall("config/read", { includeLayers: false, cwd: session.cwd || this.cwd }) as any;
        if (config.config?.plan_mode_reasoning_effort) next.effort = config.config.plan_mode_reasoning_effort;
      }
      const settings = this.turnOptions(next);
      await this.nativeCall("thread/settings/update", {
        threadId: session.thread_id,
        ...settings,
      });
      this.patch({
        ...(update.mode === "plan" ? { goalInputs: { ...this.state.goalInputs, [session.id]: false } } : {}),
        sessions: this.state.sessions.map((s) =>
          s.id === session.id
            ? { ...s, settings: { ...s.settings, ...settings } }
            : s,
        ),
      });
      return true;
    } catch (error) {
      this.fail(error);
      return false;
    } finally {
      this.patch({ settingsBusy: false });
    }
  }
  get goalInputMode() {
    return !!this.state.goalInputs[this.state.active || "new"];
  }
  async chooseGoalMode(enabled: boolean) {
    if (this.state.settingsBusy || this.state.submitting || this.state.creating) return false;
    const id = this.state.active;
    if (enabled && this.composerSettings.mode === "plan" && !await this.updateComposerSettings({ mode: "default" })) return false;
    if (this.state.active !== id) return false;
    this.patch({ goalInputs: { ...this.state.goalInputs, [id || "new"]: enabled }, error: "" });
    return true;
  }
  async sendGoal(objective: string, accepted?: (sessionId: string) => void) {
    if (!this.goalInputMode || !objective.trim() || this.state.submitting || this.state.creating || this.state.settingsBusy) return false;
    const active = this.state.active;
    const owner = active || "new";
    let submission = this.goalSubmissions.get(owner);
    if (!submission || submission.objective !== objective.trim()) {
      submission = { id: submission?.id || active || undefined, objective: objective.trim(), key: requestKey() };
      this.goalSubmissions.set(owner, submission);
    }
    this.patch({ submitting: true, error: "" });
    try {
      submission.id ||= (await this.create(false)) || undefined;
      const session = this.state.sessions.find((s) => s.id === submission.id);
      if (!session?.thread_id) throw new Error(this.state.error || "无法创建聊天");
      await api("/api/native/call", { method: "thread/goal/set", params: {
        threadId: session.thread_id, objective: submission.objective,
      } }, submission.key);
      this.goalSubmissions.delete(owner);
      this.patch({ goalInputs: { ...this.state.goalInputs, [owner]: false }, goalRevision: this.state.goalRevision + 1 });
      if (!active && this.state.active === active) await this.select(session.id);
      this.schedule();
      await this.refreshSessions().catch(this.fail);
      accepted?.(session.id);
      return true;
    } catch (error) {
      // Definite rejections may be retried after correcting the runtime state.
      // Network/503 failures retain the same key because the goal may exist.
      if (error instanceof ApiError && [400, 409, 422].includes(error.status)) submission.key = requestKey();
      this.fail(error); return false;
    } finally { this.patch({ submitting: false }); }
  }
  async updateTurnSettings(id: string, settings: Record<string, unknown>) {
    const session = this.state.sessions.find((s) => s.id === id);
    if (!session?.thread_id || !session.active_turn_id || this.state.settingsBusy) return false;
    const turnId = session.active_turn_id;
    this.patch({ settingsBusy: true, error: "" });
    try {
      await this.nativeCall("turn/settings/update", { threadId: session.thread_id, turnId, ...settings });
      this.patch({ sessions: this.state.sessions.map((s) => s.id === id && s.active_turn_id === turnId
        ? { ...s, turn_settings: { ...s.turn_settings, ...settings } } : s) });
      return true;
    } catch (error) { this.fail(error); return false; }
    finally { this.patch({ settingsBusy: false }); }
  }
  private turnOptions(
    settings = this.composerSettings,
  ): Record<string, unknown> {
    if (!settings.model) return {};
    return {
      model: settings.model,
      effort: settings.effort,
      collaborationMode: {
        mode: settings.mode,
        settings: {
          model: settings.model,
          reasoning_effort: settings.effort,
          developer_instructions: null,
        },
      },
    };
  }
  private remember(snapshot: Snapshot, id: string) {
    const first = snapshot.messages?.find(
      (m) => m.role === "user" && m.text.trim(),
    );
    const session = this.state.sessions.find((s) => s.id === id);
    if (
      first &&
      !session?.name &&
      BigInt(first.first_seq) <=
        (this.titleSequences.get(id) ?? BigInt(first.first_seq))
    ) {
      this.titleSequences.set(id, BigInt(first.first_seq));
      const titles = {
        ...this.state.titles,
        [id]: first.text.trim().slice(0, 48),
      };
      this.patch({ titles });
      localStorage.setItem("codex-agent.titles", JSON.stringify(titles));
    }
  }
  title(id: string) {
    return (
      this.state.sessions.find((s) => s.id === id)?.name ||
      this.state.titles[id] ||
      (id.startsWith("session-") ? "新会话 · " + id.slice(-6) : id)
    );
  }
  async rename(id: string, title: string) {
    if (!title.trim() || !this.state.sessions.some((s) => s.id === id)) return;
    const titles = { ...this.state.titles, [id]: title.trim().slice(0, 120) };
    localStorage.setItem("codex-agent.titles", JSON.stringify(titles));
    this.patch({ titles });
    const threadId = this.state.sessions.find((s) => s.id === id)?.thread_id;
    if (threadId) {
      try {
        await this.nativeCall("thread/name/set", {
          threadId,
          name: title.trim(),
        });
        await this.refreshSessions();
      } catch (error) {
        this.fail(error);
      }
    }
  }
  async refreshSessions() {
    const sessions = await api<Session[]>("/api/sessions");
    this.patch({ sessions, sessionsLoading: false });
  }
  async select(id: string | null, global = false, { refreshMetadata = true, clearError = true } = {}) {
    this.controller?.abort();
    clearTimeout(this.timer);
    this.timer = undefined;
    this.refreshQueued = false;
    const version = ++this.generation;
    const controller = new AbortController();
    this.controller = controller;
    this.historyPage = null;
    this.historyDepth = 0;
    this.paging = false;
    this.filterVersion++;
    this.filter = { method: "", direction: "", kind: "" };
    this.patch({
      active: id,
      global,
      snapshot: null,
      events: [],
      eventPage: null,
      loading: !!id || global,
      sessionRefreshing: !!id && refreshMetadata,
      error: clearError ? "" : this.state.error,
      streamError: "",
      history: false,
      older: false,
      eventLive: true,
    });
    if (id) localStorage.setItem("codex-agent.active", id);
    else localStorage.removeItem("codex-agent.active");
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      if (id) url.searchParams.set("session", id);
      else url.searchParams.delete("session");
      window.history.replaceState(window.history.state, "", url);
    }
    if (!id && !global) return;
    const report = (error: unknown) => { if (version === this.generation) this.fail(error); };
    if (refreshMetadata) {
      void api<Health>("/api/console/status", undefined, undefined, controller.signal).then((health) => {
        if (version !== this.generation) return;
        this.patch({ health, reset: this.state.reset ||
          (!!this.state.health && this.state.health.epoch !== health.epoch) });
      }).catch(report);
      void api<Session[]>("/api/sessions", undefined, undefined, controller.signal).then((sessions) => {
        if (version === this.generation) this.patch({ sessions, sessionRefreshing: false });
      }).catch(report);
    }
    try {
      let cursor: string;
      if (global) {
        const page = await api<EventPage>(
          "/api/events/history?limit=200&unbound=true",
          undefined,
          undefined,
          controller.signal,
        );
        if (version !== this.generation) return;
        this.patch({ events: page.events, eventPage: page });
        cursor = page.watermark;
      } else {
        const data = await api<Snapshot>(
          `/api/sessions/${encodeURIComponent(id!)}/snapshot`,
          undefined,
          undefined,
          controller.signal,
        );
        if (version !== this.generation) return;
        this.patch({ snapshot: data, older: !!data.next_page });
        this.remember(data, id!);
        cursor = data.watermark;
      }
      this.patch({ loading: false });
      void this.stream(id, global, cursor, version, controller);
    } catch (error) {
      if (version === this.generation) {
        this.patch({ loading: false });
        this.fail(error);
      }
    }
  }
  async create(activate = true) {
    if (this.state.creating) return null;
    this.patch({ creating: true });
    const key = this.actions.get("create") || requestKey();
    this.actions.set("create", key);
    try {
      if (this.state.nativeProtocol?.methods.includes("config/read")) await this.refreshRuntimeDefaults(this.currentProject?.path || this.state.draftCwd || this.state.nativeProtocol.cwd);
      const { model, effort, mode } = this.state.preferences;
      // Only explicit draft changes override project/native defaults.
      const modelOverride = model && (this.draftModelOverrides.has("model") || model !== this.defaults.model);
      const effortOverride = this.draftModelOverrides.has("effort") || effort !== this.defaults.effort;
      const project = this.currentProject;
      const options = {
        ...(this.nativeProjectMode && project ? { projectId: project.id } : {}),
        ...(project?.path || this.state.draftCwd ? { cwd: project?.path || this.state.draftCwd } : {}),
        ...(this.state.draftPermission
          ? permissionSettings(this.state.draftPermission, true)
          : {}),
        ...(modelOverride ? { model } : {}),
        ...(effortOverride ? { config: { model_reasoning_effort: effort } } : {}),
      };
      const session = await api<Session>("/api/sessions", { options }, key);
      if (project)
        this.saveProjectLayout({
          threadProjects: { [session.id]: project.id },
        });
      await this.refreshSessions();
      if (activate) {
        await this.select(session.id);
        if (mode === "plan" && !(await this.updateComposerSettings({ mode,
          effort: effortOverride ? effort : this.state.runtimeDefaults.plan_mode_reasoning_effort || this.composerSettings.effort,
        }))) return null;
      } else if (mode === "plan") {
        const inherited = session.settings || {};
        await this.nativeCall("thread/settings/update", { threadId: session.thread_id, ...this.turnOptions({
          model: inherited.model || model, effort: effortOverride ? effort : this.state.runtimeDefaults.plan_mode_reasoning_effort || inherited.effort || effort, mode,
        }) });
      }
      this.actions.delete("create");
      return session.id;
    } catch (error) {
      this.fail(error);
      return null;
    } finally {
      this.patch({ creating: false });
    }
  }
  private async sendTo(
    id: string,
    text: string,
    key: string,
    input?: Record<string, unknown>[],
    options?: Record<string, unknown>,
  ) {
    try {
      await api(
        `/api/sessions/${encodeURIComponent(id)}/messages`,
        {
          text,
          request_id: key,
          ...(options && Object.keys(options).length ? { options } : {}),
          ...(input?.length
            ? {
                input_items: [
                  { type: "text", text, text_elements: [] },
                  ...input,
                ],
              }
            : {}),
        },
        key,
      );
      this.pending = undefined;
      if (this.state.active === id) this.patch({ error: "" });
    } catch (error) {
      this.fail(error);
    } finally {
      this.patch({ submitting: false });
      this.schedule();
      await this.refreshSessions().catch(this.fail);
    }
  }
  async send(text: string, input?: Record<string, unknown>[]) {
    if (
      (!text.trim() && !input?.length) ||
      this.state.submitting ||
      this.state.creating ||
      this.state.settingsBusy
    )
      return;
    this.patch({ error: "" });
    const command = text.trim().match(/^\/(\w+)(?:\s+([\s\S]*))?$/);
    if (command && !input?.length) {
      const name = command[1].toLowerCase();
      const definition = exactSlashCommand("/" + name);
      if (definition?.unavailable || definition?.thread && !this.state.sessions.find((s) => s.id === this.state.active)?.thread_id) {
        this.fail(new Error(definition?.unavailable || "请先开始聊天")); return;
      }
      const session = this.state.sessions.find((s) => s.id === this.state.active);
      if (definition?.idle && (session?.active_turn_id || busyStatuses.has(session?.status || ""))) {
        this.fail(new Error("请先停止当前回复")); return;
      }
      if (name === "init") { await this.send(initPrompt); return; }
      if (name === "new" || name === "clear") {
        await this.select(null);
        return;
      }
      if (name === "plan") {
        await this.updateComposerSettings({
          mode: this.composerSettings.mode === "plan" ? "default" : "plan",
        });
        return;
      }
      if (
        [
          "model",
          "reasoning",
          "usage",
          "feedback",
          "permissions",
          "skills",
          "mcp",
          "apps",
          "plugins",
          "status",
          "resume",
          "help",
          "goal",
          "queue",
          "search",
          "settings", "resources", "memory", "hooks", "advanced", "loop", "native", "voice", "agents", "approvals", "background",
        ].includes(name)
      ) {
        window.dispatchEvent(new CustomEvent("codex-panel", { detail: ["settings", "resources", "memory", "hooks", "advanced", "loop", "native", "voice", "skills", "mcp", "apps", "plugins", "feedback"].includes(name) ? `session:${name}` : name }));
        return;
      }
      if (["compact", "fork", "review", "undo", "archive"].includes(name)) {
        window.dispatchEvent(new CustomEvent("codex-action", { detail: name }));
        return;
      }
    }
    let id = this.state.active;
    if (!id) id = await this.create();
    if (!id) return;
    const active = this.state.sessions.find((s) => s.id === id);
    if (active && active.status !== "ready") {
      this.fail(new Error("当前会话尚未就绪，请先停止或核对并恢复。"));
      return;
    }
    if (
      !this.pending ||
      this.pending.id !== id ||
      this.pending.text !== text ||
      JSON.stringify(this.pending.input) !== JSON.stringify(input)
    )
      this.pending = {
        id,
        text,
        input,
        options: this.turnOptions(),
        key: requestKey(),
      };
    this.patch({ submitting: true, error: "" });
    const titles = {
      ...this.state.titles,
      [id]: this.state.titles[id] || text.trim().slice(0, 48),
    };
    this.patch({ titles });
    localStorage.setItem("codex-agent.titles", JSON.stringify(titles));
    await this.sendTo(id, text, this.pending.key, input, this.pending.options);
  }
  retrySend = async () => {
    if (this.pending && !this.state.submitting) {
      this.patch({ submitting: true });
      await this.sendTo(
        this.pending.id,
        this.pending.text,
        this.pending.key,
        this.pending.input,
        this.pending.options,
      );
    }
  };
  get canRetry() {
    return !!this.pending && this.pending.id === this.state.active;
  }
  async action(action: "interrupt" | "resume" | "close") {
    const id = this.state.active;
    if (!id) return;
    const name = `${id}:${action}`,
      key = this.actions.get(name) || requestKey();
    this.actions.set(name, key);
    try {
      await api(`/api/sessions/${encodeURIComponent(id)}/${action}`, {}, key);
      this.actions.delete(name);
      this.patch({ error: "" });
    } catch (error) {
      this.fail(error);
    }
    await this.refreshSessions().catch(this.fail);
    this.schedule();
  }
  schedule() {
    this.refreshQueued = true;
    if (
      this.timer ||
      this.refreshing ||
      !this.state.active ||
      this.state.global
    )
      return;
    const version = this.generation,
      id = this.state.active;
    this.timer = setTimeout(async () => {
      this.timer = undefined;
      this.refreshing = true;
      this.refreshQueued = false;
      try {
        const [snapshot, sessions] = await Promise.all([
          api<Snapshot>(`/api/sessions/${encodeURIComponent(id)}/snapshot`),
          api<Session[]>("/api/sessions"),
        ]);
        if (version !== this.generation) return;
        let current = snapshot;
        // Rebuild every loaded page at the NEW watermark as a single snapshot.
        // This keeps live streaming and composing while browsing older items,
        // without mixing old tool results with new messages or resurrecting a rollback.
        for (
          let page = 0;
          page < this.historyDepth && current.next_page;
          page++
        ) {
          const older = await api<Snapshot>(
            `/api/sessions/${encodeURIComponent(id)}/snapshot?limit=200&view=all&page=${encodeURIComponent(current.next_page)}`,
          );
          current = mergeSnapshots(older, current);
        }
        if (version !== this.generation) return;
        if (!this.paging) {
          this.historyPage = current.next_page;
          this.patch({
            sessions,
            snapshot: current,
            older: !!current.next_page,
          });
        } else this.patch({ sessions });
        this.remember(current, id);
      } catch (error) {
        if (version === this.generation) this.fail(error);
      } finally {
        this.refreshing = false;
        if (this.refreshQueued) this.schedule();
      }
    }, 75);
  }
  private async stream(
    id: string | null,
    global: boolean,
    cursor: string,
    version: number,
    controller: AbortController,
  ) {
    let delay = 300;
    while (!controller.signal.aborted && version === this.generation) {
      try {
        const path = global
          ? "/api/events/stream"
          : `/api/sessions/${encodeURIComponent(id!)}/events`;
        const response = await fetch(
          `${path}?after=${encodeURIComponent(cursor)}`,
          { signal: controller.signal },
        );
        if (!response.ok)
          throw new ApiError((await response.json()).error, response.status);
        this.patch({ streamError: "" });
        await ConsoleTransport.consumeSSE(response.body!, (frame) => {
          if (controller.signal.aborted || version !== this.generation) return;
          const data = JSON.parse(frame.data);
          if (frame.event === "snapshot_required")
            throw new ApiError("SNAPSHOT_REQUIRED", 409);
          if (
            frame.event === "observation" &&
            ConsoleTransport.compareCursor(data.event_id, cursor) > 0
          ) {
            if (
              this.state.eventLive &&
              (!global || !data.session_id) &&
              Object.entries(this.filter).every(
                ([key, value]) => !value || data[key] === value,
              )
            ) {
              this.patch({ events: [...this.state.events, data].slice(-200) });
            }
            const notice = nativeNotice(data);
            if (notice && !this.state.nativeNotices.some((n) => n.id === notice.id)) this.patch({ nativeNotices: [...this.state.nativeNotices, notice].slice(-30) });
            if (invalidatesNativeResources(data)) {
              if (typeof window !== "undefined") window.dispatchEvent(new Event("codex-native-resources-changed"));
              if (["project/changed", "thread/project/updated"].includes(data.method)) void this.refreshProjects().catch(this.fail);
              if (["account/updated", "account/login/completed"].includes(data.method)) void this.refreshRuntimeDefaults().catch(this.fail);
            }
            cursor = data.event_id;
            if (data.method === "thread/queue/changed")
              this.patch({ queueRevision: this.state.queueRevision + 1 });
            this.schedule();
          } else if (
            frame.event === "cursor" &&
            ConsoleTransport.compareCursor(data.cursor, cursor) >= 0
          )
            cursor = data.cursor;
          else if (frame.event === "status" && this.state.health)
            this.patch({ health: { ...this.state.health, observation: data } });
        });
        delay = 300;
      } catch (error) {
        if (controller.signal.aborted || version !== this.generation) return;
        if (
          error instanceof ApiError &&
          ["EPOCH_MISMATCH", "CURSOR_AHEAD", "SNAPSHOT_REQUIRED"].includes(
            error.code,
          )
        ) {
          if (error.code === "EPOCH_MISMATCH") this.patch({ reset: true });
          await this.select(id, global);
          return;
        }
        this.patch({ streamError: "连接暂时中断，正在重新连接…" });
      }
      await new Promise<void>((resolve) => {
        const done = () => {
          clearTimeout(timer);
          controller.signal.removeEventListener("abort", done);
          resolve();
        };
        const timer = setTimeout(done, delay);
        controller.signal.addEventListener("abort", done, { once: true });
      });
      delay = Math.min(delay * 2, 5000);
    }
  }
  async older() {
    const id = this.state.active,
      version = this.generation,
      snapshot = this.state.snapshot;
    if (!id || !snapshot || this.paging) return;
    this.paging = true;
    try {
      const page = this.state.history ? this.historyPage : snapshot.next_page;
      const path = `/api/sessions/${encodeURIComponent(id)}/snapshot?limit=200&view=all`;
      if (!page) {
        if (version === this.generation) this.patch({ older: false });
        return;
      }
      const data = await api<Snapshot>(
        path + "&page=" + encodeURIComponent(page),
      );
      if (version !== this.generation) return;
      this.historyPage = data.next_page;
      this.historyDepth++;
      // All pages share the server cursor's fixed watermark. Retain every
      // item type and already-visible page; never splice in a live projection.
      this.patch({
        snapshot: mergeSnapshots(data, snapshot),
        history: true,
        older: !!data.next_page,
      });
      this.remember(this.state.snapshot!, id);
    } catch (error) {
      if (version === this.generation) this.fail(error);
    } finally {
      if (version === this.generation) {
        this.paging = false;
        this.schedule();
      }
    }
  }
  async latest() {
    if (this.state.active) await this.select(this.state.active);
  }
  async locateItem(itemId: string) {
    const id = this.state.active;
    const found = () => this.state.snapshot?.messages?.find((m) => m.item_id === itemId);
    while (!found() && this.state.older) {
      const previous = this.state.snapshot;
      await this.older();
      if (this.state.active !== id) throw new Error("当前会话已改变，请重新选择搜索结果");
      if (this.state.snapshot === previous) break;
    }
    if (!found()) throw new Error("未在可用历史中找到该消息，可能已被回退或删除");
  }
  async history(filter = this.filter, next = false) {
    this.filter = filter;
    const version = this.generation,
      queryVersion = ++this.filterVersion;
    const q = new URLSearchParams({ limit: "200" });
    Object.entries(filter).forEach(([k, v]) => {
      if (v) q.set(k, v);
    });
    if (this.state.global) q.set("unbound", "true");
    if (next && this.state.eventPage) {
      q.set("after", this.state.eventPage.next_cursor);
      q.set("until", this.state.eventPage.watermark);
    }
    const path = this.state.global
      ? "/api/events/history"
      : `/api/sessions/${encodeURIComponent(this.state.active!)}/history`;
    this.patch({ eventLive: false });
    try {
      const page = await api<EventPage>(path + "?" + q);
      if (version === this.generation && queryVersion === this.filterVersion)
        this.patch({ events: page.events, eventPage: page });
    } catch (error) {
      if (version === this.generation && queryVersion === this.filterVersion)
        this.fail(error);
    }
  }
  live() {
    this.filterVersion++;
    this.filter = { method: "", direction: "", kind: "" };
    this.patch({ eventLive: true, events: [], eventPage: null });
  }
  dispose() {
    this.generation++;
    this.initController?.abort();
    this.controller?.abort();
    clearTimeout(this.timer);
  }
}
export function mergeSnapshots(older: Snapshot, current: Snapshot): Snapshot {
  if (older.watermark !== current.watermark || older.epoch !== current.epoch)
    throw new Error("会话记录已更新，请重新加载历史记录");
  const merge = <T extends { id: string }>(
    previous: T[] = [],
    latest: T[] = [],
  ) => [
    ...new Map([...previous, ...latest].map((row) => [row.id, row])).values(),
  ];
  return {
    ...older,
    messages: merge(older.messages, current.messages),
    steps: merge(older.steps, current.steps),
    turns: merge(older.turns, current.turns),
  };
}
export const consoleStore = new ConsoleStore();
