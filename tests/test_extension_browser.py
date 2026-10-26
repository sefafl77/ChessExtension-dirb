"""Real headless Chrome regressions; page fixture, not Chess.com itself.
Browser fixture tests by sefafl.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import urllib.request
import websocket

EXT = Path(__file__).resolve().parents[1] / 'chess_extension'
FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='chess-browser-', dir=os.environ.get('TMPDIR') or None)
        chrome = os.environ.get('CHROME_BIN', 'google-chrome')
        cls.proc = subprocess.Popen([chrome, '--headless=new', '--disable-gpu',
            '--no-first-run', '--no-default-browser-check', '--remote-allow-origins=*',
            '--remote-debugging-port=0', '--user-data-dir='+cls.tmp.name, 'about:blank'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        portfile = Path(cls.tmp.name)/'DevToolsActivePort'
        for _ in range(100):
            if portfile.exists(): break
            time.sleep(.1)
        port = portfile.read_text().splitlines()[0]
        targets=json.load(urllib.request.urlopen('http://127.0.0.1:'+port+'/json'))
        cls.ws=websocket.create_connection(next(t['webSocketDebuggerUrl'] for t in targets if t['type']=='page'),timeout=10)
        cls.seq=0

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.tmp.cleanup()

    @classmethod
    def call(cls, method, params=None):
        cls.seq+=1
        cls.ws.send(json.dumps({'id':cls.seq,'method':method,'params':params or {}}))
        while True:
            r=json.loads(cls.ws.recv())
            if r.get('id')==cls.seq:
                if 'error' in r: raise RuntimeError(r['error'])
                return r.get('result',{})

    @classmethod
    def js(cls, expression):
        r=cls.call('Runtime.evaluate', {'expression':expression,'returnByValue':True,'awaitPromise':True})
        if r.get('exceptionDetails'): raise RuntimeError(r['exceptionDetails'])
        return r['result'].get('value')

    def setUp(self):
        self.call('Page.enable')
        # Navigate to a local fixture origin; no production cookies or game state.
        path=Path(self.tmp.name)/'board.html'
        path.write_text('<html><head></head><body><wc-chess-board id="board-play-computer" class="board" style="display:block;width:560px;height:560px;position:relative;background:repeating-conic-gradient(#759356 0% 25%,#eeeed2 0% 50%) 0 0/140px 140px"></wc-chess-board></body></html>')
        self.call('Page.navigate',{'url':path.as_uri()})
        for _ in range(50):
            if self.js('document.readyState')=='complete': break
            time.sleep(.05)
        self.js('window.requests=[]; window.saved={}; window.intervals=[]; window.setInterval=(f)=>{intervals.push(f);return intervals.length};')
        self.js('window.helperSettings={enabled:true,desktop_control:false,mode:"fast"};window.serverOnline=true;window.chrome={runtime:{lastError:null,sendMessage:(m,cb)=>{requests.push(m);const r=m.type==="chess-helper:analyze"?{ok:true,move:"e2e4"}:m.type==="chess-helper:settings"?(serverOnline?{ok:true,settings:helperSettings}:{ok:false,error:"offline"}):{ok:true};if(cb) cb(r);return Promise.resolve(r)}},storage:{local:{get:async()=>window.saved,set:async(o)=>Object.assign(window.saved,o)}}};')
        self.js(EXT.joinpath('content.js').read_text())
        self.js('new Promise(r=>setTimeout(r,30))')
        self.js('const style=document.createElement("style");style.textContent='+json.dumps(EXT.joinpath('overlay.css').read_text())+';document.head.appendChild(style);')

    def position(self, fen=FEN):
        self.js('window.dispatchEvent(new CustomEvent("chess-helper:page-data",{detail:JSON.stringify({type:"fen",fen:'+json.dumps(fen)+',source:"test fixture"})}));')
        self.js('new Promise(r=>setTimeout(r,50))')

    def test_modes_visible_and_selected_mode_sent_to_worker(self):
        self.assertEqual(self.js('document.querySelectorAll("#chess-helper-mode option").length'),4)
        self.position()
        self.assertEqual(self.js('requests.filter(x=>x.type==="chess-helper:analyze").at(-1).mode'),'fast')
        self.js('const s=document.querySelector("#chess-helper-mode");s.value="maximum";s.dispatchEvent(new Event("change"));')
        self.js('new Promise(r=>setTimeout(r,50))')
        self.assertEqual(self.js('requests.filter(x=>x.type==="chess-helper:analyze").at(-1).mode'),'maximum')
        self.assertEqual(self.js('saved.chessHelperMode'),'maximum')

    def test_gold_arrow_noninteractive_and_orientation_preserved(self):
        self.position()
        self.assertTrue(self.js('!!document.querySelector(".chess-helper-overlay")'))
        self.assertEqual(self.js('document.querySelectorAll(".chess-helper-overlay linearGradient stop").length'),3)
        self.assertTrue(self.js('!!document.querySelector(".chess-helper-arrow-outline")'))
        self.assertEqual(self.js('getComputedStyle(document.querySelector(".chess-helper-overlay")).pointerEvents'),'none')
        self.assertEqual(self.js('document.querySelector(".chess-helper-overlay line").getAttribute("x1")'),'4.5')
        self.assertEqual(self.js('document.querySelector(".chess-helper-overlay line").getAttribute("y1")'),'6.5')
        self.js('document.querySelector(".board").classList.add("flipped")')
        self.position(FEN.replace(' 0 1',' 1 2'))
        self.assertEqual(self.js('document.querySelector(".chess-helper-overlay line").getAttribute("x1")'),'3.5')
        self.assertEqual(self.js('document.querySelector(".chess-helper-overlay line").getAttribute("y1")'),'1.5')

    def test_mode_switch_during_search_rejects_old_result_and_analyzes_same_position(self):
        self.js('window.pending=[]; chrome.runtime.sendMessage=m=>{requests.push(m);return new Promise(resolve=>pending.push(resolve))};')
        self.position()
        self.js('const s=document.querySelector("#chess-helper-mode");s.value="maximum";s.dispatchEvent(new Event("change"));')
        self.js('pending.shift()({ok:true,move:"e2e4"});')
        self.js('new Promise(r=>setTimeout(r,30))')
        self.assertFalse(self.js('!!document.querySelector(".chess-helper-overlay")'))
        self.assertEqual(self.js('requests.filter(x=>x.type==="chess-helper:analyze").at(-1).mode'),'maximum')
        self.js('pending.shift()({ok:true,move:"g1f3"});')
        self.js('new Promise(r=>setTimeout(r,30))')
        self.assertEqual(self.js('document.querySelector(".chess-helper-overlay line").getAttribute("x1")'),'6.5')
        self.assertIn('g1 → f3',self.js('document.querySelector(".chess-helper-status").textContent'))

    def test_mode_panel_fits_narrow_viewport_and_does_not_block_board_click(self):
        self.call('Emulation.setDeviceMetricsOverride',{'width':950,'height':800,'deviceScaleFactor':1,'mobile':False})
        self.position()
        self.assertTrue(self.js('(()=>{const r=document.querySelector("#chess-helper-controls").getBoundingClientRect();return r.x>=0&&r.right<=innerWidth&&r.bottom<=innerHeight})()'))
        self.assertEqual(self.js('document.elementFromPoint(315,455).tagName'),'WC-CHESS-BOARD')
        self.call('Emulation.clearDeviceMetricsOverride')

    def test_desktop_widget_mode_controls_hide_browser_panel_and_stop_start_reanalyzes(self):
        self.position()
        self.js('helperSettings={enabled:true,desktop_control:true,mode:"strong"};')
        sync='intervals.find(f=>f.toString().includes("chess-helper:settings"))()'
        self.assertTrue(self.js('intervals.some(f=>f.toString().includes("chess-helper:settings"))'))
        self.js(sync)
        self.js('new Promise(r=>setTimeout(r,40))')
        self.assertTrue(self.js('document.querySelector("#chess-helper-controls").hidden'))
        self.assertTrue(self.js('saved.chessHelperDesktopControl'))
        self.assertEqual(self.js('requests.filter(x=>x.type==="chess-helper:analyze").at(-1).mode'),'strong')
        self.js('serverOnline=false;')
        self.js(sync)
        self.js('new Promise(r=>setTimeout(r,40))')
        self.assertFalse(self.js('!!document.querySelector(".chess-helper-overlay")'))
        before=self.js('requests.filter(x=>x.type==="chess-helper:analyze").length')
        self.js('serverOnline=true;helperSettings.mode="fast";')
        self.js(sync)
        self.js('new Promise(r=>setTimeout(r,40))')
        self.assertEqual(self.js('requests.filter(x=>x.type==="chess-helper:analyze").length'),before+1)
        self.assertTrue(self.js('!!document.querySelector(".chess-helper-overlay")'))

    def test_page_bridge_reads_web_component_game_fen_and_avoids_out_of_scope_fallback(self):
        bridge=EXT.joinpath('page_bridge.js').read_text()
        self.js('document.querySelector("wc-chess-board").game={getFEN(){return '+json.dumps(FEN)+'}};')
        self.js('window.bridgeEvents=[];window.addEventListener("chess-helper:page-data",e=>bridgeEvents.push(JSON.parse(e.detail)));')
        self.js(bridge)
        self.js('new Promise(r=>setTimeout(r,40))')
        request=self.js('requests.find(x=>x.type==="chess-helper:analyze")')
        self.assertEqual(request['fen'],FEN)
        self.assertEqual(request['mode'],'fast')
        self.js('window.bridgePoll=intervals.at(-1);')
        self.js('document.querySelector("wc-chess-board").game={};bridgePoll();')
        self.assertEqual(self.js('requests.filter(x=>x.type==="chess-helper:analyze").length'),1)
        self.assertEqual(self.js('bridgeEvents.at(-1).message'), 'No FEN getter found in page context yet')

if __name__=='__main__': unittest.main()
