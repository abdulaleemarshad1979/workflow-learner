const BASE = 'http://127.0.0.1:8765';
// Tells the app which browser this companion runs in; replays go to the browser you chose.
const BRANDS = (navigator.userAgentData?.brands || []).map(b => b.brand).join(' ');
const BROWSER = navigator.brave || /Brave/.test(BRANDS) ? 'brave' : /Google Chrome/.test(BRANDS) ? 'chrome' : 'chromium';
let state = {recording: false};
let busy = false;
let eventQueue = Promise.resolve();

async function autoPair() {
  // The local app recognises this companion by its fixed extension ID.
  const response = await fetch(BASE + '/api/adapter/pair', {method: 'POST', signal: AbortSignal.timeout(5000)});
  if (!response.ok) throw new Error('Automatic pairing refused (' + response.status + ')');
  const {token} = await response.json();
  await chrome.storage.local.set({token});
  return token;
}

async function api(path, body, retry = true) {
  let {token} = await chrome.storage.local.get('token');
  if (!token) token = await autoPair();
  const response = await fetch(BASE + '/api/adapter/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {'Content-Type': 'application/json', 'X-WL-Token': token, 'X-WL-Browser': BROWSER},
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(5000)
  });
  if (response.status === 401 && retry) {
    await chrome.storage.local.remove('token');
    return api(path, body, false);
  }
  if (!response.ok) throw new Error('Local service rejected request (' + response.status + ')');
  return response.json();
}

function allowed(url) {
  try {
    const u = new URL(url);
    return ['http:', 'https:'].includes(u.protocol) &&
      !(u.hostname === '127.0.0.1' && u.port === '8765' && u.pathname !== '/practice') &&
      !(u.hostname === 'localhost' && u.port === '8765' && u.pathname !== '/practice') &&
      !/accounts\.google|\/login|\/signin|\/oauth|\/authorize/i.test(url) &&
      (!state.origins?.length || state.origins.includes(u.origin));
  } catch { return false; }
}

async function capture(step, sender) {
  if (!state.recording || state.running || !sender.tab || !allowed(sender.tab.url)) return;
  if (sender.frameId && !allowed(sender.url)) return;
  const event = {
    session_id: state.session_id, event_id: crypto.randomUUID(),
    step: {...step, url: sender.tab.url, tab: String(sender.tab.id), frame: sender.frameId ? sender.url : ''}
  };
  // Keep ordering across change/click/navigation events. Never queue while offline.
  eventQueue = eventQueue.catch(() => {}).then(() => api('events', event));
  await eventQueue;
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.type === 'state') {
    respond({...state, recording: Boolean(state.recording && allowed(sender.tab?.url || ''))});
    return;
  }
  if (message.type === 'event') {
    capture(message.step, sender).then(() => respond({ok: true})).catch(() => respond({ok: false}));
    return true;
  }
  if (message.type === 'pair' && sender.url === chrome.runtime.getURL('popup.html')) {
    chrome.storage.local.set({token: message.token}).then(tick).then(() => respond({ok: true}));
    return true;
  }
  if (message.type === 'refresh') {
    tick().then(() => respond(state));
    return true;
  }
});

chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.url && state.recording && allowed(change.url)) {
    capture({kind: 'navigate', label: 'Open website'}, {tab, frameId: 0}).catch(() => {});
  }
});

async function execute(command) {
  const step = command.step;
  const {playback = {}} = await chrome.storage.session.get('playback');
  if (playback.run !== command.run_id) { playback.run = command.run_id; playback.tabs = {}; }
  let tabId = playback.tabs[step.tab];
  if (step.kind === 'navigate') {
    if (!tabId) {
      const tab = await chrome.tabs.create({url: step.url, active: true});
      tabId = tab.id;
      playback.tabs[step.tab] = tabId;
      await chrome.storage.session.set({playback});
    } else {
      const tab = await chrome.tabs.get(tabId);
      if (tab.url !== step.url) await chrome.tabs.update(tabId, {url: step.url, active: true});
    }
    // Bring the replay window in front of whatever you are looking at.
    const opened = await chrome.tabs.get(tabId);
    await chrome.windows.update(opened.windowId, {focused: true}).catch(() => {});
    // Wait for the navigation to finish, not just the tabs.update acknowledgement.
    for (let i = 0; i < 100; i++) {
      const tab = await chrome.tabs.get(tabId);
      if (tab.status === 'complete') return;
      await new Promise(r => setTimeout(r, 150));
    }
    throw new Error('Page load timed out; inspect the browser before continuing');
  }
  if (!tabId) throw new Error('This tab has no opening navigation step; edit the workflow');
  const tab = await chrome.tabs.get(tabId);
  if (new URL(tab.url).origin !== new URL(step.url).origin) throw new Error('Website changed; no action was performed');
  await chrome.tabs.update(tabId, {active: true});
  await chrome.windows.update(tab.windowId, {focused: true}).catch(() => {});
  let frameId = 0;
  if (step.frame) {
    const frames = await chrome.webNavigation.getAllFrames({tabId});
    const matches = frames.filter(f => f.url === step.frame);
    if (matches.length !== 1) throw new Error('Embedded frame missing or ambiguous');
    frameId = matches[0].frameId;
  }
  let result;
  // Retry delivery only until a receiver exists; never retry an acknowledged action.
  for (let i = 0; i < 20; i++) {
    try { result = await chrome.tabs.sendMessage(tabId, {type: 'execute', command}, {frameId}); break; }
    catch (error) {
      if (!String(error).includes('Receiving end does not exist')) throw new Error('Action acknowledgement lost; inspect the page');
      await new Promise(r => setTimeout(r, 250));
    }
  }
  if (!result?.ok) throw new Error(result?.error || 'Page not supported; reload it after installing the extension');
}

async function tick() {
  if (busy) return;
  busy = true;
  try {
    state = await api('state');
    // Unpacked companion: reload from disk when the app ships a newer version.
    if (state.companion_version && state.companion_version !== chrome.runtime.getManifest().version && !state.running && !state.recording) {
      const {reloadedFor} = await chrome.storage.local.get('reloadedFor');
      if (reloadedFor !== state.companion_version) {  // once per version: no reload loop
        await chrome.storage.local.set({reloadedFor: state.companion_version});
        chrome.runtime.reload();
        return;
      }
    }
    await chrome.action.setBadgeText({text: state.recording ? 'REC' : state.running ? 'RUN' : ''});
    await chrome.action.setBadgeBackgroundColor({color: state.recording ? '#be384a' : '#166b58'});
    const {command} = await api('poll', {});
    if (command) {
      let result;
      try { await execute(command); result = {command_id: command.id, ok: true}; }
      catch (error) { result = {command_id: command.id, ok: false, error: String(error.message).slice(0, 500)}; }
      // Result delivery may fail. Backend times out instead of repeating the action.
      await api('result', result);
    }
  } catch {
    state = {recording: false, connected: false};
    await chrome.action.setBadgeText({text: 'OFF'});
  } finally { busy = false; }
}

chrome.alarms.create('local-bridge', {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener(tick);
// Connect as soon as the browser opens (the app may have launched it for a replay).
chrome.runtime.onStartup.addListener(tick);
setInterval(tick, 1000);
tick();
