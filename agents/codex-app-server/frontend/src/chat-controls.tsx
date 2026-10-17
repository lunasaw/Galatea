import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
} from "react";
import { useAui, useAuiState, type PendingAttachment } from "@assistant-ui/react";
import {
  Check,
  ChevronDown,
  Folder,
  ListChecks,
  ShieldCheck,
  ArrowUp,
  Plus,
  Target,
  Lightbulb,
  Paperclip,
  ChevronUp,
  RotateCcw,
  ChevronRight,
  X,
  ListPlus,
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
import { consoleStore as store, requestKey } from "./store";
import { TooltipIconButton } from "./components/assistant-ui/elements/tooltip-icon-button";
import { native } from "./native";
import { Popover, Slider } from "radix-ui";
import { ComposerPrimitive } from "@assistant-ui/react";
import { GoalCard, QueueCard } from "./thread-capabilities";
import { VoiceConversationCard } from "./voice-controls";
import { ComposerReferences } from "./composer-references";
import { useSkillMentions } from "./composer-skill-mentions";
import { useSlashCommands } from "./composer-commands";
import { effortNames } from "./slash-commands";
import { attachments, nativeParts } from "./composer-input";
import { sharedMutation } from "./native-mutation";
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

export function ComposerAddMenu() {
  const aui = useAui();
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const session = state.sessions.find((s) => s.id === state.active);
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const upload = useRef<HTMLButtonElement>(null);
  const plan = store.composerSettings.mode === "plan";
  const goal = store.goalInputMode;
  const chooseGoal = () => {
    if (!goal && aui.composer().getState().attachments.length) {
      store.fail(new Error("目标模式只接受文字，请先移除附件")); return;
    }
    void store.chooseGoalMode(!goal);
  };
  useEffect(() => {
    const show = (event: Event) => { if ((event as CustomEvent).detail === "goal") chooseGoal(); };
    window.addEventListener("codex-composer-panel", show);
    return () => window.removeEventListener("codex-composer-panel", show);
  }, [goal, aui]);
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);
  useEffect(() => setOpen(false), [state.active]);
  const select = (action: () => void) => { setOpen(false); action(); trigger.current?.focus(); };
  return <>
    {open && <div ref={root} className="composer-add-menu" role="menu" aria-label="添加到聊天"
      onKeyDown={(event) => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus(); }
        if (event.target instanceof HTMLInputElement) return;
        if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
        const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button[role="menuitem"]:not(:disabled)')];
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        event.preventDefault();
        items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
      }}>
      <p>添加</p>
      <button type="button" role="menuitem" disabled={goal} title={goal ? "目标模式只接受文字" : undefined} onClick={() => select(() => upload.current?.click())}><Paperclip /><span>文件和文件夹</span><small>{goal ? "目标模式只接受文字" : "添加本地文件"}</small></button>
      <button type="button" role="menuitem" disabled={state.settingsBusy || state.submitting || state.creating} onClick={() => select(chooseGoal)}><Target /><span>目标</span><small>{goal ? "退出目标输入" : "设置要持续追求的目标"}</small>{goal && <Check className="ml-auto" />}</button>
      <button type="button" role="menuitem" disabled={state.settingsBusy || !!session?.active_turn_id || !store.composerSettings.model}
        onClick={() => select(() => { void store.updateComposerSettings({ mode: plan ? "default" : "plan" }); })}><Lightbulb /><span>计划模式</span><small>{plan ? "关闭计划模式" : "开启计划模式"}</small>{plan && <Check className="ml-auto" />}</button>
      <ComposerReferences threadId={session?.thread_id} disabled={goal || state.submitting || state.creating} selected={() => { setOpen(false); trigger.current?.focus(); }} />
    </div>}
    <button ref={trigger} type="button" className="composer-add-trigger" aria-label="添加到聊天" aria-haspopup="menu" aria-expanded={open}
      onClick={() => setOpen(!open)} onKeyDown={(event) => { if (event.key === "ArrowUp" || event.key === "ArrowDown") { event.preventDefault(); setOpen(true); requestAnimationFrame(() => root.current?.querySelector<HTMLButtonElement>('button[role="menuitem"]')?.focus()); } }}><Plus size={18} /></button>
    <ComposerPrimitive.AddAttachment asChild><button ref={upload} type="button" disabled={goal} hidden tabIndex={-1} aria-label="添加图片或文本文件" /></ComposerPrimitive.AddAttachment>
  </>;
}

