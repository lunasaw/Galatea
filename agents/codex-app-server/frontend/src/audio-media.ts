export const INPUT_RATE = 24_000;
export function pcm16(samples: Float32Array): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(samples.length * 2); const view = new DataView(bytes.buffer);
  samples.forEach((sample, index) => {
    const value = Number.isFinite(sample) ? Math.min(1, Math.max(-1, sample)) : 0;
    view.setInt16(index * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
  });
  return bytes;
}
export function audioBase64(bytes: Uint8Array) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}
export function pcmWav(chunks: Uint8Array[], sampleRate = INPUT_RATE): Blob {
  const size = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const header = new Uint8Array(44); const view = new DataView(header.buffer);
  const text = (offset: number, value: string) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, size + 36, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, size, true);
  return new Blob([header, ...chunks.map((c) => new Uint8Array(c))], { type: "audio/wav" });
}
export class MicrophoneCapture {
  context: AudioContext;
  private stream?: MediaStream;
  private node?: AudioWorkletNode;
  private source?: MediaStreamAudioSourceNode;
  private closed = false;
  private flushed?: () => void;
  constructor() {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("录音需要 HTTPS 或 localhost，并允许浏览器使用麦克风");
    this.context = new AudioContext({ sampleRate: INPUT_RATE });
    void this.context.resume();
  }
  async start(receive: (samples: Float32Array) => void, deviceId = "") {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { ...(deviceId ? { deviceId: { exact: deviceId } } : {}), channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false });
      if (this.closed) { this.stream.getTracks().forEach((t) => t.stop()); return false; }
      await this.context.audioWorklet.addModule("/audio-capture-worklet.js");
      if (this.closed) return false;
      this.source = this.context.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.context, "codex-audio-capture");
      this.node.port.onmessage = ({ data }) => {
        if (data.stopped) this.flushed?.();
        else if (!this.closed) receive(data.samples);
      };
      this.source.connect(this.node); this.node.connect(this.context.destination);
      return true;
    } catch (error) { await this.close(); throw error; }
  }
  mute(value: boolean) { this.stream?.getAudioTracks().forEach((t) => { t.enabled = !value; }); }
  async flush() {
    if (!this.node || this.closed) return;
    await new Promise<void>((resolve) => {
      const timeout = setTimeout(resolve, 250);
      this.flushed = () => { clearTimeout(timeout); resolve(); };
      this.node!.port.postMessage("flush");
    });
  }
  async close() {
    this.closed = true; this.flushed?.(); this.source?.disconnect(); this.node?.disconnect();
    this.node?.port.close(); this.stream?.getTracks().forEach((t) => t.stop());
    if (this.context.state !== "closed") await this.context.close();
  }
}
export class PcmPlayback {
  private end = 0; private nodes = new Set<AudioBufferSourceNode>();
  constructor(private context: AudioContext) {}
  append(audio: { data: string; sampleRate: number; numChannels: number; samplesPerChannel?: number }) {
    const { sampleRate: rate, numChannels: channels } = audio;
    if (!Number.isInteger(channels) || channels < 1 || channels > 8 || !Number.isInteger(rate) || rate < 8000 || rate > 192000)
      throw new Error("实时语音返回了无效的音频格式");
    const raw = atob(audio.data); const bytes = Uint8Array.from(raw, (c) => c.charCodeAt(0));
    if (bytes.length % (channels * 2)) throw new Error("实时音频数据不完整");
    const frames = bytes.length / (channels * 2); if (!frames) return;
    if (audio.samplesPerChannel != null && audio.samplesPerChannel !== frames) throw new Error("实时音频长度与元数据不匹配");
    if (this.end - this.context.currentTime > 15) throw new Error("语音播放积压，请结束并重新连接");
    const buffer = this.context.createBuffer(channels, frames, rate); const view = new DataView(bytes.buffer);
    for (let c = 0; c < channels; c++) {
      const target = buffer.getChannelData(c);
      for (let i = 0; i < frames; i++) target[i] = view.getInt16((i * channels + c) * 2, true) / 32768;
    }
    const node = this.context.createBufferSource(); node.buffer = buffer; node.connect(this.context.destination);
    this.nodes.add(node); node.onended = () => { this.nodes.delete(node); node.disconnect(); };
    const start = Math.max(this.context.currentTime + 0.02, this.end); this.end = start + buffer.duration; node.start(start);
  }
  clear() { this.nodes.forEach((node) => { node.stop(); node.disconnect(); }); this.nodes.clear(); this.end = 0; }
}
