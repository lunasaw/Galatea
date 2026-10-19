import { useRef, useState, useSyncExternalStore } from "react";
import { useAuiState } from "@assistant-ui/react";
import { consoleStore as store } from "./store";
import { native, openNativeThread } from "./native";
import { answeredQuestions, questionIdentity, replyText, textInput } from "./conversation-capabilities";
import { Button } from "./components/ui/button";
import { WorkspaceLink } from "./workspace-file";
import type { Message } from "./types";
import { sharedMutation } from "./native-mutation";
import { NativeImage, NativeLocalAudio } from "./native-item-details";

export function NativeUserInputs() {
  const content = useAuiState((s) => s.message.metadata.custom?.nativeContent) as Message["content"];
  return <>{content?.map((part, i) => part.type === "localImage" && part.path ? <NativeImage key={i} path={part.path} caption={part.path.split("/").at(-1) || "附加图片"} />
    : part.type === "localAudio" && part.path ? <NativeLocalAudio key={i} path={part.path} />
    : (part.type === "skill" || part.type === "mention") && part.name ? <span key={i} className="native-input-reference">{part.type === "skill" ? "Skill" : "引用"} · {part.name}</span> : null)}</>;
}

export function NativeMessageExtras() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const meta = useAuiState((s) => s.message.metadata.custom);
  const itemId = typeof meta?.itemId === "string" ? meta.itemId : "";
  const questions = meta?.questions as Message["questions"];
  const citation = meta?.memoryCitation as Message["memoryCitation"];
  const answered = answeredQuestions(state.snapshot?.messages || []);
  return <>
    {questions?.map((question, index) => <AsyncQuestion key={questionIdentity(itemId, index)}
      identity={questionIdentity(itemId, index)} question={question} sessionId={state.active}
      answered={answered.has(questionIdentity(itemId, index)) || answered.has(itemId)} />)}
    {citation && <details className="memory-citations">
      <summary>引用了 {citation.entries.length} 条记忆</summary>
      {citation.entries.map((entry, index) => <p key={index}>
        <WorkspaceLink href={`${entry.path}#L${entry.lineStart}-L${entry.lineEnd}`}>{entry.path} · {entry.lineStart}–{entry.lineEnd}</WorkspaceLink>
        {entry.note && <span> · {entry.note}</span>}
      </p>)}
      {citation.threadIds.map((id) => <Button key={id} variant="ghost" size="sm"
        onClick={() => void openNativeThread(id).catch(store.fail)}>查看来源会话</Button>)}
    </details>}
  </>;
}

function AsyncQuestion({ identity, question, sessionId, answered }: {
  identity: string; question: { title: string; options?: string[] | null }; sessionId: string | null; answered: boolean;
}) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState("");
  const mutation = sharedMutation(`question:${sessionId}:${identity}`);
  const send = async () => {
    const session = store.state.sessions.find((s) => s.id === sessionId);
    if (!session?.thread_id || !answer.trim() || busy) return;
    const text = replyText([{ questionItemId: identity, question: question.title, answer }]);
    setBusy(true); setError("");
    try {
      const replyId = JSON.stringify([session.thread_id, identity, text]);
      if (session.active_turn_id) await mutation.run("turn/steer", {
        threadId: session.thread_id, expectedTurnId: session.active_turn_id, input: textInput(text),
      }, replyId);
      else await mutation.run("turn/start", { threadId: session.thread_id, input: textInput(text) }, replyId);
      setAccepted(true);
      store.schedule();
      await store.refreshSessions();
    } catch (error) { setError(String(error)); }
    finally { setBusy(false); }
  };
  return <section className="async-question" aria-label="异步问题">
    <p>{question.title}</p>
    {answered || accepted ? <small role="status">已提交回答</small> : <fieldset disabled={busy}>
      <div className="question-choices">{question.options?.map((option, index) => <button type="button" key={index}
        aria-pressed={answer === option} onClick={() => setAnswer(option)}>{option}</button>)}</div>
      <label><span className="sr-only">回答：{question.title}</span><textarea value={answer}
        onChange={(event) => setAnswer(event.target.value)} placeholder="选择一项，或输入你的回答" /></label>
      <Button size="sm" disabled={!answer.trim()} onClick={() => void send()}>提交回答</Button>
    </fieldset>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
