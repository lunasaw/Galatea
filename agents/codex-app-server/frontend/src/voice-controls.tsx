import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useAui, useAuiState } from "@assistant-ui/react";
import { AudioLines, Mic } from "lucide-react";
import { Button } from "./components/ui/button";
import { Feedback, useAction } from "./capability-controls";
import { MicrophoneCapture, pcm16, pcmWav } from "./audio-media";
import { RealtimeSession } from "./realtime-session";
import { native, type Obj } from "./native";
import { consoleStore as store } from "./store";
import { transcriptEvent, type TranscriptState } from "./voice-transcript";

export function VoiceButtons() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const empty = useAuiState((s) => s.composer.isEmpty);
  const running = useAuiState((s) => s.thread.isRunning);
  const show = (kind: string) => window.dispatchEvent(new CustomEvent("codex-voice", { detail: kind }));
  return <><button type="button" className="composer-microphone" aria-label="录音作为消息附件" disabled={store.goalInputMode || state.submitting || state.creating} onClick={() => show("record")}><Mic size={16} /></button>
    {empty && !running && !store.goalInputMode && <button type="button" className="composer-realtime" aria-label="开始实时语音对话" disabled={state.creating || state.submitting} onClick={() => show("realtime")}><AudioLines size={19} /></button>}</>;
}

