// Browser API boundary is mocked; the actual worker source is exercised.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
let listener;
let request;
let injection;
const context = {
  console,
  AbortSignal,
  chrome: {
    runtime: { onInstalled: { addListener() {} }, onMessage: { addListener(fn) { listener = fn; } } },
    scripting: { executeScript: async args => { injection = args; return []; } }
  },
  fetch: async (url, init) => { request = { url, payload: init.body ? JSON.parse(init.body) : null }; return { ok: true, json: async () => url.endsWith('/settings') ? { desktop_control: true, enabled: true, mode: 'strong' } : ({ move: 'e2e4' }) }; }
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'chess_extension', 'background.js'), 'utf8'), context);
const sender = { tab: { id: 42, url: 'https://www.chess.com/play/computer' } };
const call = (message, source=sender) => new Promise(resolve => listener(message, source, resolve));
(async () => {
  const injected = await call({ type: 'chess-helper:inject-bridge' });
  assert.equal(injected.ok, true);
  assert.equal(injection.world, 'MAIN');
  assert.equal(injection.target.tabId, 42);
  for (const mode of ['fast', 'balanced', 'strong', 'maximum']) {
    const response = await call({ type: 'chess-helper:analyze', fen: 'position', mode });
    assert.equal(response.move, 'e2e4');
    assert.equal(request.url, 'http://127.0.0.1:8765/analyze');
    assert.equal(request.payload.mode, mode);
    assert.equal(request.payload.fen, 'position');
    assert.equal('depth' in request.payload, false);
  }
  const settings = await call({ type: 'chess-helper:settings' });
  assert.equal(request.url, 'http://127.0.0.1:8765/settings');
  assert.equal(settings.ok, true);
  assert.equal(settings.settings.mode, 'strong');
  assert.equal(settings.settings.desktop_control, true);
  const denied = await call({ type: 'chess-helper:analyze', mode: 'maximum' }, { tab: { url: 'https://not-chess.com/' } });
  assert.equal(denied.ok, false);
  console.log('PASS: MAIN bridge, 4 analysis modes, desktop settings, FEN, origin rejection');
})().catch(error => { console.error(error); process.exitCode = 1; });
