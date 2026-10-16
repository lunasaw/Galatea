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
      const titles = localStorage.getItem("galatea.titles");
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
      const previous = localStorage.getItem("galatea.active");
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
    if (first && !this.state.titles[id]) {
      const titles = {
        ...this.state.titles,
        [id]: first.text.trim().slice(0, 48),
      };
      this.patch({ titles });
      localStorage.setItem("galatea.titles", JSON.stringify(titles));
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
    localStorage.setItem("galatea.titles", JSON.stringify(titles));
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
    if (id) localStorage.setItem("galatea.active", id);
    else localStorage.removeItem("galatea.active");
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
      const session = await api<Session>(
        "/api/sessions",
        model
          ? { options: { model, config: { model_reasoning_effort: effort } } }
          : {},
        key,
      );
      this.actions.delete("create");
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
    localStorage.setItem("galatea.titles", JSON.stringify(titles));
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
        this.patch({
          sessions,
          ...(!this.state.history
            ? { snapshot, older: !!snapshot.next_page }
            : {}),
        });
        this.remember(snapshot, id);
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
    if (!id || !snapshot) return;
    try {
      let page = this.historyPage;
      const path = `/api/sessions/${encodeURIComponent(id)}/messages?limit=200&view=messages`;
      if (!this.state.history) {
        const latest = await api<Snapshot>(
          path + "&until=" + encodeURIComponent(snapshot.watermark),
        );
        page = latest.next_page;
      }
      if (!page) {
        if (version === this.generation) this.patch({ older: false });
        return;
      }
      const data = await api<Snapshot>(
        path + "&page=" + encodeURIComponent(page),
      );
      if (version !== this.generation) return;
      this.historyPage = data.next_page;
      // Historical messages must never be mixed with live tool steps or turns.
      this.patch({
        snapshot: { ...data, steps: [], turns: [] },
        history: true,
        older: !!data.next_page,
      });
    } catch (error) {
      if (version === this.generation) this.fail(error);
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
export const consoleStore = new ConsoleStore();
