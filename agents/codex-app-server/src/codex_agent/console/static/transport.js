'use strict';
(function (root) {
  class SSEParser {
    constructor() { this.buffer = ''; this.frame = {event: 'message', data: [], id: null}; }
    feed(text) {
      this.buffer += text;
      const frames = [];
      while (true) {
        const match = /[\r\n]/.exec(this.buffer);
        if (!match || (this.buffer[match.index] === '\r' && match.index === this.buffer.length - 1)) break;
        const line = this.buffer.slice(0, match.index);
        const width = this.buffer.slice(match.index, match.index + 2) === '\r\n' ? 2 : 1;
        this.buffer = this.buffer.slice(match.index + width);
        if (!line) {
          if (this.frame.data.length) frames.push({...this.frame, data: this.frame.data.join('\n')});
          this.frame = {event: 'message', data: [], id: null};
        } else if (!line.startsWith(':')) {
          const colon = line.indexOf(':');
          const key = colon < 0 ? line : line.slice(0, colon);
          let value = colon < 0 ? '' : line.slice(colon + 1);
          if (value.startsWith(' ')) value = value.slice(1);
          if (key === 'data') this.frame.data.push(value);
          if (key === 'event') this.frame.event = value;
          if (key === 'id' && !value.includes('\0')) this.frame.id = value;
        }
      }
      return frames;
    }
  }
  async function consumeSSE(body, onFrame) {
    const reader = body.getReader(), decoder = new TextDecoder(), parser = new SSEParser();
    try {
      while (true) {
        const {value, done} = await reader.read();
        const text = done ? decoder.decode() : decoder.decode(value, {stream: true});
        for (const frame of parser.feed(text)) await onFrame(frame);
        if (done) break;
      }
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  }
  function compareCursor(a, b) {
    const parse = value => {
      if (!/^[\w-]+:(0|[1-9][0-9]*)$/.test(value)) throw new Error('INVALID_CURSOR');
      const [epoch, seq] = value.split(':'); return [epoch, BigInt(seq)];
    };
    const [ea, sa] = parse(a), [eb, sb] = parse(b);
    if (ea !== eb) throw new Error('EPOCH_MISMATCH');
    return sa === sb ? 0 : sa > sb ? 1 : -1;
  }
  const api = {SSEParser, consumeSSE, compareCursor};
  if (typeof module !== 'undefined') module.exports = api;
  else root.ConsoleTransport = api;
})(globalThis);
