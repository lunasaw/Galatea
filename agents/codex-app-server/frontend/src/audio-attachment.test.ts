import { test } from "node:test";
import assert from "node:assert/strict";
import { AudioAttachmentAdapter, nativeAudioUrl } from "./audio-attachment";
import { toMessages } from "./messages";

test("audio attachments preserve bytes and map to native audio input and playback", async () => {
  const adapter = new AudioAttachmentAdapter();
  const file = new File([new Uint8Array([82, 73, 70, 70, 255])], "recording.wav", { type: "audio/wav" });
  const complete = await adapter.send(await adapter.add({ file }));
  const part = complete.content[0]; assert.equal(part.type, "file");
  if (part.type !== "file") return;
  const url = nativeAudioUrl(part.data, part.mimeType);
  assert.equal(url, "data:audio/wav;base64,UklGRv8=");
  const result = toMessages({ epoch: "e", watermark: "e:1", next_page: null, coverage: { status: "complete" },
    messages: [{ id: "voice", role: "user", text: "", first_seq: "1", last_seq: "1", content: [{ type: "audio", url }] }] });
  assert.deepEqual(result[0].content[1], { type: "file", filename: "语音", mimeType: "audio/wav", data: url, sourceType: "url" });
});

test("audio preparation respects cancellation and rejects non-audio MIME types", async () => {
  const adapter = new AudioAttachmentAdapter();
  await assert.rejects(adapter.add({ file: new File(["html"], "page.html", { type: "text/html" }) }));
  assert.throws(() => nativeAudioUrl("data:text/html;base64,", "text/html"));
  const pending = await adapter.add({ file: new File(["wav"], "voice.wav") });
  const controller = new AbortController(); controller.abort();
  await assert.rejects(adapter.send(pending, { signal: controller.signal }));
});