export function ComposerConversationCards() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const threadId = state.sessions.find((s) => s.id === state.active)?.thread_id;
  const [goalOpen, setGoalOpen] = useState(false);
  useEffect(() => { setGoalOpen(false); }, [state.active]);
  return <div className="composer-conversation-cards">
    <GoalCard key={`goal-${threadId}`} threadId={threadId} revision={state.goalRevision} expanded={goalOpen} onExpandedChange={setGoalOpen} />
    <VoiceConversationCard />
  </div>;
}
export function ComposerQueue() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const session = state.sessions.find((s) => s.id === state.active);
  return <QueueCard key={session?.thread_id || "new"} threadId={session?.thread_id}
    revision={state.queueRevision} canStart={session?.status === "ready" && !session.active_turn_id && !state.sessionRefreshing} />;
}
export function ComposerOptions() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const settings = store.composerSettings;
  const session = state.sessions.find((s) => s.id === state.active);
  const model = state.models.find((m) => m.model === settings.model);
  const efforts = model?.supportedReasoningEfforts?.map((e) => e.reasoningEffort) || [];
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);
  const disabled = state.settingsBusy || state.submitting;
  const label = model?.displayName || settings.model || "默认模型";
  useEffect(() => setIndex(Math.max(0, efforts.indexOf(settings.effort))), [settings.model, settings.effort, open]);
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener("codex-model", show);
    return () => window.removeEventListener("codex-model", show);
  }, []);
  const save = async (effort: string) => {
    if (!await store.updateComposerSettings({ effort })) setIndex(Math.max(0, efforts.indexOf(store.composerSettings.effort)));
  };
  return <div className="composer-options">
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild><button type="button" aria-label="选择推理强度" className="composer-model-trigger" disabled={disabled}>
        <span>{label}</span><span className="muted">{effortNames[settings.effort] || settings.effort}</span><ChevronDown size={12} />
      </button></Popover.Trigger>
      <Popover.Portal><Popover.Content side="top" align="end" sideOffset={8} className="reasoning-popover" aria-label="模型与推理强度">
        <div className="reasoning-heading"><strong>{effortNames[efforts[index] || settings.effort] || efforts[index] || settings.effort}</strong>
          <button type="button" aria-label="重置推理强度" title="恢复模型默认强度" disabled={disabled || !model?.defaultReasoningEffort}
            onClick={() => { const effort = model!.defaultReasoningEffort!; setIndex(Math.max(0, efforts.indexOf(effort))); void save(effort); }}><RotateCcw size={14} /></button></div>
        <DropdownMenu><DropdownMenuTrigger asChild><button type="button" className="reasoning-model" aria-label="选择模型" disabled={disabled}>{label}<ChevronRight size={12} /></button></DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="center" className="min-w-56"><DropdownMenuLabel>模型</DropdownMenuLabel>
            {state.models.map((item) => <DropdownMenuItem key={item.model} onSelect={() => {
              const options = item.supportedReasoningEfforts?.map((e) => e.reasoningEffort) || [];
              const effort = options.includes(settings.effort) ? settings.effort : item.defaultReasoningEffort;
              void store.updateComposerSettings({ model: item.model, ...(effort ? { effort } : {}) });
            }}>{item.displayName || item.model}{item.model === settings.model && <Check className="ml-auto" />}</DropdownMenuItem>)}
            <DropdownMenuSeparator /><DropdownMenuItem onSelect={() => { setOpen(false); openSettings(); }}>模型设置…</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        {efforts.length > 0 ? <Slider.Root className="reasoning-slider" min={0} max={Math.max(1, efforts.length - 1)} step={1} value={[index]}
          disabled={disabled || efforts.length < 2} onValueChange={([value]) => setIndex(value)} onValueCommit={([value]) => void save(efforts[value])}>
          <Slider.Track className="reasoning-track"><Slider.Range className="reasoning-range" /></Slider.Track>
          <div className="reasoning-ticks" aria-hidden>{efforts.map((effort) => <span key={effort} />)}</div>
          <Slider.Thumb className="reasoning-thumb" aria-label="推理强度" aria-valuetext={effortNames[efforts[index]] || efforts[index]} />
        </Slider.Root> : <p className="reasoning-unavailable">此模型未提供可选推理强度</p>}
        {session?.active_turn_id && <p className="reasoning-scope">仅应用到当前回复</p>}
      </Popover.Content></Popover.Portal>
    </Popover.Root>
  </div>;
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
      {store.composerSettings.mode === "plan" && <button type="button" className="composer-plan-badge" aria-label="关闭计划模式" disabled={disabled}
        onClick={() => void store.updateComposerSettings({ mode: "default" })}><Lightbulb size={14} />计划</button>}
      {store.goalInputMode && <button type="button" className="composer-plan-badge" aria-label="退出目标输入" disabled={state.submitting || state.creating}
        onClick={() => void store.chooseGoalMode(false)}><Target size={14} />目标</button>}
      <span className="composer-shortcut">/ 命令</span>
    </div>
  );
}

