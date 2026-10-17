// Runs in the audio rendering thread. The AudioContext resamples the device to
// the native realtime input rate before this processor receives the samples.
class CodexAudioCapture extends AudioWorkletProcessor {
  constructor() {
    super(); this.samples = new Float32Array(2400); this.offset = 0;
    this.port.onmessage = () => { this.flush(); this.port.postMessage({ stopped: true }); };
  }
  flush() {
    if (!this.offset) return;
    const samples = this.samples.slice(0, this.offset);
    this.port.postMessage({ samples }, [samples.buffer]); this.offset = 0;
  }
  process(inputs) {
    const channels = inputs[0];
    if (channels?.length) for (let i = 0; i < channels[0].length; i++) {
      this.samples[this.offset++] = channels.reduce((sum, channel) => sum + channel[i], 0) / channels.length;
      if (this.offset === this.samples.length) this.flush();
    }
    return true;
  }
}
registerProcessor("codex-audio-capture", CodexAudioCapture);
