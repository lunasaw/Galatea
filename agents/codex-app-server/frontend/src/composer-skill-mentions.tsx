import { useEffect, useId, useRef, useState, type KeyboardEvent, type SyntheticEvent } from "react";
import { useAui, useAuiState } from "@assistant-ui/react";
import { native } from "./native";
import { consoleStore as store } from "./store";
import { useAddReference } from "./composer-references";
import { filterSkills, skillMentionAt, skillReferences, type SkillReference } from "./skill-mentions";

export function useSkillMentions({ disabled, threadId }: { disabled: boolean; threadId?: string }) {
  const aui = useAui();
  const text = useAuiState((s) => s.composer.text);
  const addReference = useAddReference();
  const input = useRef<HTMLTextAreaElement | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const choosing = useRef(false);
  const composing = useRef(false);
  const [cursor, setCursor] = useState({ text: "", start: 0, end: 0, focused: false });
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [catalog, setCatalog] = useState<{ key: string; rows: SkillReference[]; error: string } | null>(null);
  const cwd = store.cwd;
  const context = JSON.stringify([cwd, threadId]);
  const token = !disabled && cursor.focused && cursor.text === text ? skillMentionAt(text, cursor.start, cursor.end) : null;
  const tokenKey = JSON.stringify([context, text, token?.start, token?.end, token?.query]);
  const open = !!token && dismissed !== tokenKey;
  const id = useId();
  const loading = open && catalog?.key !== context;
  const rows = open && !loading ? filterSkills(catalog?.rows || [], token!.query) : [];
  const index = rows.length ? selected % rows.length : 0;

  useEffect(() => { setSelected(0); setDismissed(null); }, [text, cursor.start, cursor.end, context]);
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    setCatalog(null);
    void native("skills/list", { cwds: cwd ? [cwd] : [], forceReload: true }).then((result) => {
      if (!disposed) setCatalog({ key: context, rows: skillReferences(result), error: (result.data || []).flatMap((entry: { errors?: { message: string }[] }) => entry.errors || []).map((error: { message: string }) => error.message).join("\n") });
    }).catch((error) => {
      if (!disposed) setCatalog({ key: context, rows: [], error: String(error) });
    });
    return () => { disposed = true; };
  }, [open, cwd, threadId, context]);
  useEffect(() => { menuRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [index]);

  const trackCursor = (event: SyntheticEvent<HTMLTextAreaElement>) => {
    const element = event.currentTarget;
    input.current = element;
    // The browser owns the provisional text and selection until IME commits.
    if (composing.current || (event.nativeEvent as InputEvent).isComposing) return;
    // Let ComposerPrimitive.Input publish its controlled value first; an
    // earlier render can restore stale text and move the caret to the end.
    queueMicrotask(() => {
      if (composing.current || input.current !== element || document.activeElement !== element) return;
      setCursor({ text: element.value, start: element.selectionStart, end: element.selectionEnd, focused: true });
    });
  };
  const choose = async (row: SkillReference) => {
    if (!token || choosing.current) return;
    choosing.current = true;
    const composer = aui.composer();
    const active = store.state.active;
    try {
      await addReference(row);
      if (store.state.active !== active || composer.getState().text !== text) return;
      // The removable reference chip owns the binding, so remove only the
      // completed token and leave the surrounding draft untouched.
      const next = text.slice(0, token.start) + text.slice(token.end);
      composer.setText(next);
      setCursor({ text: next, start: token.start, end: token.start, focused: true });
      requestAnimationFrame(() => {
        input.current?.focus();
        input.current?.setSelectionRange(token.start, token.start);
      });
    } catch (error) { store.fail(error); }
    finally { choosing.current = false; }
  };
  const onKeyDownCapture = (event: KeyboardEvent) => {
    if (!open || event.nativeEvent.isComposing || event.keyCode === 229 || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return false;
    if (!(event.target instanceof HTMLTextAreaElement) || event.target !== input.current) return false;
    if (event.key === "Escape" || rows.length && ["ArrowDown", "ArrowUp", "Enter", "Tab"].includes(event.key) || loading && ["Enter", "Tab"].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      if (event.key === "Escape") setDismissed(tokenKey);
      else if (rows.length && ["Enter", "Tab"].includes(event.key)) void choose(rows[index]);
      else if (rows.length) setSelected((index + (event.key === "ArrowUp" ? rows.length - 1 : 1)) % rows.length);
      return true;
    }
    return false;
  };

  return {
    onKeyDownCapture,
    inputProps: {
      onInput: trackCursor, onSelect: trackCursor, onFocus: trackCursor,
      onCompositionStart: () => { composing.current = true; },
      onCompositionEnd: (event: SyntheticEvent<HTMLTextAreaElement>) => {
        composing.current = false;
        trackCursor(event);
      },
      onBlur: () => {
        composing.current = false;
        setCursor((value) => ({ ...value, focused: false }));
      },
      "aria-autocomplete": "list" as const,
      "aria-controls": open ? id : undefined,
      "aria-activedescendant": open && rows.length ? `${id}-${index}` : undefined,
    },
    menu: open ? <div className="slash-menu skill-mention-menu" ref={menuRef}>
      <div className="skill-mention-heading">引用 Skill <small>↑↓ 选择 · Enter / Tab 确认 · Esc 关闭</small></div>
      <div id={id} role="listbox" aria-label="引用 Skill" aria-busy={loading}>
        {rows.map((row, i) => <button type="button" role="option" id={`${id}-${i}`} key={row.path}
          aria-selected={i === index} onMouseDown={(event) => event.preventDefault()} onClick={() => void choose(row)}>
          <strong>{row.label}<code>${row.name}</code></strong>
          <span>{row.description}<small>{row.path}</small></span>
        </button>)}
      </div>
      {loading ? <p role="status">正在读取可用 Skills…</p> : !rows.length && <p role="status">没有匹配的可用 Skill</p>}
      {!loading && catalog?.error && <p role="alert">{catalog.error}</p>}
    </div> : null,
  };
}
