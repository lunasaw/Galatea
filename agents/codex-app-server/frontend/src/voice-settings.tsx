import { useCallback, useState } from "react";
import { SchemaInput } from "./schema-form";
import { consoleStore as store, type ConsoleState } from "./store";
import { resolveSchema } from "./schema-data";
import { native } from "./native";
import { SettingRow } from "./model-settings";
import { Button } from "./components/ui/button";
import { Feedback, useAction, useResource } from "./capability-controls";
export type VoicePreferences = { version: "" | "v1" | "v2" | "v3"; voice: string; model: string; outputModality: "audio" | "text"; transport: "websocket" | "webrtc"; inputDeviceId: string; outputDeviceId: string; advanced: Record<string, any> };
export function voicePreferences(threadId?: string): VoicePreferences {
  let saved: Partial<VoicePreferences> = {};
  try { saved = JSON.parse(localStorage.getItem("codex-agent.voice") || "{}"); } catch {}
  let advanced: Record<string, any> = {};
  try { if (threadId) advanced = JSON.parse(sessionStorage.getItem(`codex-agent.voice-context.${threadId}`) || "{}"); } catch {}
  return { version: ["v1", "v2", "v3"].includes(saved?.version || "") ? saved.version! : "",
    voice: typeof saved?.voice === "string" ? saved.voice : "", model: typeof saved?.model === "string" ? saved.model : "",
    outputModality: saved?.outputModality === "text" ? "text" : "audio",
    transport: saved?.transport === "webrtc" ? "webrtc" : "websocket",
    inputDeviceId: typeof saved?.inputDeviceId === "string" ? saved.inputDeviceId : "",
    outputDeviceId: typeof saved?.outputDeviceId === "string" ? saved.outputDeviceId : "",
    advanced: advanced && typeof advanced === "object" && !Array.isArray(advanced) ? advanced : {} };
}
export function VoiceSettings({ state, thread = false }: { state: ConsoleState; thread?: boolean }) {
  const resource = useResource(useCallback(() => native("thread/realtime/listVoices"), []));
  const threadId = thread ? state.sessions.find((s) => s.id === state.active)?.thread_id : undefined;
  const [value, setValue] = useState(() => voicePreferences(threadId)); const action = useAction();
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const root = { definitions: state.nativeProtocol?.definitions || {} };
  const schema = resolveSchema(state.nativeProtocol?.params["thread/realtime/start"] as Record<string, any> || {}, root);
  const advancedSchema = { ...schema, required: [], properties: Object.fromEntries(Object.entries(schema.properties || {}).filter(([key]) => !["threadId", "transport", "version", "voice", "model", "outputModality", "realtimeSessionId"].includes(key))) };
  const voices: string[] = value.version === "v1" || value.version === "v2" ? resource.data?.voices?.[value.version] || [] : [];
  return <section className="capability-page"><h3>{thread ? "会话语音上下文" : "语音"}</h3><p className="muted">{thread ? "用于当前聊天下次发起的实时语音；仅在本浏览器标签页保留。" : "此浏览器的音色、设备与传输偏好。"}</p>
    {!thread && <><div className="desktop-settings-group">
      <SettingRow label="语音版本"><select aria-label="语音版本" value={value.version} onChange={(e) => setValue({ ...value, version: e.target.value as VoicePreferences["version"], voice: "" })}>
        <option value="">运行时默认</option><option value="v1">V1</option><option value="v2">V2</option><option value="v3">V3</option></select></SettingRow>
      <SettingRow label="音色" description={!voices.length ? "当前版本使用运行时默认音色。" : undefined}><select aria-label="音色" value={value.voice} disabled={!voices.length} onChange={(e) => setValue({ ...value, voice: e.target.value })}>
        <option value="">默认{value.version === "v1" ? `（${resource.data?.voices?.defaultV1 || ""}）` : value.version === "v2" ? `（${resource.data?.voices?.defaultV2 || ""}）` : ""}</option>
        {voices.map((voice) => <option key={voice}>{voice}</option>)}</select></SettingRow>
      <SettingRow label="回复方式"><select aria-label="语音回复方式" value={value.outputModality} onChange={(e) => setValue({ ...value, outputModality: e.target.value as "audio" | "text" })}><option value="audio">语音</option><option value="text">文字</option></select></SettingRow>
      <SettingRow label="语音模型" description="留空使用运行时配置。"><input aria-label="语音模型" value={value.model} onChange={(e) => setValue({ ...value, model: e.target.value })} placeholder="运行时默认" /></SettingRow>
      <SettingRow label="实时传输"><select aria-label="实时传输" value={value.transport} onChange={(e) => setValue({ ...value, transport: e.target.value as VoicePreferences["transport"] })}><option value="websocket">WebSocket PCM</option><option value="webrtc" disabled={typeof RTCPeerConnection === "undefined"}>WebRTC</option></select></SettingRow>
      <SettingRow label="麦克风"><select aria-label="语音麦克风" value={value.inputDeviceId} onChange={(e) => setValue({ ...value, inputDeviceId: e.target.value })}><option value="">系统默认</option>{devices.filter((d) => d.kind === "audioinput").map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `麦克风 ${i + 1}`}</option>)}</select></SettingRow>
      <SettingRow label="播放设备" description="需要浏览器支持音频输出选择。"><select aria-label="语音播放设备" value={value.outputDeviceId} onChange={(e) => setValue({ ...value, outputDeviceId: e.target.value })}><option value="">系统默认</option>{devices.filter((d) => d.kind === "audiooutput").map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `扬声器 ${i + 1}`}</option>)}</select></SettingRow>
    </div><Button variant="outline" onClick={() => void action.run(async () => { if (!navigator.mediaDevices?.enumerateDevices) throw new Error("浏览器不支持设备枚举，请使用 HTTPS 或 localhost"); setDevices(await navigator.mediaDevices.enumerateDevices()); }, "设备列表已刷新")}>刷新音频设备</Button></>}
    {thread && <details open><summary>初始上下文、指令与 Handoff</summary><SchemaInput schema={advancedSchema} root={root} value={value.advanced} change={(advanced) => setValue({ ...value, advanced })} label="实时高级设置" /></details>}
    <Button disabled={action.busy || (thread ? !threadId : !!value.voice && !voices.includes(value.voice))} onClick={() => void action.run(async () => { if (threadId) sessionStorage.setItem(`codex-agent.voice-context.${threadId}`, JSON.stringify(value.advanced)); else { const { advanced: _, ...preferences } = value; localStorage.setItem("codex-agent.voice", JSON.stringify(preferences)); } }, "语音偏好已保存")}>{thread ? "保存会话语音上下文" : "保存语音偏好"}</Button>
    <Feedback error={resource.error || action.error} status={action.status} /></section>;
}
