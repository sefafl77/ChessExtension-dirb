"""Backend regression tests; isolated servers never bind production port 8765.
Test suite maintained by dirbusterSync.
"""
import contextlib
import http.client
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import chess
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chess_api as api


@pytest.fixture
def service():
    instance = api.EngineService()
    yield instance
    instance.close()


def test_real_fast_analysis_is_legal_timed_and_reuses_strong_engine(service):
    started = time.monotonic()
    result = service.analyze(chess.STARTING_FEN)
    elapsed = time.monotonic() - started
    assert chess.Move.from_uci(result["move"]) in chess.Board().legal_moves
    assert result["mode"] == "fast"
    assert result["budget_seconds"] == 1
    assert result["depth"] > 0
    assert result["nodes"] > 0
    assert 0.8 <= elapsed < 8
    engine = service.engine
    assert engine.protocol.config["Skill Level"] == 20
    assert engine.protocol.config["UCI_LimitStrength"] is False
    assert engine.protocol.config["Threads"] == 6
    assert engine.protocol.config["Hash"] == 512
    assert engine.protocol.config["MultiPV"] == 1
    sent = []
    original_send = engine.protocol.send_line
    def record_command(line):
        sent.append(line)
        return original_send(line)
    engine.protocol.send_line = record_command
    service.analyze(chess.STARTING_FEN)
    assert "ucinewgame" not in sent, "persistent hash must not be cleared between requests"
    assert "go movetime 1000" in sent
    assert service.engine is engine
    assert engine.returncode.done() is False
    service.close()
    assert engine.returncode.result(timeout=3) == 0
    service.close()  # shutdown is idempotent


