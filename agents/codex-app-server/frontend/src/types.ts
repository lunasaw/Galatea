export type Session = {
  id: string;
  status: string;
  thread_id?: string;
  active_turn_id?: string | null;
  cwd?: string;
  name?: string;
  archived?: boolean;
  settings?: Record<string, any>;
  turn_settings?: Record<string, any>;
  section_id?: string | null;
};
export type Message = {
  id: string;
  item_id?: string;
  client_id?: string | null;
  role: "user" | "assistant";
  text: string;
  turn_id?: string;
  first_seq: string;
  last_seq: string;
  replay_order?: number;
  content?: Array<{ type: string; url?: string; path?: string; name?: string }>;
  phase?: "commentary" | "final_answer" | null;
  delivery?: "async" | null;
  questions?: Array<{ title: string; options?: string[] | null }> | null;
  memoryCitation?: {
    entries: Array<{ path: string; lineStart: number; lineEnd: number; note: string }>;
    threadIds: string[];
  } | null;
  status?: string;
  sendStatus?: PendingMessage["status"];
};
export type PendingMessage = {
  id: string | null;
  key: string;
  text: string;
  input?: Record<string, unknown>[];
  options?: Record<string, unknown>;
  steer?: { threadId: string; expectedTurnId: string };
  status: "sending" | "sent" | "failed";
};
export type Step = {
  id: string;
  type: string;
  turn_id?: string;
  first_seq: string;
  last_seq: string;
  replay_order?: number;
  tool?: string;
  call_id?: string;
  arguments?: unknown;
  result?: unknown;
  success?: boolean;
  status?: string;
  text?: string;
  method?: string;
  detail?: unknown;
  observed_at?: string;
  started_at?: string;
  completed_at?: string;
  item_id?: string;
  [key: `summary_${number}`]: string | undefined;
};
export type Turn = {
  id: string;
  status?: string;
  usage?: unknown;
  started_at?: string;
  completed_at?: string;
  detail?: unknown;
};
export type Snapshot = {
  epoch: string;
  watermark: string;
  next_page: string | null;
  messages?: Message[];
  steps?: Step[];
  app_step?: Step | null;
  turns?: Turn[];
  coverage: { status: string; reason?: string };
};
export type NativeEvent = {
  event_id: string;
  seq: string;
  method?: string;
  direction?: string;
  kind: string;
  session_id?: string;
  redactions: unknown[];
};
export type EventPage = {
  events: NativeEvent[];
  watermark: string;
  next_cursor: string;
  has_more: boolean;
};
export type ToolDefinition = {
  name: string;
  description?: string;
  tools?: ToolDefinition[];
};
export type Capabilities = {
  skill_integration: boolean;
  interactive_requests: boolean;
  storage: { retention: string };
  tools: ToolDefinition[];
  native_protocol_version?: string;
  conversation_storage?: string;
  [key: string]: unknown;
};
export type NativeRequest = {
  id: string;
  method: string;
  params: Record<string, unknown>;
  session_id?: string | null;
  created_at: number;
};
export type NativeProtocol = {
  skill_roots?: string[];
  platform?: string;
  version: string;
  methods: string[];
  params: Record<string, unknown>;
  configuration_editable: boolean;
  cwd: string;
  definitions: Record<string, unknown>;
};
export type Health = {
  epoch: string;
  host_ready: boolean;
  observation: { status: string };
  [key: string]: unknown;
};
