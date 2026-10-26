"""Native controller tests; every listener uses an isolated ephemeral port.
Maintained by dirbusterSync.
"""
import importlib.util
import json
import socket
import os
import time
import urllib.request
from pathlib import Path


def free_url():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return f'http://127.0.0.1:{listener.getsockname()[1]}'

import pytest

CORE_PATH = Path(__file__).resolve().parents[1] / 'widget' / 'chess_widget_core.py'


def load_core():
    assert CORE_PATH.exists(), 'native backend controller has not been implemented'
    spec = importlib.util.spec_from_file_location('chess_widget_core', CORE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_state_is_idle_and_atomic_merge_preserves_unknown_keys(tmp_path):
    core = load_core()
    config = tmp_path / 'config' / 'widget.json'
    controller = core.WidgetController(config_path=config, base_url=free_url())
    assert controller.load_state()['mode'] == 'fast'
    assert not config.exists(), 'reading state must not write settings or start the API'
    state = controller.save_state({'mode': 'balanced', 'position': {'x': 2, 'y': 9}, 'future': [1, 2]})
    assert state['future'] == [1, 2]
    controller.save_state({'pinned': True})
    controller.set_mode('strong')
    saved = json.loads(config.read_text())
    assert saved == {'mode': 'strong', 'position': {'x': 2, 'y': 9}, 'future': [1, 2], 'pinned': True}
    snapshot = controller.snapshot()
    assert snapshot['mode'] == 'strong'
    assert snapshot['running'] is False and snapshot['owned'] is False
    assert snapshot['pid'] is None and snapshot['error'] is None
    assert {'running', 'mode', 'status', 'owned', 'pid', 'error'} <= snapshot.keys()


@pytest.mark.parametrize('mode', ['turbo', None, [], 10])
def test_invalid_modes_never_replace_persisted_settings(tmp_path, mode):
    controller = load_core().WidgetController(config_path=tmp_path / 'settings.json', base_url=free_url())
    controller.set_mode('maximum')
    before = controller.config_path.read_bytes()
    with pytest.raises(ValueError, match='mode'):
        controller.set_mode(mode)
    with pytest.raises(ValueError, match='mode'):
        controller.save_state({'mode': mode})
    assert controller.config_path.read_bytes() == before


def test_corrupt_config_is_reported_and_not_overwritten(tmp_path):
    config = tmp_path / 'settings.json'
    config.write_text('{broken')
    controller = load_core().WidgetController(config_path=config, base_url=free_url())
    with pytest.raises(ValueError):
        controller.save_state({'pinned': True})
    assert config.read_text() == '{broken'
    assert controller.snapshot()['error']


@pytest.fixture
def controller(tmp_path):
    instance = load_core().WidgetController(config_path=tmp_path / 'settings.json',
                                          base_url=free_url())
    instance.log_path = tmp_path / 'server.log'
    yield instance
    if instance.process is not None and instance.process.poll() is None:
        instance.process.terminate()
        try:
            instance.process.wait(timeout=7)
        except Exception:
            os.killpg(instance.process.pid, 9)
            instance.process.wait(timeout=3)


def read_json(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.load(response)


def child_pids(pid):
    return {int(child) for task in Path(f'/proc/{pid}/task').iterdir()
            for child in (task / 'children').read_text().split()}


def test_real_start_stop_is_owned_idle_idempotent_and_restarts(controller):
    assert callable(getattr(controller, 'start', None)), 'Start lifecycle is not implemented'
    assert controller.process is None
    assert not controller.snapshot()['running']
    controller.set_mode('balanced')
    started = controller.start()
    try:
        assert started['running'] is True, started
        assert started['owned'] is True
        assert started['mode'] == 'balanced'
        pid = started['pid']
        assert pid == controller.process.pid
        assert os.getsid(pid) == pid, 'server must be detached from widget session'
        assert not child_pids(pid), 'Start must not start Stockfish until an analysis arrives'
        assert read_json(controller.base_url + '/health')['pid'] == pid
        settings = read_json(controller.base_url + '/settings')
        assert settings['desktop_control'] is True
        assert settings['mode'] == 'balanced'
        assert controller.start()['pid'] == pid, 'duplicate Start must not spawn another server'
        changed = controller.set_mode('strong')
        assert changed['running'] is True and changed['owned'] is True
        assert changed['pid'] == pid and changed['mode'] == 'strong'
        assert read_json(controller.base_url + '/settings')['mode'] == 'strong'
        assert controller.snapshot()['mode'] == 'strong'
    finally:
        stopped = controller.stop()
    assert not stopped['running'] and not stopped['owned']
    assert stopped['pid'] is None and stopped['error'] is None
    assert not Path(f'/proc/{pid}').exists()
    assert not controller.stop()['running']
    restarted = controller.start()
    assert restarted['running'] and restarted['pid'] != pid
    assert controller.stop()['error'] is None


def test_stop_during_real_maximum_search_is_prompt_and_reaps_stockfish(controller):
    import http.client
    controller.set_mode('maximum')
    started = controller.start()
    assert started['running'], started
    pid = started['pid']
    connection = http.client.HTTPConnection('127.0.0.1', controller._port, timeout=8)
    connection.request('POST', '/analyze', json.dumps({
        'fen': 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        'mode': 'fast'}), {'Content-Type': 'application/json'})
    response = connection.getresponse()
    assert response.read(1) == b'\n'
    engine_pids = set()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            engine_pids = child_pids(pid)
            if engine_pids:
                break
            time.sleep(0.02)
        assert engine_pids, 'analysis must launch a real persistent Stockfish child'
        time.sleep(0.4)  # allow UCI startup to finish and the 30s search to begin
        before = time.monotonic()
        stopped = controller.stop()
        elapsed = time.monotonic() - before
        assert elapsed < 3, f'Stop waited {elapsed:.2f}s for an active 30-second search'
        assert not stopped['running'] and not stopped['error'], stopped
        assert all(not Path(f'/proc/{child}').exists() for child in engine_pids), 'Stockfish child leaked'
        result = json.loads(response.read())
        assert result['mode'] == 'maximum' and result['budget_seconds'] == 30
        assert len(result['move']) in (4, 5)
    finally:
        connection.close()
        controller.stop()
        for child in engine_pids:
            if Path(f'/proc/{child}').exists():
                os.kill(child, 15)


def test_snapshot_reports_busy_and_last_move_without_canceling_analysis(controller):
    import http.client
    controller.set_mode('fast')
    assert controller.start()['running']
    connection = http.client.HTTPConnection('127.0.0.1', controller._port, timeout=5)
    before = time.monotonic()
    connection.request('POST', '/analyze', json.dumps({
        'fen': 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'}),
        {'Content-Type': 'application/json'})
    response = connection.getresponse()
    assert response.read(1) == b'\n'
    try:
        active = controller.snapshot()
        assert active.get('busy') is True, active
        assert active['status'] == 'Thinking'
        result = json.loads(response.read())
        assert time.monotonic() - before >= 0.8, 'polling must not interrupt normal searches'
        idle = controller.snapshot()
        assert idle['busy'] is False
        assert idle['last_move'] == result
        assert len(result['move']) in (4, 5)
    finally:
        connection.close()
        controller.stop()


def test_unowned_chess_server_is_never_adopted_or_stopped(controller, tmp_path):
    started = controller.start()
    assert started['running']
    observer = load_core().WidgetController(config_path=tmp_path / 'other.json',
                                          base_url=controller.base_url)
    observed = observer.snapshot()
    assert observed['running'] and not observed['owned']
    assert observed['pid'] == started['pid']
    rejected = observer.start()
    assert not rejected['owned'] and 'already in use' in rejected['error'].lower()
    rejected = observer.stop()
    assert 'not owned' in rejected['error'].lower()
    assert read_json(controller.base_url + '/health')['pid'] == started['pid']
    assert controller.process.poll() is None
    assert controller.stop()['error'] is None


def test_unrelated_listener_is_not_spawned_over_or_stopped(controller):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class OtherHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"app":"not-chess","pid":123,"status":"ok"}')
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', controller._port), OtherHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rejected = controller.start()
        assert not rejected['running'] and not rejected['owned']
        assert rejected['error'] and controller.process is None
        rejected = controller.stop()
        assert 'not owned' in rejected['error'].lower()
        assert read_json(controller.base_url + '/health')['app'] == 'not-chess'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_failed_child_start_exposes_error_and_later_start_clears_it(controller):
    path = controller.api_path
    controller.api_path = '/nonexistent/chess-test-api.py'
    failed = controller.start()
    assert not failed['running'] and not failed['owned']
    assert 'exited' in failed['error'].lower()
    assert controller.process is None
    assert controller.log_path.exists()
    controller.api_path = path
    recovered = controller.start()
    assert recovered['running'] and recovered['error'] is None
    assert controller.stop()['error'] is None


def test_concurrent_native_merges_and_starts_preserve_state_without_duplicates(controller):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: controller.save_state({f'future_{index}': index}), range(12)))
        snapshots = list(pool.map(lambda _: controller.start(), range(4)))
    assert len({state['pid'] for state in snapshots}) == 1
    assert all(state['owned'] and state['running'] for state in snapshots)
    assert all(controller.load_state()[f'future_{index}'] == index for index in range(12))
    assert controller.stop()['error'] is None
