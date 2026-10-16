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
  loading: boolean;
  creating: boolean;
  submitting: boolean;
  error: string;
  streamError: string;
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
  }>;
  preferences: { model: string; effort: string; mode: string };
  settingsBusy: boolean;
  projectLayout: ProjectLayout;
  draftCwd: string;
  draftProjectId: string;
  draftPermission: PermissionPreset | null;
  runtimeDefaults: Record<string, any>;
};

export class ConsoleStore {
  private defaults = { model: "", effort: "medium", mode: "default" };
  private listeners = new Set<() => void>();
  private controller?: AbortController;
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
    loading: false,
    creating: false,
    submitting: false,
    error: "",
    streamError: "",
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
    projectLayout: emptyLayout(),
    draftCwd: "",
    draftProjectId: "",
    draftPermission: null,
    runtimeDefaults: {},
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
    try {
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
      const [health, sessions, capabilities, nativeProtocol, nativeRequests] =
        await Promise.all([
          api<Health>("/api/console/status"),
          api<Session[]>("/api/sessions"),
          api<Capabilities>("/api/capabilities"),
          api<NativeProtocol>("/api/native/protocol"),
          api<NativeRequest[]>("/api/native/requests"),
        ]);
      this.patch({
        health,
        sessions,
        capabilities,
        nativeProtocol,
        nativeRequests,
      });
      this.saveProjectLayout({});
      const previous = localStorage.getItem("codex-agent.active");
      if (previous && sessions.some((s) => s.id === previous))
        await this.select(previous);
      const [models, config] = await Promise.all([
        this.nativeCall("model/list", {}) as Promise<any>,
        this.nativeCall("config/read", {
          includeLayers: false,
        }) as Promise<any>,
      ]);
      this.defaults = {
        model: config.config?.model || "",
        effort: config.config?.model_reasoning_effort || "medium",
        mode: "default",
      };
      this.patch({
        models: models.data || [],
        preferences: this.defaults,
        runtimeDefaults: config.config || {},
      });
    } catch (error) {
      this.fail(error);
    }
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
  async nativeCall(method: string, params: unknown) {
    const key = requestKey();
    return api(`/api/native/call`, { method, params }, key);
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
    return mergeProjects(this.state.projectLayout, [
      this.state.nativeProtocol?.cwd || "",
      this.state.draftCwd,
      ...this.state.sessions
        .filter((s) => !s.archived)
        .map((s) => s.cwd || s.settings?.cwd || ""),
    ]);
  }
  projectForSession(id: string) {
    if (this.state.projectLayout.threadProjects[id] === "") return undefined;
    const session = this.state.sessions.find((s) => s.id === id);
    if (!session) return undefined;
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
  removeProject(id: string) {
    const project = this.projects.find((p) => p.id === id);
    if (!project) return;
    const threadProjects = Object.fromEntries(
      this.state.sessions
        .filter((s) => this.projectForSession(s.id)?.id === id)
        .map((s) => [s.id, ""]),
    );
    this.saveProjectLayout({
      projects: this.projects.filter((p) => p.id !== id),
      hiddenPaths: [
        ...new Set([...this.state.projectLayout.hiddenPaths, project.path]),
      ],
      threadProjects,
    });
    if (this.state.draftProjectId === id)
      this.setDraftProject({ id: "", path: "" });
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
      !project ||
      !session ||
      this.state.settingsBusy ||
      this.state.submitting ||
      this.state.creating
    )
      return false;
    if (
      (session.cwd || session.settings?.cwd) !== project.path &&
      !(await this.updateSessionSettings(id, { cwd: project.path }))
    )
      return false;
    this.saveProjectLayout({ threadProjects: { [id]: project.id } });
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
                settings: { ...s.settings, ...settings },
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
    const settings = this.state.sessions.find(
      (s) => s.id === this.state.active,
    )?.settings;
    const fallback = this.state.active ? this.defaults : this.state.preferences;
    return {
      model:
        settings?.collaborationMode?.settings?.model ||
        settings?.model ||
        fallback.model,
      effort:
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
      this.patch({ preferences: next });
      return true;
    }
    this.patch({ settingsBusy: true });
    try {
      const settings = this.turnOptions(next);
      await this.nativeCall("thread/settings/update", {
        threadId: session.thread_id,
        ...settings,
      });
      this.patch({
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
    this.patch({ sessions });
  }
  async select(id: string | null, global = false) {
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
      error: "",
      streamError: "",
      history: false,
      older: false,
      eventLive: true,
    });
    if (id) localStorage.setItem("codex-agent.active", id);
    else localStorage.removeItem("codex-agent.active");
    if (!id && !global) return;
    try {
      const [health, sessions] = await Promise.all([
        api<Health>(
          "/api/console/status",
          undefined,
          undefined,
          controller.signal,
        ),
        api<Session[]>(
          "/api/sessions",
          undefined,
          undefined,
          controller.signal,
        ),
      ]);
      if (version !== this.generation) return;
      this.patch({
        health,
        sessions,
        reset:
          this.state.reset ||
          (!!this.state.health && this.state.health.epoch !== health.epoch),
      });
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
  async create() {
    if (this.state.creating) return null;
    this.patch({ creating: true });
    const key = this.actions.get("create") || requestKey();
    this.actions.set("create", key);
    try {
      const { model, effort } = this.state.preferences;
      const project = this.currentProject;
      const options = {
        ...(this.state.draftCwd ? { cwd: this.state.draftCwd } : {}),
        ...(this.state.draftPermission
          ? permissionSettings(this.state.draftPermission, true)
          : {}),
        ...(model ? { model, config: { model_reasoning_effort: effort } } : {}),
      };
      const session = await api<Session>("/api/sessions", { options }, key);
      this.actions.delete("create");
      if (project)
        this.saveProjectLayout({
          threadProjects: { [session.id]: project.id },
        });
      await this.refreshSessions();
      await this.select(session.id);
      if (model && !(await this.updateComposerSettings(this.state.preferences)))
        return null;
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
    const command = text.trim().match(/^\/(\w+)(?:\s+([\s\S]*))?$/);
    if (command && !input?.length) {
      const name = command[1];
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
          "permissions",
          "skills",
          "mcp",
          "apps",
          "plugins",
          "status",
          "resume",
          "help",
        ].includes(name)
      ) {
        window.dispatchEvent(new CustomEvent("codex-panel", { detail: name }));
        return;
      }
      if (["compact", "fork", "review", "undo"].includes(name)) {
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
            cursor = data.event_id;
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
