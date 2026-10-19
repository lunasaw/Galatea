import { useEffect, useState } from "react";
import {
  ChevronDown,
  ShieldCheck,
  Terminal,
  Workflow,
  Wrench,
} from "lucide-react";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import {
  api,
  consoleStore as store,
  statusLabel,
  type ConsoleState,
} from "./store";
import type { NativeEvent, ToolDefinition } from "./types";
function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}
function EventRow({ row }: { row: NativeEvent }) {
  const [detail, setDetail] = useState<unknown>();
  const [error, setError] = useState("");
  return (
    <details
      className="event"
      onToggle={async (e) => {
        if (!e.currentTarget.open || detail !== undefined) return;
        try {
          setDetail(
            await api("/api/events/" + encodeURIComponent(row.event_id)),
          );
        } catch (error) {
          setError(String(error));
        }
      }}
    >
      <summary>
        <span className="event-seq">{row.seq}</span>
        <span>{row.method || row.kind}</span>
        {row.redactions.length > 0 && <span className="tag">已脱敏</span>}
        <ChevronDown size={13} />
      </summary>
      <div className="event-meta">
        {row.direction || "Host"} · {row.kind}
      </div>
      {error && <p role="alert">{error}</p>}
      {detail !== undefined && <Json value={detail} />}
    </details>
  );
}
export function Execution({ state }: { state: ConsoleState }) {
  return (
    <div id="loop">
      <p className="panel-description">
        查看本次会话实际发生的步骤与工具回执。
      </p>
      <div id="turns">
        {state.snapshot?.turns?.map((turn) => (
          <details className="turn" key={turn.id}>
            <summary>
              <span className="status-dot" />
              {statusLabel(turn.status || "unknown")}
              <ChevronDown size={14} />
            </summary>
            <Json value={turn} />
          </details>
        ))}
      </div>
      <div id="steps">
        {state.snapshot?.steps?.map((step) => (
          <details key={step.id} className="step">
            <summary>
              <Wrench size={15} />
              <strong>{step.tool || step.method || step.type}</strong>
              <span className="muted">{statusLabel(step.status || "")}</span>
            </summary>
            {step.type === "request" && <p>等待回应的请求会显示在页面顶部。</p>}
            {step.text && <p>{step.text}</p>}
            <Json value={step} />
          </details>
        ))}
      </div>
      {!state.snapshot?.steps?.length && (
        <div className="panel-empty">
          <Workflow />
          暂无执行步骤
        </div>
      )}
      <p className="panel-description">
        显示最近 200 条步骤；完整历史可在原生事件中分页查询。
      </p>
    </div>
  );
}
export function NativeEvents({ state }: { state: ConsoleState }) {
  const [filter, setFilter] = useState({ method: "", direction: "", kind: "" });
  useEffect(() => {
    if (state.active || state.global) void store.history();
  }, [state.active, state.global]);
  return (
    <div id="native">
      <p className="panel-description">
        {state.global
          ? "当前运行实例中尚未归属会话的事件。"
          : "当前会话的双向协议事件，正文已按规则脱敏。"}
      </p>
      <form
        id="event-filter"
        className="event-filters"
        onSubmit={(e) => {
          e.preventDefault();
          void store.history(filter);
        }}
      >
        <label>
          方法
          <Input
            id="method"
            value={filter.method}
            onChange={(e) => setFilter({ ...filter, method: e.target.value })}
            placeholder="精确匹配 method"
          />
        </label>
        <div className="filter-selects">
          <label>
            方向
            <select
              id="direction"
              value={filter.direction}
              onChange={(e) =>
                setFilter({ ...filter, direction: e.target.value })
              }
            >
              <option value="">全部</option>
              <option value="host_to_runtime">Host → Codex</option>
              <option value="runtime_to_host">Codex → Host</option>
            </select>
          </label>
          <label>
            类型
            <select
              id="kind"
              value={filter.kind}
              onChange={(e) => setFilter({ ...filter, kind: e.target.value })}
            >
              <option value="">全部</option>
              <option>request</option>
              <option>response</option>
              <option>notification</option>
            </select>
          </label>
        </div>
        <div className="panel-actions">
          <Button type="submit">查询历史</Button>
          <Button
            type="button"
            id="live-events"
            onClick={() => {
              setFilter({ method: "", direction: "", kind: "" });
              store.live();
            }}
          >
            查看实时
          </Button>
          <span className="muted">{state.eventLive ? "实时" : "历史"}</span>
        </div>
      </form>
      <div id="native-events">
        {state.events.map((row) => (
          <EventRow key={row.event_id} row={row} />
        ))}
      </div>
      {!state.events.length && (
        <div className="panel-empty">
          <Terminal />
          当前筛选没有事件
        </div>
      )}
      <Button
        id="next-events"
        className="panel-next"
        disabled={!state.eventPage?.has_more || state.eventLive}
        onClick={() => void store.history(filter, true)}
      >
        下一页事件
      </Button>
    </div>
  );
}

const flattenTools = (tools: ToolDefinition[]): ToolDefinition[] =>
  tools.flatMap((tool) => (tool.tools ? flattenTools(tool.tools) : [tool]));
export function Capabilities({ state }: { state: ConsoleState }) {
  const tools = flattenTools(state.capabilities?.tools || []);
  return (
    <>
      <div className="capability-banner">
        <ShieldCheck />
        <div>
          <strong>当前可用能力</strong>
          <p>来自运行中 Host 的真实配置</p>
        </div>
      </div>
      <div className="capability-stats">
        <div>
          <span>运行时</span>
          <strong>Codex</strong>
        </div>
        <div>
          <span>已开放工具</span>
          <strong>{tools.length}</strong>
        </div>
      </div>
      {tools.map((tool) => (
        <details className="capability-tool" key={tool.name}>
          <summary>
            <Wrench size={15} />
            <strong>{tool.name}</strong>
            <ChevronDown size={13} />
          </summary>
          <p>{tool.description}</p>
        </details>
      ))}
      <dl className="capability-flags">
        <div>
          <dt>Skill 集成</dt>
          <dd>
            {state.capabilities?.skill_integration ? "已接入" : "尚未接入"}
          </dd>
        </div>
        <div>
          <dt>页面交互请求</dt>
          <dd>
            {state.capabilities?.interactive_requests ? "已支持" : "尚未支持"}
          </dd>
        </div>
        <div>
          <dt>聊天与事件历史</dt>
          <dd>
            {state.capabilities?.storage.retention === "persistent"
              ? "消息与事件持久化"
              : "对话可恢复；事件保留于当前进程"}
          </dd>
        </div>
      </dl>
      <p className="panel-description">
        对话可从原生 Codex 记录恢复。所有访问者共用 Host 配置的执行身份。
      </p>
      <details className="diagnostics">
        <summary>运行状态与完整性</summary>
        <Json value={state.health} />
        <Json value={state.capabilities} />
      </details>
    </>
  );
}
