import { CompositeAttachmentAdapter, SimpleImageAttachmentAdapter, SimpleTextAttachmentAdapter, type ThreadUserMessagePart, type ThreadAssistantMessagePart } from "@assistant-ui/react";
import { AudioAttachmentAdapter, nativeAudioUrl } from "./audio-attachment";
import { referenceInput } from "./composer-references";
const text = new SimpleTextAttachmentAdapter();
text.accept = "text/*,.md,.txt,.json,.csv,.yaml,.yml,.toml,.py,.js,.ts,.tsx,.jsx,.html,.css,.sh,.sql,.xml,.log";
export const attachments = new CompositeAttachmentAdapter([new SimpleImageAttachmentAdapter(), new AudioAttachmentAdapter(), text]);
export function nativeParts(parts: readonly (ThreadUserMessagePart | ThreadAssistantMessagePart)[]): Record<string, unknown>[] {
  return parts.flatMap<Record<string, unknown>>((p) => p.type === "text" ? [{ type: "text", text: p.text, text_elements: [] }]
    : p.type === "image" ? [{ type: "image", url: p.image }]
    : p.type === "file" && p.mimeType.startsWith("audio/") ? [{ type: "audio", url: nativeAudioUrl(p.data, p.mimeType) }] : referenceInput(p));
}
