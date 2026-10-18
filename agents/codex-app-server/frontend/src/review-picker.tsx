import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { ArrowLeft, ChevronRight, LoaderCircle, Search, X } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./components/ui/dialog";
import { Button } from "./components/ui/button";
import { Feedback, useAction, useResource } from "./capability-controls";
import { native } from "./native";
import { sharedMutation } from "./native-mutation";
import { consoleStore as store, type ConsoleState } from "./store";
import { readReviewChoices, type ReviewChoice, type ReviewTarget } from "./review-targets";
import "./review-picker.css";

const presets = [
  { id: "baseBranch", label: "与基准分支比较", detail: "PR 风格" },
  { id: "uncommittedChanges", label: "审查未提交的更改" },
  { id: "commit", label: "审查指定提交" },
  { id: "custom", label: "自定义审查要求" },
] as const;
type Preset = typeof presets[number]["id"];
type Choice = { id: string; label: string; detail?: string; search?: string };

function Choices<T extends Choice>({ choices, label, disabled, searchable = false, initialIndex = 0, onIndexChange, onChoose }: {
  choices: T[]; label: string; disabled: boolean; searchable?: boolean; initialIndex?: number;
  onIndexChange?: (index: number) => void; onChoose: (choice: T) => void;
}) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(initialIndex);
  const root = useRef<HTMLDivElement>(null);
  const listId = useId();
  const filtered = choices.filter((choice) => (choice.search || choice.label).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const selected = Math.min(index, filtered.length - 1);
  const optionId = (at: number) => `${listId}-${at}`;
  const select = (at: number) => { setIndex(at); onIndexChange?.(at); };
  useEffect(() => { root.current?.querySelector<HTMLElement>(searchable ? "input" : '[role="listbox"]')?.focus(); }, [searchable]);
  useEffect(() => { document.getElementById(optionId(selected))?.scrollIntoView({ block: "nearest" }); }, [selected, query]);
  const keydown = (event: KeyboardEvent) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return;
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) && filtered.length) {
      event.preventDefault();
      select(event.key === "Home" ? 0 : event.key === "End" ? filtered.length - 1
        : (selected + (event.key === "ArrowDown" ? 1 : -1) + filtered.length) % filtered.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (!disabled && filtered[selected]) onChoose(filtered[selected]);
    }
  };
  return <div ref={root} className="review-choices" onKeyDown={keydown}>
    {searchable && <label className="review-search"><Search size={16} aria-hidden="true" />
      <input role="combobox" aria-label={label === "基准分支" ? "搜索分支" : "搜索提交"}
        aria-expanded="true" aria-controls={listId} aria-activedescendant={selected >= 0 ? optionId(selected) : undefined}
        autoComplete="off" placeholder={label === "基准分支" ? "搜索分支…" : "搜索提交标题或 SHA…"}
        value={query} onChange={(event) => { setQuery(event.target.value); select(0); }} />
    </label>}
    <div id={listId} className="review-choice-list" role="listbox" aria-label={label}
      tabIndex={searchable ? -1 : 0} aria-activedescendant={selected >= 0 ? optionId(selected) : undefined}>
      {filtered.map((choice, at) => <button key={choice.id} id={optionId(at)} type="button" role="option" tabIndex={-1}
        aria-selected={selected === at} aria-disabled={disabled} className="review-choice"
        onPointerMove={() => select(at)} onClick={() => { select(at); if (!disabled) onChoose(choice); }}>
        <ChevronRight size={16} className="review-choice-cursor" aria-hidden="true" />
        {!searchable && <span className="review-choice-number" aria-hidden="true">{at + 1}.</span>}
        <span className="review-choice-label">{choice.label}</span>
        {choice.detail && <span className="review-choice-detail">{choice.detail}</span>}
      </button>)}
      {!filtered.length && <p className="review-empty">{query ? "没有匹配的结果" : label === "基准分支" ? "当前仓库没有本地分支" : "当前分支没有可审查的提交"}</p>}
    </div>
  </div>;
}

function GitChoices({ cwd, kind, disabled, onChoose }: {
  cwd: string; kind: "baseBranch" | "commit"; disabled: boolean; onChoose: (target: ReviewTarget) => void;
}) {
  const resource = useResource(useCallback(() => readReviewChoices({ nativeCall: native }, cwd, kind), [cwd, kind]));
  return <>
    {resource.data?.currentBranch && <p className="review-current-branch">当前分支：{resource.data.currentBranch}</p>}
    {resource.loading ? <p role="status" className="review-loading"><LoaderCircle size={16} className="animate-spin" />正在读取{kind === "baseBranch" ? "分支" : "提交"}…</p>
      : resource.error ? <div className="review-load-error"><Feedback error={resource.error} /><Button variant="outline" size="sm" onClick={() => void resource.refresh()}>重试</Button></div>
        : <Choices<ReviewChoice> choices={resource.data?.choices || []} label={kind === "baseBranch" ? "基准分支" : "提交"}
          searchable disabled={disabled} onChoose={(choice) => onChoose(choice.target)} />}
  </>;
}

