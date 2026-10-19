'use strict';
const assert=require('node:assert/strict');
const {chromium}=require('../frontend/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE,args:['--no-sandbox','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
 try {
  const context=await browser.newContext({permissions:['microphone']}), page=await context.newPage(), calls=[], errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().endsWith('/api/native/call'))calls.push(r.postDataJSON());});
  await page.addInitScript(()=>{
   localStorage.setItem('codex-agent.voice',JSON.stringify({transport:'webrtc'}));
   window.rtcPeers=[];window.rtcTracks=[];
   const capture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
   navigator.mediaDevices.getUserMedia=async (...args)=>{const stream=await capture(...args);window.rtcTracks.push(...stream.getTracks());return stream;};
   window.RTCPeerConnection=class {
    iceGatheringState='complete';connectionState='new';
    constructor(){window.rtcPeers.push(this);}
    addTrack(){} createDataChannel(){return {};}
    async createOffer(){return {type:'offer',sdp:'fixture-offer-sdp'};}
    async setLocalDescription(value){this.localDescription=value;}
    async setRemoteDescription(value){this.remoteDescription=value;}
    close(){this.connectionState='closed';}
   };
  });
  await page.goto(process.env.CONSOLE_TEST_URL);
  await page.locator('#text').fill('voice-session-fixture');await page.locator('#send').click();await page.getByText('你好，完整回复。',{exact:true}).waitFor();
  await page.locator('#text').fill('/voice');await page.locator('#text').press('Enter');
  await page.getByLabel('包含 realtimeStartInstructions',{exact:true}).check();await page.getByLabel('realtimeStartInstructions',{exact:true}).fill('Browser RTC test');
  await page.getByRole('button',{name:'保存会话语音上下文',exact:true}).click();await page.getByRole('button',{name:'返回聊天',exact:true}).click();
  await page.getByRole('button',{name:'开始实时语音对话',exact:true}).click();
  const region=page.getByRole('region',{name:'实时语音',exact:true});await region.getByText('实时语音已连接',{exact:true}).waitFor();
  await page.waitForFunction(()=>window.rtcPeers[0]?.remoteDescription?.sdp==='fixture-answer-sdp');
  const start=calls.find(c=>c.method==='thread/realtime/start');assert.equal(start.params.transport.type,'webrtc');assert.equal(start.params.transport.sdp,'fixture-offer-sdp');assert.equal(start.params.realtimeStartInstructions,'Browser RTC test');
  await region.getByRole('button',{name:'静音麦克风',exact:true}).click();assert.equal(await page.evaluate(()=>window.rtcTracks.every(t=>!t.enabled)),true);
  await region.getByRole('button',{name:'结束语音',exact:true}).click();
  await page.waitForFunction(()=>window.rtcTracks.every(t=>t.readyState==='ended')&&window.rtcPeers.every(p=>p.connectionState==='closed'));
  assert.ok(calls.some(c=>c.method==='thread/realtime/stop'));assert.ok(!calls.some(c=>c.method==='thread/realtime/appendAudio'));
  assert.deepEqual(errors,[]);console.log('WebRTC browser: SDP offer/answer, advanced context, mute, native stop and device cleanup passed (synthetic peer).');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
