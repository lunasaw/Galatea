import { test } from "node:test";
import assert from "node:assert/strict";
import { pcm16, pcmWav, audioBase64 } from "./audio-media";
test("PCM conversion clamps input and writes little-endian samples in a valid mono WAV", async () => {
  const bytes = pcm16(new Float32Array([-2, -1, 0, 1, 2, NaN]));
  const view = new DataView(bytes.buffer);
  assert.deepEqual(Array.from({ length: 6 }, (_, i) => view.getInt16(i * 2, true)), [-32768, -32768, 0, 32767, 32767, 0]);
  assert.deepEqual(Uint8Array.from(atob(audioBase64(bytes)), (c) => c.charCodeAt(0)), bytes);
  const wav = await pcmWav([bytes]).arrayBuffer(); const header = new DataView(wav);
  assert.equal(new TextDecoder().decode(wav.slice(0, 4)), "RIFF");
  assert.equal(header.getUint32(4, true) + 8, wav.byteLength);
  assert.equal(header.getUint32(24, true), 24000); assert.equal(header.getUint16(22, true), 1);
  assert.equal(header.getUint16(34, true), 16); assert.equal(header.getUint32(40, true), bytes.length);
  assert.deepEqual(new Uint8Array(wav.slice(44)), bytes);
});