export function ReviewPicker({ state, onClose }: { state: ConsoleState; onClose: () => void }) {
  const session = state.sessions.find((row) => row.id === state.active);
  const [step, setStep] = useState<"presets" | "baseBranch" | "commit" | "custom">("presets");
  const [presetIndex, setPresetIndex] = useState(0);
  const [instructions, setInstructions] = useState("");
  const [delivery, setDelivery] = useState("inline");
  const action = useAction();
  const ready = !!session?.thread_id && session.status === "ready" && !session.active_turn_id && !state.settingsBusy;
  const disabled = action.busy || !ready;
  const title = step === "presets" ? "选择审查类型" : step === "baseBranch" ? "选择基准分支" : step === "commit" ? "选择要审查的提交" : "自定义审查要求";
  const back = () => { if (!action.busy) { if (step === "presets") onClose(); else setStep("presets"); } };
  const start = (target: ReviewTarget) => {
    if (disabled || !session?.thread_id) return;
    void action.run(async () => {
      const result = await sharedMutation(`review:${JSON.stringify([session.id, target, delivery])}`).run("review/start", {
        threadId: session.thread_id, target, delivery,
      });
      if (store.state.active === session.id) onClose();
      if (result.session?.id) { await store.refreshSessions(); await store.select(result.session.id); }
      store.schedule();
      void store.refreshSessions().catch((error) => store.fail(error));
    });
  };
  const choosePreset = (preset: Preset) => {
    if (preset === "uncommittedChanges") start({ type: preset });
    else setStep(preset);
  };
  return <Dialog open onOpenChange={(open) => { if (!open && !action.busy) onClose(); }}>
    <DialogContent className="review-picker" showCloseButton={false}
      onOpenAutoFocus={(event) => event.preventDefault()}
      onEscapeKeyDown={(event) => {
        if (event.isComposing || event.keyCode === 229) { event.preventDefault(); return; }
        event.preventDefault(); back();
      }}
      onCloseAutoFocus={(event) => { event.preventDefault(); requestAnimationFrame(() => document.getElementById("text")?.focus()); }}>
      <div className="review-picker-heading"><DialogTitle>{title}</DialogTitle>
        <button type="button" className="review-close" aria-label="关闭审查" disabled={action.busy} onClick={onClose}><X size={16} /></button>
      </div>
      <DialogDescription className="sr-only">选择审查范围和位置，确认后开始原生代码审查。</DialogDescription>
      <label>审查位置<select aria-label="审查位置" disabled={disabled} value={delivery} onChange={(e) => setDelivery(e.target.value)}><option value="inline">当前聊天</option><option value="detached">独立审查聊天（原生已弃用）</option></select></label>
      {step === "presets" ? <Choices choices={[...presets]} label="审查类型" disabled={disabled}
        initialIndex={presetIndex} onIndexChange={setPresetIndex} onChoose={(choice) => choosePreset(choice.id)} />
        : step === "custom" ? <form className="review-custom" onSubmit={(event) => { event.preventDefault(); if (instructions.trim()) start({ type: "custom", instructions: instructions.trim() }); }}>
          <textarea autoFocus aria-label="审查要求" placeholder="输入审查要求，按 Enter 开始…" value={instructions} disabled={action.busy}
            onChange={(event) => setInstructions(event.target.value)} onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
                event.preventDefault(); if (instructions.trim()) start({ type: "custom", instructions: instructions.trim() });
              }
            }} />
          <Button type="submit" size="sm" disabled={disabled || !instructions.trim()}>开始审查</Button>
        </form>
          : <GitChoices key={step} cwd={session?.cwd || store.cwd} kind={step} disabled={disabled} onChoose={start} />}
      {!ready && <p className="review-notice" role="status">{session?.thread_id ? "请等待当前会话就绪后再开始审查" : "请先打开一个聊天"}</p>}
      {action.busy && <p className="review-loading" role="status"><LoaderCircle size={16} className="animate-spin" />正在启动审查…</p>}
      {action.error && <div className="review-start-error"><Feedback error={action.error} /></div>}
      <div className="review-picker-footer"><button type="button" disabled={action.busy} onClick={back}>
        {step !== "presets" && <ArrowLeft size={13} />}{step === "presets" ? "取消" : "返回"}
      </button><span>{step === "custom" ? <><kbd>Shift Enter</kbd> 换行 · </> : <><kbd>↑↓</kbd> 选择 · </>}<kbd>Enter</kbd> 确认 · <kbd>Esc</kbd> 返回</span></div>
    </DialogContent>
  </Dialog>;
}
