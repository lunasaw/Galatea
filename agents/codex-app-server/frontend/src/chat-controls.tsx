import {
  useEffect,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
} from "react";
import { useAui, useAuiState } from "@assistant-ui/react";
import {
  Check,
  ChevronDown,
  Folder,
  ListChecks,
  ShieldCheck,
  ArrowUp,
} from "lucide-react";
import { Button } from "./components/ui/button";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "./components/ui/dropdown-menu";
import { consoleStore as store } from "./store";
import { native } from "./native";
import { ProjectPicker } from "./project-controls";
import {
  identifyPermission,
  permissionPresets,
  type PermissionPreset,
} from "./permission-presets";
import { busyStatuses } from "./store";
import { Hand, Shield, ShieldAlert } from "lucide-react";

export const openSettings = (tab = "settings") =>
  window.dispatchEvent(new CustomEvent("codex-panel", { detail: tab }));
const effortNames: Record<string, string> = {
  none: "无",
  minimal: "最低",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "很高",
  max: "最高",
  ultra: "Ultra",
};

export function ComposerOptions() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const settings = store.composerSettings;
  const session = state.sessions.find((s) => s.id === state.active);
  const model = state.models.find((m) => m.model === settings.model);
  const efforts = model?.supportedReasoningEfforts?.map(
    (e) => e.reasoningEffort,
  ) || ["low", "medium", "high", "xhigh"];
  const disabled =
    state.settingsBusy || state.submitting || !!session?.active_turn_id;
  return (
    <div className="composer-options">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled}
            aria-label="选择模型"
            className="composer-option"
          >
            {settings.model || "默认模型"}
            <ChevronDown />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-56">
          <DropdownMenuLabel>模型</DropdownMenuLabel>
          {[...new Set([settings.model, ...state.models.map((m) => m.model)])]
            .filter(Boolean)
            .map((name) => (
              <DropdownMenuItem
                key={name}
                onSelect={() =>
                  void store.updateComposerSettings({ model: name })
                }
              >
                {name}
                {name === settings.model && <Check className="ml-auto" />}
              </DropdownMenuItem>
            ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => openSettings()}>
            模型设置…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled || !settings.model}
            aria-label="选择推理强度"
            className="composer-option"
          >
            {effortNames[settings.effort] || settings.effort}
            <ChevronDown />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuLabel>推理强度</DropdownMenuLabel>
          {efforts.map((e) => (
            <DropdownMenuItem
              key={e}
              onSelect={() => void store.updateComposerSettings({ effort: e })}
            >
              {effortNames[e] || e}
              {e === settings.effort && <Check className="ml-auto" />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled || !settings.model}
            aria-label="选择工作模式"
            className="composer-option"
          >
            <ListChecks />
            {settings.mode === "plan" ? "计划" : "执行"}
            <ChevronDown />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {[
            ["default", "执行", "直接完成任务"],
            ["plan", "计划", "先讨论并制定计划"],
          ].map(([mode, label, hint]) => (
            <DropdownMenuItem
              key={mode}
              onSelect={() => void store.updateComposerSettings({ mode })}
            >
              <div>
                {label}
                <p className="text-xs text-muted-foreground">{hint}</p>
              </div>
              {mode === settings.mode && <Check className="ml-auto" />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

export function WorkspaceContext() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const session = state.sessions.find((s) => s.id === state.active);
  const [permissionOpen, setPermissionOpen] = useState(false);
  const disabled =
    state.settingsBusy ||
    state.creating ||
    state.submitting ||
    !!session?.active_turn_id ||
    !!(session && busyStatuses.has(session.status));
  const selected = identifyPermission(store.permissionConfiguration);
  useEffect(() => {
    const open = () => setPermissionOpen(true);
    window.addEventListener("codex-permissions", open);
    return () => window.removeEventListener("codex-permissions", open);
  }, []);
  return (
    <div className="workspace-context">
      <ProjectPicker disabled={disabled} />
      <DropdownMenu open={permissionOpen} onOpenChange={setPermissionOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            aria-label="切换权限"
            className={selected === "full" ? "permission-full" : ""}
          >
            <ShieldCheck />
            {selected === "custom"
              ? "自定义权限"
              : selected === "full"
                ? "完全访问"
                : permissionPresets[selected].label}
            <ChevronDown />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side="top"
          align="start"
          className="permission-menu"
        >
          <DropdownMenuLabel>应如何批准 Codex 操作？</DropdownMenuLabel>
          {(
            Object.entries(permissionPresets) as [
              PermissionPreset,
              (typeof permissionPresets)[PermissionPreset],
            ][]
          ).map(([id, preset]) => {
            const Icon =
              id === "ask" ? Hand : id === "auto" ? Shield : ShieldAlert;
            const supportsAuto = JSON.stringify(
              state.nativeProtocol?.definitions?.ApprovalsReviewer || {},
            ).includes("auto_review");
            return (
              <DropdownMenuItem
                key={id}
                disabled={disabled || (id === "auto" && !supportsAuto)}
                className={id === "full" ? "permission-full" : ""}
                onSelect={() => void store.choosePermission(id)}
              >
                <Icon />
                <div>
                  <span>{preset.label}</span>
                  <small>
                    {id === "auto" && !supportsAuto
                      ? "当前运行时不支持自动审批"
                      : preset.description}
                  </small>
                </div>
                {selected === id && <Check className="ml-auto" />}
              </DropdownMenuItem>
            );
          })}
          {selected === "custom" && (
            <p className="permission-custom">当前会话使用自定义权限配置</p>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <span className="composer-shortcut">/ 命令</span>
    </div>
  );
}

const commands = [
  ["new", "新建会话"],
  ["model", "选择模型与推理强度"],
  ["plan", "切换计划模式"],
  ["permissions", "设置文件与命令权限"],
  ["skills", "浏览 Skills"],
  ["mcp", "管理 MCP 工具"],
  ["apps", "浏览 Apps"],
  ["plugins", "管理插件"],
  ["resume", "恢复历史会话"],
  ["compact", "压缩当前上下文"],
  ["fork", "分叉当前会话"],
  ["review", "审查未提交的修改"],
  ["undo", "回退上一轮上下文"],
  ["status", "查看会话状态"],
  ["help", "快捷命令与帮助"],
];

export function useComposerCommands() {
  const aui = useAui();
  const text = useAuiState((s) => s.composer.text);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const session = state.sessions.find((s) => s.id === state.active);
  const [selected, setSelected] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const [steering, setSteering] = useState(false);
  useEffect(() => {
    setSelected(0);
    setDismissed(false);
  }, [text]);
  const filtered =
    !dismissed && /^\/\w*$/.test(text)
      ? commands.filter(([name]) => name.startsWith(text.slice(1)))
      : [];
  const run = async (name: string) => {
    aui.composer().setText("");
    await store.send("/" + name);
  };
  const steer = async () => {
    if (!session?.active_turn_id || !text.trim() || steering) return;
    setSteering(true);
    try {
      await native("turn/steer", {
        threadId: session.thread_id,
        expectedTurnId: session.active_turn_id,
        input: [{ type: "text", text, text_elements: [] }],
      });
      if (
        store.state.active === session.id &&
        aui.composer().getState().text === text
      )
        aui.composer().setText("");
    } catch (error) {
      store.fail(error);
    } finally {
      setSteering(false);
    }
  };
  const onKeyDownCapture = (event: KeyboardEvent) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.target instanceof HTMLElement && event.target.id !== "text")
      return;
    if (
      filtered.length &&
      ["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(event.key) &&
      !event.shiftKey
    ) {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "Escape") setDismissed(true);
      else if (event.key === "Enter")
        void run(filtered[selected % filtered.length][0]);
      else
        setSelected(
          (selected + (event.key === "ArrowDown" ? 1 : filtered.length - 1)) %
            filtered.length,
        );
    } else if (
      session?.active_turn_id &&
      event.key === "Enter" &&
      !event.shiftKey
    ) {
      event.preventDefault();
      event.stopPropagation();
      void steer();
    }
  };
  return {
    onKeyDownCapture,
    placeholder: session?.active_turn_id
      ? "补充说明，或调整接下来的方向…"
      : "描述任务，输入 / 查看命令",
    menu: filtered.length ? (
      <div className="slash-menu" role="listbox" aria-label="快捷命令">
        {filtered.map(([name, label], index) => (
          <button
            type="button"
            role="option"
            aria-selected={index === selected % filtered.length}
            key={name}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => void run(name)}
          >
            <code>/{name}</code>
            <span>{label}</span>
          </button>
        ))}
      </div>
    ) : null,
    steerButton:
      session?.active_turn_id && text.trim() ? (
        <Button
          type="button"
          size="icon"
          className="rounded-full size-9"
          aria-label="发送补充"
          disabled={steering}
          onClick={() => void steer()}
        >
          <ArrowUp className="size-4" />
        </Button>
      ) : null,
  };
}
