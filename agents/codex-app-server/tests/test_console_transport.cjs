'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { SSEParser, consumeSSE, compareCursor } = require('../src/codex_agent/console/static/transport.js');

test('UTF-8 split at every byte, multiline data, CRLF, comments and consecutive frames', async () => {
  const body = ': keepalive\r\nevent: observation\r\nid: epoch:9007199254740993\r\ndata: {"text":\r\ndata: "你好"}\r\n\r\nevent: cursor\ndata: {"cursor":"epoch:9007199254740994"}\n\n';
  const bytes = new TextEncoder().encode(body), frames = [];
  const stream = new ReadableStream({start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close(); }});
  await consumeSSE(stream, async frame => frames.push(frame));
  assert.equal(frames.length, 2);
  assert.equal(frames[0].event, 'observation');
  assert.equal(JSON.parse(frames[0].data).text, '你好');
  assert.equal(frames[0].id, 'epoch:9007199254740993');
  assert.equal(compareCursor(frames[0].id, 'epoch:9007199254740992'), 1);
});

test('cursor updates only after a handler completes; incomplete final frame is not dispatched', async () => {
  const parser = new SSEParser();
  assert.deepEqual(parser.feed('id: e:1\ndata: {'), []);
  assert.equal(parser.feed('}\n\n')[0].id, 'e:1');
  assert.deepEqual(parser.feed('data: unfinished'), []);
  assert.throws(() => compareCursor('old:1', 'new:2'));
});
