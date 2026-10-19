import { api, consoleStore as store, requestKey } from "./store";
import { native, type Obj } from "./native";
import { audioBase64, INPUT_RATE, MicrophoneCapture, pcm16, PcmPlayback } from "./audio-media";
import { NativeMutation } from "./native-mutation";
import { voicePreferences } from "./voice-settings";

/** The client owns device capture; Codex owns the realtime conversation. */
export class RealtimeSession {
  private capture = new MicrophoneCapture();
  private playback = new PcmPlayback(this.capture.context);
  private controller = new AbortController();
  private active = false; private closed = false; private starting = true;
  private queue: Uint8Array[] = []; private draining?: Promise<void>;
  private startMutation = new NativeMutation(); private stopMutation = new NativeMutation();
  private textMutation = new NativeMutation();
  private startRequest?: Obj;
  private rtc?: RTCPeerConnection;
  private rtcMedia?: MediaStream;
  private rtcAudio?: HTMLAudioElement;
  threadId = "";
  constructor(private event: (method: string, params: Obj) => void, private failed: (error: unknown) => void) {}
  async start(sessionId: string | null) {
    try {
      const preferences = voicePreferences(store.state.sessions.find((s) => s.id === sessionId)?.thread_id);
      let transport: Obj = { type: "websocket" };
      if (preferences.transport === "webrtc") transport = await this.startWebRTC(preferences.inputDeviceId, preferences.outputDeviceId);
      else if (!await this.capture.start((samples) => {
        if (!this.active || this.closed) return;
        this.queue.push(pcm16(samples));
        if (this.queue.length > 30) { this.failed(new Error("语音上传过慢，已停止麦克风，请结束并重新连接")); void this.capture.close(); return; }
        this.drain();
      }, preferences.inputDeviceId)) return;
      if (preferences.transport === "websocket" && preferences.outputDeviceId) {
        const context = this.capture.context as AudioContext & { setSinkId?: (id: string) => Promise<void> };
        if (!context.setSinkId) throw new Error("当前浏览器不支持选择播放设备");
        await context.setSinkId(preferences.outputDeviceId);
      }
      if (this.closed) return;
      const id = sessionId || await store.create(false);
      if (!id) throw new Error("无法创建语音聊天");
      this.threadId = store.state.sessions.find((s) => s.id === id)?.thread_id || "";
      if (!this.threadId) throw new Error("聊天未连接到运行时");
      if (!sessionId && !this.closed) await store.select(id);
      if (this.closed) return;
      const baseline = await api<Obj>("/api/events/history?limit=1");
      const connected = this.listen(baseline.watermark);
      await connected;
      if (this.closed) return;
      this.startRequest = { ...preferences.advanced, threadId: this.threadId, realtimeSessionId: requestKey(), outputModality: preferences.outputModality,
        transport, ...(preferences.version ? { version: preferences.version } : {}),
        ...(preferences.voice ? { voice: preferences.voice } : {}), ...(preferences.model ? { model: preferences.model } : {}) };
      await this.startMutation.run("thread/realtime/start", this.startRequest);
      if (!this.closed) { this.active = true; this.event("connected", {}); }
    } catch (error) { if (!this.closed) { this.failed(error); await this.release(); } }
    finally { this.starting = false; if (this.closed && this.startRequest) await this.stopNative().catch(this.failed); }
  }
  private async startWebRTC(input: string, output: string): Promise<Obj> {
    if (typeof RTCPeerConnection === "undefined") throw new Error("当前浏览器不支持 WebRTC");
    const rtc = this.rtc = new RTCPeerConnection();
    this.rtcMedia = await navigator.mediaDevices.getUserMedia({ audio: input ? { deviceId: { exact: input } } : true });
    if (this.closed) { this.rtcMedia.getTracks().forEach((t) => t.stop()); throw new Error("语音已关闭"); }
    this.rtcMedia.getTracks().forEach((track) => rtc.addTrack(track, this.rtcMedia!));
    const audio = this.rtcAudio = new Audio(); audio.autoplay = true;
    if (output) {
      if (!("setSinkId" in audio)) throw new Error("当前浏览器不支持选择播放设备");
      await (audio as HTMLAudioElement & { setSinkId: (id: string) => Promise<void> }).setSinkId(output);
    }
    rtc.ontrack = ({ streams, track }) => { audio.srcObject = streams[0] || new MediaStream([track]); void audio.play().catch(this.failed); };
    rtc.onconnectionstatechange = () => { if (rtc.connectionState === "failed" && !this.closed) { this.failed(new Error("WebRTC 连接失败")); void this.stop().catch(this.failed); } };
    // The native backend owns realtime control on its sideband connection.
    // This channel is negotiated as required by the realtime media endpoint.
    const channel = rtc.createDataChannel("oai-events");
    channel.onmessage = ({ data }) => { if (!this.closed) { try { this.event("webrtc/event", JSON.parse(data)); } catch { /* Non-JSON media metadata. */ } } };
    await rtc.setLocalDescription(await rtc.createOffer());
    if (rtc.iceGatheringState !== "complete") await new Promise<void>((resolve, reject) => {
      const done = (error?: Error) => { clearTimeout(timer); rtc.removeEventListener("icegatheringstatechange", changed); this.controller.signal.removeEventListener("abort", cancelled); error ? reject(error) : resolve(); };
      const changed = () => { if (rtc.iceGatheringState === "complete") done(); };
      const cancelled = () => done(new Error("语音已关闭"));
      const timer = setTimeout(() => done(new Error("WebRTC 网络协商超时")), 10000);
      rtc.addEventListener("icegatheringstatechange", changed); this.controller.signal.addEventListener("abort", cancelled, { once: true });
      if (this.closed) cancelled();
    });
    return { type: "webrtc", sdp: rtc.localDescription!.sdp };
  }
  private async listen(cursor: string) {
    const response = await fetch(`/api/events/stream?after=${encodeURIComponent(cursor)}`, { signal: this.controller.signal });
    if (!response.ok) throw new Error(`无法订阅语音事件（${response.status}）`);
    void ConsoleTransport.consumeSSE(response.body!, (frame) => {
      if (this.closed) return;
      if (frame.event === "snapshot_required") throw new Error("语音事件连接已失效，请重新开始");
      if (frame.event !== "observation") return;
      const notification = JSON.parse(frame.data);
      const method = notification.method || ""; const params = notification.payload?.params;
      if (!method.startsWith("thread/realtime/") || params?.threadId !== this.threadId) return;
      if (method === "thread/realtime/sdp" && this.rtc) void this.rtc.setRemoteDescription({ type: "answer", sdp: params.sdp }).catch((error) => { this.failed(error); void this.stop().catch(this.failed); });
      else if (method === "thread/realtime/outputAudio/delta" && !this.rtc) this.playback.append(params.audio);
      else if (method === "thread/realtime/error") this.failed(new Error(params.message));
      else if (method === "thread/realtime/closed") {
        this.active = false; this.startRequest = undefined;
        void this.release();
      }
      this.event(method, params);
    }).then(() => {
      if (!this.closed) throw new Error("语音事件连接已断开，请结束并重新连接");
    }).catch((error) => {
      if (!this.closed) { this.failed(error); void this.stop().catch(this.failed); }
    });
  }
  private drain() {
    if (this.draining) return;
    this.draining = (async () => {
      while (this.queue.length && !this.closed) {
        const bytes = this.queue.shift()!;
        await native("thread/realtime/appendAudio", { threadId: this.threadId,
          audio: { data: audioBase64(bytes), sampleRate: INPUT_RATE, numChannels: 1, samplesPerChannel: bytes.length / 2 } });
      }
    })().catch((error) => { this.queue = []; this.active = false; this.failed(error); void this.capture.close(); })
      .finally(() => { this.draining = undefined; });
  }
  mute(value: boolean) { this.capture.mute(value); this.rtcMedia?.getAudioTracks().forEach((t) => { t.enabled = !value; }); }
  async text(text: string, speech = false) {
    if (!this.active || this.closed) throw new Error("语音尚未连接");
    await this.textMutation.run(speech ? "thread/realtime/appendSpeech" : "thread/realtime/appendText", { threadId: this.threadId, text });
  }
  private async stopNative() { await this.stopMutation.run("thread/realtime/stop", { threadId: this.threadId }); this.startRequest = undefined; }
  private async release() {
    this.closed = true; this.active = false; this.queue = []; this.controller.abort(); this.playback.clear(); await this.capture.close();
    this.rtc?.close(); this.rtcMedia?.getTracks().forEach((t) => t.stop());
    if (this.rtcAudio) { this.rtcAudio.pause(); this.rtcAudio.srcObject = null; }
  }
  async stop() {
    await this.release();
    // Start may still be in flight. Its completion performs the native stop.
    if (!this.starting && this.startRequest) await this.stopNative();
  }
}