export function useComposerCommands() {
  const aui = useAui();
  const text = useAuiState((s) => s.composer.text);
  const attachmentCount = useAuiState((s) => s.composer.attachments.length);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const session = state.sessions.find((s) => s.id === state.active);
  const [steering, setSteering] = useState(false);
  const submittingInput = useRef(false);
  const queueAvailable = !!session?.thread_id && !!state.nativeProtocol?.methods.includes("thread/queue/add");
  const goalMode = store.goalInputMode;
  const skillMentions = useSkillMentions({ disabled: goalMode || state.creating || (state.submitting && !session?.active_turn_id), threadId: session?.thread_id });
  const slash = useSlashCommands({ text, state, disabled: goalMode });
  const sendGoal = async () => {
    if (aui.composer().getState().attachments.length) { store.fail(new Error("目标模式只接受文字，请先移除附件")); return; }
    const active = state.active;
    await store.sendGoal(text, (id) => {
      if ((store.state.active === active || active === null && store.state.active === id)
        && aui.composer().getState().text === text) aui.composer().setText("");
    });
  };
  const submitInput = async (kind: "steer" | "queue") => {
    if (!session?.thread_id || (kind === "steer" && !session.active_turn_id) ||
      (kind === "queue" && !queueAvailable) || (!text.trim() && !attachmentCount) ||
      submittingInput.current || state.loading || state.sessionRefreshing || state.settingsBusy) return;
    submittingInput.current = true;
    setSteering(true);
    const draftAttachments = [...aui.composer().getState().attachments];
    try {
      const prepared = await Promise.all(draftAttachments.map((a) => a.status.type === "complete" ? a : attachments.send(a as PendingAttachment)));
      await sharedMutation(`${kind}:${session.thread_id}`).run(kind === "queue" ? "thread/queue/add" : "turn/steer", {
        threadId: session.thread_id,
        ...(kind === "queue" ? { clientUserMessageId: requestKey() } : { expectedTurnId: session.active_turn_id }),
        input: nativeParts([{ type: "text", text }, ...prepared.flatMap((a) => a.content || [])]),
      }, JSON.stringify([session.thread_id, text, draftAttachments.map((a) => a.id)]));
      if (kind === "queue") store.patch({ queueRevision: store.state.queueRevision + 1 });
      if (
        store.state.active === session.id &&
        aui.composer().getState().text === text
      ) {
        store.patch({ error: "" });
        aui.composer().setText("");
        for (const attachment of draftAttachments) {
          if (aui.composer().getState().attachments.some((a) => a.id === attachment.id)) await aui.composer().attachment({ id: attachment.id }).remove();
        }
      }
    } catch (error) {
      store.fail(error);
    } finally {
      submittingInput.current = false;
      setSteering(false);
    }
  };
  const onKeyDownCapture = (event: KeyboardEvent) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.target instanceof HTMLElement && event.target.id !== "text")
      return;
    if (skillMentions.onKeyDownCapture(event)) return;
    if (goalMode && event.key === "Enter" && !event.shiftKey) {
      event.preventDefault(); event.stopPropagation(); void sendGoal(); return;
    }
    if (slash.onKeyDownCapture(event)) return;
    // Native CLI uses Tab for an explicit queued submission. Keep completion
    // menus first, and leave Tab navigation alone for empty/idle composers.
    if (!goalMode && queueAvailable && session?.active_turn_id && (text.trim() || attachmentCount) &&
      event.key === "Tab" && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey) {
      event.preventDefault(); event.stopPropagation(); void submitInput("queue"); return;
    }
    if (event.key === "Enter" && !event.shiftKey && slash.submit()) {
      event.preventDefault(); event.stopPropagation(); return;
    }
    if (
      session?.active_turn_id &&
      event.key === "Enter" &&
      !event.shiftKey
    ) {
      event.preventDefault();
      event.stopPropagation();
      void submitInput("steer");
    }
  };
  return {
    goalMode,
    inputProps: {
      ...skillMentions.inputProps,
      ...slash.inputProps,
      onInput: (event: React.SyntheticEvent<HTMLTextAreaElement>) => { skillMentions.inputProps.onInput(event); slash.inputProps.onInput(event); },
      onFocus: (event: React.SyntheticEvent<HTMLTextAreaElement>) => { skillMentions.inputProps.onFocus(event); slash.inputProps.onFocus(event); },
      onBlur: () => { skillMentions.inputProps.onBlur(); slash.inputProps.onBlur(); },
      "aria-controls": slash.inputProps["aria-controls"] || skillMentions.inputProps["aria-controls"],
      "aria-activedescendant": slash.inputProps["aria-activedescendant"] || skillMentions.inputProps["aria-activedescendant"],
      "aria-expanded": slash.inputProps["aria-expanded"] || !!skillMentions.menu,
    },
    onSubmitCapture: (event: React.FormEvent) => {
      if (goalMode) { event.preventDefault(); event.stopPropagation(); void sendGoal(); }
      else if (slash.submit()) { event.preventDefault(); event.stopPropagation(); }
    },
    onClickCapture: (event: React.MouseEvent) => {
      if (event.target instanceof Element && event.target.closest("#send") && slash.submit()) {
        event.preventDefault(); event.stopPropagation();
      }
    },
    goalButton: goalMode ? <Button type="button" size="icon" className="rounded-full size-9" aria-label="发送目标"
      disabled={!text.trim() || state.submitting || state.creating || state.settingsBusy} onClick={() => void sendGoal()}><ArrowUp className="size-4" /></Button> : null,
    queueButton: !goalMode && queueAvailable && (text.trim() || attachmentCount) ? <TooltipIconButton
      type="button" className="composer-queue-submit" aria-label="加入待发送队列"
      tooltip={session?.active_turn_id ? "加入待发送队列（Tab）" : "加入待发送队列"}
      disabled={steering || state.loading || state.sessionRefreshing || state.settingsBusy}
      onClick={() => { if (!slash.submit()) void submitInput("queue"); }}><ListPlus size={17} /></TooltipIconButton> : null,
    onKeyDownCapture,
    placeholder: goalMode ? "描述你的目标，并设定可衡量的成果，以获得最佳效果" : session?.active_turn_id
      ? "补充说明，或调整接下来的方向…"
      : store.composerSettings.mode === "plan" ? "描述你的任务以生成计划…" : "和 Codex 一起工作",
    menu: skillMentions.menu || slash.menu,
    steerButton:
      !goalMode && session?.active_turn_id && (text.trim() || attachmentCount) ? (
        <Button
          type="button"
          size="icon"
          className="rounded-full size-9"
          aria-label="发送补充"
          disabled={steering}
          onClick={() => { if (!slash.submit()) void submitInput("steer"); }}
        >
          <ArrowUp className="size-4" />
        </Button>
      ) : null,
  };
}
