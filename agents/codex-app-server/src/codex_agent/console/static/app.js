'use strict';
const $ = id => document.getElementById(id);
let token = '', session = null, stream = null, after = 0, pendingMessage = null;
const steps = new Map();
async function api(path, body, key) {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST',
    headers: {'Authorization': `Bearer ${token}`, ...(body === undefined ? {} : {'Content-Type':'application/json','Idempotency-Key':key})},
    ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const result = await response.json();
  if (!response.ok) throw new Error(`${response.status}: ${result.error || '请求失败'}`);
  return result;
}
function error(e) { $('error').textContent = e.message; }
async function refresh() {
  const sessions = await api('/api/sessions');
  $('sessions').replaceChildren();
  for (const entry of sessions) {
    const li = document.createElement('li'), button = document.createElement('button');
    button.textContent = `${entry.id.slice(0,24)} · ${entry.status}`;
    button.onclick = () => select(entry.id).catch(error);
    li.append(button); $('sessions').append(li);
  }
}
function filterSteps() {
  let visible = 0;
  for (const li of steps.values()) {
    li.hidden = $('filter').value !== 'all' && !li.dataset.categories.split(' ').includes($('filter').value);
    if (!li.hidden) visible++;
  }
  $('count').textContent = `${visible} / ${steps.size}`;
}
$('filter').onchange = filterSteps;
function show(event) {
  after = Math.max(after, event.seq);
  const key = event.public.call_id || event.item_id || `event-${event.seq}`;
  let li = steps.get(key);
  if (!li) { li = document.createElement('li'); steps.set(key, li); $('events').append(li); }
  li.dataset.categories = [event.kind.startsWith('tool_') ? 'tool' : 'summary', event.public.item_type === 'agentMessage' ? 'reply' : '', event.public.ok === false || event.kind === 'observation_gap' ? 'error' : ''].join(' ');
  li.textContent = `${event.seq} · ${event.kind} · ${Object.entries(event.public).filter(([,v])=>v!=null).map(([k,v])=>`${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(' · ')}`;
  if (steps.size > 1000) { const oldest = steps.keys().next().value; steps.get(oldest).remove(); steps.delete(oldest); }
  filterSteps();
}
async function subscribe(id, controller) {
  while (!controller.signal.aborted) {
    try {
      const response = await fetch(`/api/sessions/${encodeURIComponent(id)}/events`, {
        headers:{Authorization:`Bearer ${token}`, 'Last-Event-ID':String(after)}, signal:controller.signal});
      if (!response.ok) throw new Error(`事件订阅失败：${response.status}`);
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream:true});
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0,boundary); buffer = buffer.slice(boundary+2);
          const data = frame.split('\n').find(line=>line.startsWith('data: '));
          if (data) show(JSON.parse(data.slice(6)));
        }
      }
    } catch(e) { if (controller.signal.aborted) return; error(e); }
    await new Promise(resolve=>setTimeout(resolve, 1500));
  }
}
async function select(id) {
  if (stream) stream.abort();
  session = id; after = 0; steps.clear(); $('events').replaceChildren();
  $('session-title').textContent = id;
  const loop = await api(`/api/sessions/${encodeURIComponent(id)}/loop`);
  loop.steps.forEach(show); after = loop.after;
  stream = new AbortController(); subscribe(id, stream);
  for (const name of ['send','interrupt','close','resume']) $(name).disabled = false;
}
$('connect').onsubmit = async e => { e.preventDefault(); token = $('token').value; $('token').value = '';
  try { await api('/health/ready'); $('health').textContent = '已连接'; $('new').disabled = false; await refresh(); } catch(e) { error(e); } };
$('new').onclick = async () => { try { const entry = await api('/api/sessions', {}, crypto.randomUUID()); await refresh(); await select(entry.id); } catch(e) { error(e); } };
$('message').onsubmit = async e => { e.preventDefault(); if (!session) return; $('send').disabled = true;
  const text = $('text').value;
  if (!pendingMessage || pendingMessage.text !== text || pendingMessage.session !== session) pendingMessage = {text,session,key:crypto.randomUUID()};
  try { await api(`/api/sessions/${encodeURIComponent(session)}/messages`, {text,request_id:pendingMessage.key}, pendingMessage.key); pendingMessage=null; $('text').value=''; $('error').textContent=''; await refresh(); }
  catch(e) { error(e); } finally { $('send').disabled=false; } };
for (const action of ['interrupt','close','resume']) $(action).onclick = async () => {
  try { await api(`/api/sessions/${encodeURIComponent(session)}/${action}`, {}, crypto.randomUUID()); await refresh(); } catch(e) { error(e); } };
window.addEventListener('pagehide',()=>stream?.abort());
