import type { AttachmentAdapter, CompleteAttachment, PendingAttachment } from "@assistant-ui/react";
import { requestKey } from "./store";

export class AudioAttachmentAdapter implements AttachmentAdapter {
  accept = "audio/*,.wav,.mp3,.ogg,.m4a,.flac,.webm";
  async add({ file }: { file: File }): Promise<PendingAttachment> {
    if (file.size > 10 * 1024 * 1024) throw new Error("音频附件请控制在 10 MB 以内");
    const extension = file.name.split(".").at(-1)?.toLowerCase() || "";
    const contentType = file.type || ({ wav: "audio/wav", mp3: "audio/mpeg", ogg: "audio/ogg", m4a: "audio/mp4", flac: "audio/flac", webm: "audio/webm" } as Record<string, string>)[extension];
    if (!contentType?.startsWith("audio/")) throw new Error("无法识别音频格式");
    return { id: requestKey(), type: "file", name: file.name, file, contentType, status: { type: "requires-action", reason: "composer-send" } };
  }
  async send(attachment: PendingAttachment, options?: { signal?: AbortSignal }): Promise<CompleteAttachment> {
    options?.signal?.throwIfAborted();
    const bytes = new Uint8Array(await attachment.file.arrayBuffer());
    options?.signal?.throwIfAborted();
    const chunks: string[] = [];
    for (let offset = 0; offset < bytes.length; offset += 8192) chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 8192)));
    return { ...attachment, status: { type: "complete" }, content: [{ type: "file", filename: attachment.name,
      mimeType: attachment.contentType!, data: btoa(chunks.join("")) }] };
  }
  async remove() {}
}

export function nativeAudioUrl(data: string, mimeType: string) {
  if (!/^audio\/[a-z0-9.+-]+$/i.test(mimeType)) throw new Error("无法识别音频格式");
  if (/^(data:audio\/|https?:\/\/)/i.test(data)) return data;
  return `data:${mimeType};base64,${data}`;
}
