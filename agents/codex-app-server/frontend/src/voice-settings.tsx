import { useCallback, useState } from "react";
import { native } from "./native";
import { SettingRow } from "./model-settings";
import { Button } from "./components/ui/button";
import { Feedback, useAction, useResource } from "./capability-controls";
export type VoicePreferences = { version: "" | "v1" | "v2" | "v3"; voice: string; model: string; outputModality: "audio" | "text" };
export function voicePreferences(): VoicePreferences {
  let saved: Partial<VoicePreferences> = {};
  try { saved = JSON.parse(localStorage.getItem("codex-agent.voice") || "{}"); } catch {}
  return { version: ["v1", "v2", "v3"].includes(saved?.version || "") ? saved.version! : "",
    voice: typeof saved?.voice === "string" ? saved.voice : "", model: typeof saved?.model === "string" ? saved.model : "",
    outputModality: saved?.outputModality === "text" ? "text" : "audio" };
}
export function VoiceSettings() {
  const resource = useResource(useCallback(() => native("thread/realtime/listVoices"), []));
  const [value, setValue] = useState(voicePreferences); const action = useAction();
  const voices: string[] = value.version === "v1" || value.version === "v2" ? resource.data?.voices?.[value.version] || [] : [];
  return <section className="capability-page"><h3>语音</h3><p className="muted">用于此浏览器发起的实时语音对话。</p>
    <div className="desktop-settings-group">
      <SettingRow label="语音版本"><select aria-label="语音版本" value={value.version} onChange={(e) => setValue({ ...value, version: e.target.value as VoicePreferences["version"], voice: "" })}>
        <option value="">运行时默认</option><option value="v1">V1</option><option value="v2">V2</option><option value="v3">V3</option></select></SettingRow>
      <SettingRow label="音色" description={!voices.length ? "当前版本使用运行时默认音色。" : undefined}><select aria-label="音色" value={value.voice} disabled={!voices.length} onChange={(e) => setValue({ ...value, voice: e.target.value })}>
        <option value="">默认{value.version === "v1" ? `（${resource.data?.voices?.defaultV1 || ""}）` : value.version === "v2" ? `（${resource.data?.voices?.defaultV2 || ""}）` : ""}</option>
        {voices.map((voice) => <option key={voice}>{voice}</option>)}</select></SettingRow>
      <SettingRow label="回复方式"><select aria-label="语音回复方式" value={value.outputModality} onChange={(e) => setValue({ ...value, outputModality: e.target.value as "audio" | "text" })}><option value="audio">语音</option><option value="text">文字</option></select></SettingRow>
      <SettingRow label="语音模型" description="留空使用运行时配置。"><input aria-label="语音模型" value={value.model} onChange={(e) => setValue({ ...value, model: e.target.value })} placeholder="运行时默认" /></SettingRow>
    </div><Button disabled={action.busy || (!!value.voice && !voices.includes(value.voice))} onClick={() => void action.run(async () => { localStorage.setItem("codex-agent.voice", JSON.stringify(value)); }, "语音偏好已保存")}>保存语音偏好</Button>
    <Feedback error={resource.error || action.error} status={action.status} /></section>;
}
