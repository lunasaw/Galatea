'use strict';
const $ = id => document.getElementById(id);
const {consumeSSE, compareCursor} = ConsoleTransport;
let session = null, stream = null, epoch = null, globalEvents = false, generation = 0;
let snapshot = null, viewPages = {}, browsing = {}, nativeRows = [], nativePage = null, nativeLive = true;
let pendingMessage = null, refreshTimer = null;
const pendingActions = new Map();
function key() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return 'web-' + Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}
async function api(path, body, idempotencyKey, signal) {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', signal,
    headers: body === undefined ? {} : {'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const result = await response.json();
  if (!response.ok) { const e = new Error(`${response.status}: ${result.error || '请求失败'}`); e.code = result.error; throw e; }
  return result;
}
function error(e) { if (e.name !== 'AbortError') $('error').textContent = e.message; }
function node(tag, text, className) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (className) e.className = className; return e; }
function resetNotice() { $('reset').hidden = false; $('reset').textContent = '服务已重启，上一进程的事件历史已清空。持久化会话仍可核对并恢复。'; }
function tab(name) {
  for (const pane of ['chat', 'loop', 'native']) $(pane).hidden = pane !== name;
  document.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  $('message').hidden = name === 'native' && globalEvents;
}
document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => tab(b.dataset.tab));
async function refreshSessions() {
  const entries = await api('/api/sessions');
  $('sessions').replaceChildren(...entries.map(entry => {
    const li = node('li'), button = node('button', `${entry.id.slice(0, 22)} · ${entry.status}`, entry.id === session ? 'selected' : '');
    button.title = entry.id; button.onclick = () => select(entry.id).catch(error); li.append(button); return li;
  }));
}
async function status() {
  const result = await api('/api/console/status');
  if (epoch && epoch !== result.epoch) resetNotice();
  epoch = result.epoch;
  $('health').textContent = `${result.host_ready ? 'Host 已就绪' : 'Host 待恢复'} · 观察 ${result.observation.status}`;
  $('diagnostics').textContent = JSON.stringify(result, null, 2);
  return result;
}
function showMessages(rows) {
  $('messages').replaceChildren(...rows.map(message => {
    const div = node('article', undefined, 'message ' + message.role);
    div.append(node('div', message.role === 'user' ? '你' : 'GALATEA', 'role'), node('div', message.text || '', 'body'));
    return div;
  }));
  if (!rows.length) $('messages').append(node('p', '本次启动暂无历史事件', 'empty'));
}
function detail(data, label='展开详情') {
  const d = node('details'); d.append(node('summary', label), node('pre', JSON.stringify(data, null, 2))); return d;
}
function showSteps(rows) {
  $('steps').replaceChildren(...rows.map(step => {
    const div = node('article', undefined, 'step'), heading = node('div', undefined, 'step-heading');
    heading.append(node('strong', step.tool || step.type || '步骤'), node('span', step.status || (step.success === false ? 'failed' : ''), 'status'));
    div.append(heading);
    if (step.type === 'request') div.append(node('p', '当前版本不提供页面回复；实际回执请查看原生事件。', 'meta'));
    if (step.type === 'tool') div.append(node('p', `最近观察：${step.observed_at || '未知'} · call ${step.call_id || '未知'}`, 'meta'));
    if (step.job) div.append(node('p', `作业 ${step.job.job_id || ''} · ${step.job.status || '未知'}（最近一次工具回执）`));
    if (step.text) div.append(node('p', step.text));
    div.append(detail(step)); return div;
  }));
  if (!rows.some(s => s.type === 'tool')) $('steps').prepend(node('p', '当前页没有观察到训练工具调用。', 'meta'));
}
function showSnapshot(data) {
  snapshot = data;
  if (!browsing.messages) showMessages(data.messages || []);
  if (!browsing.steps) showSteps(data.steps || []);
  $('turns').replaceChildren(...(data.turns || []).map(t => node('div', `Turn ${t.id} · ${t.status || 'unknown'}${t.usage ? ' · 用量 ' + JSON.stringify(t.usage) : ''}`, 'turn')));
  $('history-note').textContent = data.coverage.status === 'complete' ? '本次启动的已观察历史' : `观察不完整：${data.coverage.reason}`;
}
async function loadSnapshot(id, version) {
  const data = await api(`/api/sessions/${encodeURIComponent(id)}/snapshot`);
  if (version === generation) showSnapshot(data);
  return data;
}
function scheduleSnapshot() {
  if (refreshTimer || !session || globalEvents) return;
  const id = session, version = generation;
  refreshTimer = setTimeout(async () => {
    refreshTimer = null;
    try { await loadSnapshot(id, version); await refreshSessions(); } catch(e) { error(e); }
  }, 80);
}
function filterParams() {
  const q = new URLSearchParams({limit: '200'});
  for (const name of ['method', 'kind', 'direction']) if ($(name).value) q.set(name, $(name).value);
  if (globalEvents) q.set('unbound', 'true');
  return q;
}
function renderNative() {
  $('native-events').replaceChildren(...nativeRows.map(row => {
    const d = node('details', undefined, 'event');
    d.append(node('summary', `${row.seq} · ${row.direction || 'Host'} · ${row.kind} · ${row.method || row.kind}${row.redactions.length ? ' · 已脱敏' : ''}`));
    d.ontoggle = async () => { if (!d.open || d.querySelector('pre')) return;
      try { const full = await api('/api/events/' + encodeURIComponent(row.event_id)); d.append(node('pre', JSON.stringify(full, null, 2))); } catch(e) { error(e); }
    }; return d;
  }));
  if (!nativeRows.length) $('native-events').append(node('p', '当前筛选没有事件。', 'empty'));
}
function matches(row) {
  return (!globalEvents || !row.session_id) && ['method', 'kind', 'direction'].every(k => !$(k).value || row[k] === $(k).value);
}
async function loadHistory(next=false) {
  const version = generation, q = filterParams();
  if (next && nativePage) { q.set('after', nativePage.next_cursor); q.set('until', nativePage.watermark); }
  const path = globalEvents ? '/api/events/history' : `/api/sessions/${encodeURIComponent(session)}/history`;
  const data = await api(path + '?' + q);
  if (version !== generation) return data;
  nativePage = data; nativeRows = data.events; renderNative(); $('next-events').disabled = !data.has_more;
  return data;
}
async function subscribe(id, controller, initial, version, global) {
  let cursor = initial, delay = 300;
  while (!controller.signal.aborted && version === generation) {
    try {
      const path = global ? '/api/events/stream' : `/api/sessions/${encodeURIComponent(id)}/events`;
      const response = await fetch(path + '?after=' + encodeURIComponent(cursor), {signal: controller.signal});
      if (!response.ok) { const body = await response.json(); const e = new Error(body.error); e.code = body.error; throw e; }
      await consumeSSE(response.body, async frame => {
        if (controller.signal.aborted || version !== generation) return;
        const data = JSON.parse(frame.data);
        if (frame.event === 'snapshot_required') { const e = new Error('snapshot_required'); e.code = 'SNAPSHOT_REQUIRED'; throw e; }
        if (frame.event === 'observation') {
          if (compareCursor(data.event_id, cursor) <= 0) return;
          if (nativeLive && matches(data)) { nativeRows.push(data); if (nativeRows.length > 200) nativeRows.shift(); renderNative(); }
          scheduleSnapshot(); cursor = data.event_id;
        } else if (frame.event === 'cursor') {
          if (compareCursor(data.cursor, cursor) >= 0) cursor = data.cursor;
        } else if (frame.event === 'status') {
          $('health').textContent = '观察 ' + data.status;
        }
      });
      delay = 300;
    } catch(e) {
      if (controller.signal.aborted || version !== generation) return;
      if (['EPOCH_MISMATCH', 'CURSOR_AHEAD', 'SNAPSHOT_REQUIRED'].includes(e.code)) {
        if (e.code === 'EPOCH_MISMATCH') resetNotice();
        await select(id, global); return;
      }
      error(e);
    }
    await new Promise(resolve => { const timer = setTimeout(resolve, delay); controller.signal.addEventListener('abort', () => {clearTimeout(timer); resolve();}, {once: true}); });
    delay = Math.min(delay * 2, 5000);
  }
}
async function select(id, global=false) {
  stream?.abort(); if (refreshTimer) clearTimeout(refreshTimer); refreshTimer = null;
  const version = ++generation; session = id; globalEvents = global; browsing = {}; viewPages = {}; nativeRows = []; nativeLive = true;
  $('session-title').textContent = global ? '运行实例事件' : id;
  $('event-scope').textContent = global ? '当前未归属会话的初始化、配置和协议事件；绑定后可在对应会话查看。' : '当前会话的全部双向协议与 Host 观察事件。';
  for (const name of ['send', 'interrupt', 'close', 'resume', 'older-messages', 'older-steps', 'latest-messages', 'latest-steps']) $(name).disabled = global || !id;
  await status();
  let cursor;
  if (global) { tab('native'); cursor = (await loadHistory()).watermark; }
  else { tab('chat'); const data = await loadSnapshot(id, version); cursor = data.watermark; await loadHistory(); }
  if (version !== generation) return;
  stream = new AbortController(); subscribe(id, stream, cursor, version, global).catch(error); await refreshSessions();
}
for (const view of ['messages', 'steps']) {
  $('older-' + view).onclick = async () => { try {
    const id = session, version = generation;
    const q = new URLSearchParams({limit: '200', view});
    const path = `/api/sessions/${encodeURIComponent(id)}/${view === 'messages' ? 'messages' : 'loop'}?`;
    let page = viewPages[view];
    if (!page) {
      q.set('until', snapshot.watermark);
      const current = await api(path + q);
      page = current.next_page;
      if (!page) { $('older-' + view).disabled = true; return; }
    }
    q.set('page', page);
    const data = await api(path + q);
    if (version !== generation) return;
    browsing[view] = true; viewPages[view] = data.next_page;
    (view === 'messages' ? showMessages : showSteps)(data[view]);
    $('older-' + view).disabled = !data.next_page;
  } catch(e) { error(e); } };
  $('latest-' + view).onclick = async () => { browsing[view] = false; viewPages[view] = null; $('older-' + view).disabled = false; await loadSnapshot(session, generation).catch(error); };
}
$('event-filter').onsubmit = e => { e.preventDefault(); nativeLive = false; loadHistory().catch(error); };
$('next-events').onclick = () => {nativeLive = false; loadHistory(true).catch(error);};
$('live-events').onclick = () => {nativeLive = true; nativeRows = []; renderNative();};
$('global').onclick = () => select(null, true).catch(error);
$('new').onclick = async () => { const name='create'; if (!pendingActions.has(name)) pendingActions.set(name,key()); $('new').disabled=true;
  try { const entry=await api('/api/sessions',{},pendingActions.get(name)); pendingActions.delete(name); await select(entry.id); } catch(e) {error(e);} finally {$('new').disabled=false;} };
$('message').onsubmit = async e => { e.preventDefault(); if (!session) return;
  const id=session, text=$('text').value;
  if (!pendingMessage || pendingMessage.text !== text || pendingMessage.session !== id) pendingMessage={text,session:id,key:key()};
  $('send').disabled=true;
  try {await api(`/api/sessions/${encodeURIComponent(id)}/messages`, {text,request_id:pendingMessage.key}, pendingMessage.key); pendingMessage=null; $('text').value=''; $('error').textContent=''; scheduleSnapshot(); await refreshSessions();}
  catch(e){error(e);} finally {$('send').disabled=false;} };
for (const action of ['interrupt','close','resume']) $(action).onclick=async()=>{
  const id=session, name=`${id}:${action}`; if (!id) return; if (!pendingActions.has(name)) pendingActions.set(name,key()); $(action).disabled=true;
  try{await api(`/api/sessions/${encodeURIComponent(id)}/${action}`,{},pendingActions.get(name));pendingActions.delete(name);await refreshSessions();scheduleSnapshot();}
  catch(e){error(e);}finally{$(action).disabled=false;}
};
window.addEventListener('pagehide',()=>{stream?.abort();clearTimeout(refreshTimer);});
Promise.all([status(),refreshSessions()]).catch(error);