def test_real_fast_analysis_finds_mate_in_one(service):
    board = chess.Board("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    assert board.is_valid()
    result = service.analyze(board.fen())
    board.push_uci(result["move"])
    assert board.is_checkmate()


@pytest.mark.parametrize("fen", ["not a fen", "8/8/8/8/8/8/8/8 w - - 0 1", None, 123])
def test_bad_fen_keeps_move_error_compatibility_without_starting_engine(service, fen):
    result = service.analyze(fen)
    assert result["move"].startswith("Invalid FEN")
    assert result["depth"] == result["nodes"] == 0
    assert service.engine is None


@pytest.mark.parametrize("fen", [
    "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1",  # checkmate
    "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1",  # stalemate
    "7k/8/6K1/8/8/8/8/8 w - - 0 1",  # insufficient material
])
def test_game_over_needs_no_engine(service, fen):
    assert service.analyze(fen)["move"] == "No legal move"
    assert service.engine is None

@pytest.mark.parametrize("body,headers", [
    ("{", {"Content-Length": "1"}),
    (b"\xff", {"Content-Length": "1"}),
    ("", {"Content-Length": "-1"}),
    ("", {"Content-Length": "bad"}),
    ("", {"Content-Length": "65537"}),
])
def test_http_malformed_body_is_rejected_without_search(service, body, headers):
    with running_server(service) as port:
        connection = http.client.HTTPConnection("localhost", port, timeout=2)
        connection.request("POST", "/analyze", body, headers)
        response = connection.getresponse()
        assert response.status == 400
        assert "error" in json.loads(response.read())
        connection.close()
        assert service.engine is None


def test_existing_routes_and_cors_are_preserved(service):
    with running_server(service) as port:
        for method, path, expected in [("OPTIONS", "/analyze", 200), ("GET", "/missing", 404),
                                       ("POST", "/missing", 404)]:
            connection = http.client.HTTPConnection("localhost", port, timeout=2)
            connection.request(method, path)
            response = connection.getresponse()
            assert response.status == expected
            if method == "OPTIONS":
                assert response.getheader("Access-Control-Allow-Origin") == "*"
                assert "POST" in response.getheader("Access-Control-Allow-Methods")
            response.read()
            connection.close()


def test_engine_start_failure_returns_compatible_error_and_can_retry(service, monkeypatch):
    path = api.STOCKFISH_PATH
    monkeypatch.setattr(api, "STOCKFISH_PATH", "/nonexistent/chess-test-stockfish")
    result = service.analyze(chess.STARTING_FEN)
    assert result["move"].startswith("Engine error:")
    assert service.engine is None
    monkeypatch.setattr(api, "STOCKFISH_PATH", path)
    result = service.analyze(chess.STARTING_FEN)
    assert chess.Move.from_uci(result["move"]) in chess.Board().legal_moves


def test_shutdown_rejects_queued_search_without_killing_engine_before_quit(service):
    service.analyze(chess.STARTING_FEN)
    engine = service.engine
    service.closed = True  # shutdown announced, close() has not acquired the lock yet
    result = service.analyze(chess.STARTING_FEN)
    assert result["move"].startswith("Engine error:")
    assert service.engine is engine, "only close() should quit an engine during shutdown"
    assert not engine.returncode.done()
    service.close()
    assert engine.returncode.result(timeout=3) == 0


def test_close_marks_shutdown_before_waiting_for_active_search(service):
    service.lock.acquire()
    closer = threading.Thread(target=service.close)
    closer.start()
    try:
        deadline = time.monotonic() + 0.5
        while not service.closed and time.monotonic() < deadline:
            time.sleep(0.005)
        assert service.closed, "queued searches must not prolong engine shutdown"
    finally:
        service.lock.release()
        closer.join(timeout=3)
    result = service.analyze(chess.STARTING_FEN)
    assert result["move"].startswith("Engine error:")
    assert service.engine is None


def test_queued_http_request_starts_response_before_engine_lock_is_available(service):
    with running_server(service) as port:
        started = time.monotonic()
        first_connection, first = post(port, {"fen": chess.STARTING_FEN})
        assert first.read(1) == b"\n"
        deadline = time.monotonic() + 1
        while not service.lock.locked() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert service.lock.locked()
        queued_at = time.monotonic()
        second_connection, second = post(port, {"fen": chess.STARTING_FEN})
        assert second.read(1) == b"\n"
        assert time.monotonic() - queued_at < 0.5
        for response in (first, second):
            result = json.loads(response.read())
            assert chess.Move.from_uci(result["move"]) in chess.Board().legal_moves
        assert 1.7 <= time.monotonic() - started < 8
        first_connection.close()
        second_connection.close()


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize("during_search", [False, True])
def test_server_signal_shutdown_reaps_persistent_engine(stop_signal, during_search):
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", "import chess_api; chess_api.run_server(0)"],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    children = []
    try:
        assert select.select([process.stdout], [], [], 5)[0], "server did not announce its port"
        banner = process.stdout.readline()
        port = int(banner.strip().rsplit(":", 1)[-1])
        assert port != 0
        connection, response = post(port, {"fen": chess.STARTING_FEN})
        assert response.status == 200
        assert "move" in json.loads(response.read())
        connection.close()
        children = sorted({int(pid)
                           for task in Path(f"/proc/{process.pid}/task").iterdir()
                           for pid in (task / "children").read_text().split()})
        assert children, "persistent Stockfish child should be alive"
        if during_search:
            connection, response = post(port, {"fen": chess.STARTING_FEN, "mode": "balanced"})
            assert response.read(1) == b"\n"
            time.sleep(0.1)
        process.send_signal(stop_signal)
        _, stderr = process.communicate(timeout=8)
        if during_search:
            assert chess.Move.from_uci(json.loads(response.read())["move"]) in chess.Board().legal_moves
            connection.close()
        assert process.returncode == 0, stderr
        assert all(not Path(f"/proc/{pid}").exists() for pid in children), "Stockfish child leaked"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
        for pid in children:
            if Path(f"/proc/{pid}").exists():
                os.kill(pid, signal.SIGTERM)


@contextlib.contextmanager
def running_server(engine_service):
    server = api.create_server(port=0, engine_service=engine_service)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def post(port, payload, timeout=5):
    connection = http.client.HTTPConnection("localhost", port, timeout=timeout)
    connection.request("POST", "/analyze", json.dumps(payload), {"Content-Type": "application/json"})
    return connection, connection.getresponse()


@pytest.mark.parametrize("mode", ["fast", pytest.param("maximum", marks=pytest.mark.skipif(
    os.environ.get("RUN_MAXIMUM") != "1", reason="set RUN_MAXIMUM=1 for real 30-second search"))])
def test_http_starts_body_immediately_and_health_stays_available_during_real_search(service, mode):
    with running_server(service) as port:
        started = time.monotonic()
        connection, response = post(port, {"fen": chess.STARTING_FEN, "mode": mode}, timeout=2)
        assert response.status == 200
        assert response.getheader("Access-Control-Allow-Origin") == "*"
        assert response.read(1) == b"\n"
        assert time.monotonic() - started < 1
        deadline = time.monotonic() + 1
        while not service.lock.locked() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert service.lock.locked(), "search should still be running"
        health = http.client.HTTPConnection("localhost", port, timeout=1)
        health_started = time.monotonic()
        health.request("GET", "/health")
        health_response = health.getresponse()
        assert health_response.status == 200
        health_payload = json.loads(health_response.read())
        assert health_payload['status'] == 'ok'
        assert health_payload['app'] == 'chess-helper'
        assert health_payload['pid'] == os.getpid()
        assert time.monotonic() - health_started < 0.8
        health.close()
        response.fp.raw._sock.settimeout(40)
        result = json.loads(response.read())
        elapsed = time.monotonic() - started
        assert chess.Move.from_uci(result["move"]) in chess.Board().legal_moves
        assert result["budget_seconds"] == api.MODE_BUDGETS[mode]
        assert result["mode"] == mode
        assert result["depth"] > 0 and result["nodes"] > 0
        assert api.MODE_BUDGETS[mode] * 0.8 <= elapsed < api.MODE_BUDGETS[mode] + 8
        print(f"\nreal HTTP {mode}: {elapsed:.3f}s, {result}")
        connection.close()


@pytest.mark.parametrize("mode,seconds", [("fast", 1), ("balanced", 3), ("strong", 10), ("maximum", 30)])
def test_http_modes_map_to_budget_and_ignore_legacy_depth(service, mode, seconds):
    with running_server(service) as port:
        connection, response = post(port, {
            "fen": "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", "mode": mode, "depth": "ignored"})
        assert response.status == 200
        result = json.loads(response.read())
        assert result["mode"] == mode
        assert result["budget_seconds"] == seconds
        assert result["move"] == "No legal move"
        connection.close()


@pytest.mark.parametrize("payload", [[], None, {"mode": "fast"}, {"fen": chess.STARTING_FEN, "mode": "turbo"},
                                     {"fen": chess.STARTING_FEN, "depth": "bad"}])
def test_http_rejects_bad_request_without_starting_engine(service, payload):
    with running_server(service) as port:
        connection, response = post(port, payload)
        assert response.status == 400
        assert "error" in json.loads(response.read())
        assert service.engine is None
        connection.close()


@pytest.mark.parametrize("mode,seconds", [("fast", 1), ("balanced", 3), ("strong", 10), ("maximum", 30)])
def test_mode_has_timed_budget_without_depth_cap(mode, seconds):
    limit = api.make_limit(mode)
    assert limit.time == seconds
    assert limit.depth is None


def test_default_is_fast_and_legacy_depth_is_bounded():
    assert api.make_limit().time == 1
    assert api.make_limit(depth=999).depth == 18
    assert api.make_limit(depth=-1).depth == 1
    assert api.make_limit(depth=999).time == 1


@pytest.mark.parametrize("mode", ["turbo", None, 123, [], {}])
def test_unknown_mode_is_rejected(mode):
    with pytest.raises(ValueError, match="mode"):
        api.make_limit(mode)


def get_json(port, path):
    connection = http.client.HTTPConnection('localhost', port, timeout=2)
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader('Access-Control-Allow-Origin') == '*'
        return json.loads(response.read())
    finally:
        connection.close()


def test_settings_standalone_and_health_identify_api_without_creating_config(service, monkeypatch, tmp_path):
    monkeypatch.delenv('CHESS_HELPER_DESKTOP_CONTROL', raising=False)
    config = tmp_path / 'unused.json'
    monkeypatch.setenv('CHESS_HELPER_SETTINGS_FILE', str(config))
    with running_server(service) as port:
        health = get_json(port, '/health')
        assert health == {'status': 'ok', 'app': 'chess-helper', 'pid': os.getpid()}
        settings = get_json(port, '/settings')
        assert settings == {'desktop_control': False, 'enabled': True, 'mode': 'fast',
                            'app': 'chess-helper', 'pid': os.getpid()}
    assert not config.exists()


def test_desktop_settings_reread_native_mode_and_override_incoming_analysis(service, monkeypatch, tmp_path):
    config = tmp_path / 'widget.json'
    monkeypatch.setenv('CHESS_HELPER_DESKTOP_CONTROL', '1')
    monkeypatch.setenv('CHESS_HELPER_SETTINGS_FILE', str(config))
    game_over = '7k/6Q1/6K1/8/8/8/8/8 b - - 0 1'
    with running_server(service) as port:
        for mode in ('maximum', 'balanced'):
            pending = config.with_suffix('.pending')
            pending.write_text(json.dumps({'mode': mode, 'future': 'keep'}))
            pending.replace(config)
            settings = get_json(port, '/settings')
            assert settings['desktop_control'] is True
            assert settings['enabled'] is True
            assert settings['mode'] == mode
            connection, response = post(port, {'fen': game_over, 'mode': 'turbo', 'depth': 'bad'})
            assert response.status == 200
            result = json.loads(response.read())
            connection.close()
            assert result['mode'] == mode
            assert result['budget_seconds'] == api.MODE_BUDGETS[mode]
            assert result['move'] == 'No legal move'
            assert json.loads(config.read_text()) == {'mode': mode, 'future': 'keep'}


@pytest.mark.parametrize('content', [None, '{broken', '[]', '{"mode": []}', '{"mode": "turbo"}'])
def test_desktop_missing_or_invalid_settings_fall_back_without_writing(service, monkeypatch, tmp_path, content):
    config = tmp_path / 'widget.json'
    if content is not None:
        config.write_text(content)
    monkeypatch.setenv('CHESS_HELPER_DESKTOP_CONTROL', '1')
    monkeypatch.setenv('CHESS_HELPER_SETTINGS_FILE', str(config))
    with running_server(service) as port:
        assert get_json(port, '/settings')['mode'] == 'fast'
    assert config.read_text() == content if content is not None else not config.exists()