export function VoiceConversationCard() {
  const aui = useAui(); const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const composer = useRef(aui); composer.current = aui;
  const [kind, setKind] = useState<"record" | "realtime" | "">(""); const [status, setStatus] = useState("");
  const [error, setError] = useState(""); const [seconds, setSeconds] = useState(0); const [muted, setMuted] = useState(false);
  const [text, setText] = useState(""); const [speech, setSpeech] = useState(false); const action = useAction();
  const [transcript, setTranscript] = useState<TranscriptState>({ canonical: false, lines: [] }); const [timeline, setTimeline] = useState<Obj[]>([]); const [cursor, setCursor] = useState<string | null>(null);
  const recording = useRef<MicrophoneCapture | undefined>(undefined); const realtime = useRef<RealtimeSession | undefined>(undefined);
  const chunks = useRef<Uint8Array[]>([]); const bytes = useRef(0); const origin = useRef<string | null>(null);
  const owner = useRef(0); const mode = useRef(""); const started = useRef(0); const timelineThread = useRef("");
  const loadTimeline = async (next?: string) => {
    if (!timelineThread.current) return;
    const threadId = timelineThread.current;
    const result = await native("thread/timeline/list", { threadId, limit: 50, ...(next ? { cursor: next } : {}) });
    if (threadId !== timelineThread.current) return;
    setTimeline((old) => next ? [...old, ...result.data] : result.data); setCursor(result.nextCursor);
  };
  const fail = (failure: unknown) => { setError(String(failure)); setStatus("语音异常"); };
  const stop = async (save = false) => {
    const capture = recording.current; const session = realtime.current;
    if (capture) {
      if (save) await capture.flush();
      await capture.close();
      if (save && store.state.active === origin.current && bytes.current > 0 && !store.goalInputMode) {
        await composer.current.composer().addAttachment(new File([pcmWav(chunks.current)], `录音-${new Date().toISOString().replaceAll(":", "-")}.wav`, { type: "audio/wav" }));
      }
      recording.current = undefined; chunks.current = []; bytes.current = 0;
    }
    if (session) {
      await session.stop(); realtime.current = undefined; setStatus("语音已结束");
      void loadTimeline().catch(fail);
    }
    mode.current = "";
    if (!session) setKind("");
  };
  useEffect(() => {
    const start = async (event: Event) => {
      if (mode.current || recording.current || realtime.current) return;
      const value = (event as CustomEvent).detail as "record" | "realtime" | "history";
      if (value === "history") {
        origin.current = store.state.active;
        timelineThread.current = store.state.sessions.find((s) => s.id === origin.current)?.thread_id || "";
        setKind("realtime"); setStatus("语音记录"); setError(""); setSeconds(0); setTranscript({ canonical: false, lines: [] });
        await loadTimeline().catch(fail); return;
      }
      if (!["record", "realtime"].includes(value) || store.goalInputMode) return;
      const generation = ++owner.current; origin.current = store.state.active;
      mode.current = value; setKind(value); setError(""); setTranscript({ canonical: false, lines: [] }); setTimeline([]); setCursor(null); setSeconds(0); setMuted(false); setStatus("连接麦克风…");
      started.current = Date.now(); chunks.current = []; bytes.current = 0; timelineThread.current = "";
      try {
        if (value === "record") {
          const capture = new MicrophoneCapture(); recording.current = capture;
          const accepted = await capture.start((samples) => {
            if (owner.current !== generation) return;
            const chunk = pcm16(samples); chunks.current.push(chunk); bytes.current += chunk.length;
            if (bytes.current > 9 * 1024 * 1024) { capture.mute(true); void capture.close(); setStatus("已达到录音时长上限，请保存附件"); }
          });
          if (accepted && owner.current === generation) setStatus("正在录音");
        } else {
          const session = new RealtimeSession((method, params) => {
            if (owner.current !== generation) return;
            if (method === "connected") { setStatus("实时语音已连接"); timelineThread.current = session.threadId; }
            else if (method === "thread/realtime/closed") { setStatus("语音已结束"); mode.current = ""; realtime.current = undefined; void loadTimeline().catch(fail); }
            else setTranscript((old) => transcriptEvent(old, method, params));
          }, (e) => { if (owner.current === generation) fail(e); });
          realtime.current = session; await session.start(origin.current);
        }
      } catch (failure) { if (owner.current === generation) { fail(failure); mode.current = ""; } }
    };
    const listener = (event: Event) => { void start(event); };
    window.addEventListener("codex-voice", listener);
    return () => { window.removeEventListener("codex-voice", listener); owner.current++; void recording.current?.close(); void realtime.current?.stop().catch(store.fail); };
  }, []);
  useEffect(() => {
    if (origin.current === state.active) return;
    const threadId = state.sessions.find((s) => s.id === state.active)?.thread_id;
    if (origin.current === null && realtime.current?.threadId === threadId) { origin.current = state.active; return; }
    owner.current++; void stop().catch(store.fail); setKind(""); timelineThread.current = "";
  }, [state.active]);
  useEffect(() => {
    if (!kind) return;
    const timer = setInterval(() => { if (mode.current) setSeconds(Math.floor((Date.now() - started.current) / 1000)); }, 1000);
    return () => clearInterval(timer);
  }, [kind]);
  if (!kind) return null;
  return <section className="voice-conversation-card" aria-label={kind === "record" ? "录音附件" : "实时语音"}>
    <header><strong>{status}</strong><time>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}</time></header>
    {kind === "record" ? <div className="native-options"><Button type="button" disabled={action.busy} onClick={() => void action.run(() => stop(true), "录音已添加为附件")}>停止并添加附件</Button><Button type="button" variant="ghost" onClick={() => void action.run(() => stop())}>取消录音</Button></div>
      : <><div className="voice-transcript" aria-live="polite">{transcript.lines.map((line, index) => <p key={line.id || index}><strong>{line.role === "user" ? "你" : "Codex"}</strong> {line.text}</p>)}</div>
        {realtime.current ? <><div className="native-options"><Button type="button" variant="outline" aria-pressed={muted} onClick={() => { realtime.current?.mute(!muted); setMuted(!muted); }}>{muted ? "取消静音" : "静音麦克风"}</Button>
          <Button type="button" variant="outline" disabled={action.busy} onClick={() => void action.run(() => stop(), "语音已结束")}>结束语音</Button></div>
          <form onSubmit={(e) => { e.preventDefault(); void action.run(async () => { if (!realtime.current) throw new Error("语音已结束"); await realtime.current.text(text, speech); setText(""); }, "已发送"); }}><input aria-label="语音对话文字" value={text} onChange={(e) => setText(e.target.value)} placeholder="输入补充说明…" /><label><input type="checkbox" checked={speech} onChange={(e) => setSpeech(e.target.checked)} />朗读这段文字</label><Button disabled={action.busy || !text.trim()}>发送文字</Button></form></>
          : <Button type="button" variant="ghost" onClick={() => setKind("")}>收起语音记录</Button>}
        <details><summary onClick={() => { if (!timeline.length) void loadTimeline().catch(fail); }}>聊天与语音时间线</summary><VoiceTimeline entries={timeline} />{cursor && <Button type="button" variant="ghost" onClick={() => void loadTimeline(cursor).catch(fail)}>加载更多记录</Button>}</details></>}
    <Feedback error={error || action.error} status={action.status} />
  </section>;
}
function VoiceTimeline({ entries }: { entries: Obj[] }) {
  return <ol className="voice-timeline">{entries.map((entry) => {
    const item = entry.item;
    const label = entry.type === "turnStarted" ? "回复开始" : entry.type === "turnCompleted" ? "回复结束" : item?.type === "realtimeSessionStarted" ? "语音开始" : item?.type === "realtimeSessionClosed" ? "语音结束" : item?.role === "user" || item?.type === "userMessage" ? "你" : "Codex";
    const text = item?.text || item?.content?.filter((p: Obj) => p.type === "text").map((p: Obj) => p.text).join("\n");
    return <li key={entry.position}><small>{label}</small>{text && <p>{text}</p>}{item?.type === "bemItemPromoted" && <span>引用了聊天回复</span>}</li>;
  })}</ol>;
}
