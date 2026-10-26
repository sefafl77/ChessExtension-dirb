#!/usr/bin/env python3
"""Chess API server - Stockfish analysis for browser extension.
Project creator: sefafl.
"""

import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import chess
import chess.engine

STOCKFISH_PATH = "/usr/games/stockfish"
MODE_BUDGETS = {"fast": 1, "balanced": 3, "strong": 10, "maximum": 30}


def desktop_controlled():
    return os.environ.get('CHESS_HELPER_DESKTOP_CONTROL') == '1'


def read_native_mode():
    """Atomic-replace settings are read afresh, never written by the API."""
    path = Path(os.environ.get('CHESS_HELPER_SETTINGS_FILE',
                               str(Path.home() / '.config/kali-rice/chess-helper-widget.json')))
    try:
        settings = json.loads(path.read_text())
        mode = settings.get('mode', 'fast') if isinstance(settings, dict) else 'fast'
        return mode if isinstance(mode, str) and mode in MODE_BUDGETS else 'fast'
    except (OSError, ValueError):
        return 'fast'


def make_limit(mode="fast", depth=None):
    """Modes are time-limited; optional legacy depth is bounded as before."""
    if not isinstance(mode, str) or mode not in MODE_BUDGETS:
        raise ValueError("mode must be fast, balanced, strong, or maximum")
    return chess.engine.Limit(
        time=MODE_BUDGETS[mode],
        depth=max(1, min(int(depth), 18)) if depth is not None else None,
    )


class EngineService:
    """One engine and one serialized search stream; retain hash between requests."""

    def __init__(self):
        self.engine = None
        self.lock = threading.Lock()
        self.closed = False
        self._observation_lock = threading.Lock()
        self._last_move = None

    def observations(self):
        with self._observation_lock:
            return {'busy': self.lock.locked(),
                    'last_move': dict(self._last_move) if self._last_move else None}

    def analyze(self, fen, mode="fast", depth=None):
        limit = make_limit(mode, depth)
        response = {"move": "No legal move", "mode": mode,
                    "budget_seconds": MODE_BUDGETS[mode], "depth": 0, "nodes": 0}
        try:
            if not isinstance(fen, str):
                raise ValueError("expected a FEN string")
            board = chess.Board(fen)
            if not board.is_valid():
                raise ValueError("invalid board position")
        except ValueError as exc:
            response["move"] = f"Invalid FEN: {exc}"
            return response
        if board.is_game_over():
            return response
        with self.lock:
            if self.closed:
                response["move"] = "Engine error: engine service is shutting down"
                return response
            try:
                if self.engine is None:
                    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
                    try:
                        engine.configure({"Skill Level": 20, "UCI_LimitStrength": False,
                                          "Threads": 6, "Hash": 512})
                    except Exception:
                        engine.close()
                        raise
                    self.engine = engine
                # python-chess manages MultiPV for play(), resetting it to
                # Stockfish's default of 1. Do not configure managed options.
                # game=None remains stable, avoiding ucinewgame/hash clearing.
                if self.closed:
                    response['move'] = 'Engine error: engine service is shutting down'
                    return response
                result = self.engine.play(board, limit, info=chess.engine.INFO_BASIC)
                response.update(move=result.move.uci() if result.move else "No legal move",
                                depth=result.info.get("depth", 0),
                                nodes=result.info.get("nodes", 0))
                with self._observation_lock:
                    self._last_move = dict(response)
            except Exception as exc:
                response["move"] = f"Engine error: {exc}"
                if self.engine is not None:
                    self.engine.close()
                    self.engine = None
        return response

    def cancel_search(self):
        """Ask UCI to stop from its event loop, never from the signal thread."""
        engine = self.engine
        if engine is not None:
            try:
                engine.protocol.loop.call_soon_threadsafe(engine.protocol.send_line, 'stop')
            except RuntimeError:
                pass  # The event loop may already be closed during shutdown.

    def close(self, cancel_active=False):
        # Refuse queued searches before waiting. Only explicit server shutdown
        # cancels: ordinary analysis requests retain their full timed budget.
        self.closed = True
        while not self.lock.acquire(timeout=0.05):
            if cancel_active:
                # Also covers SIGTERM arriving during engine startup/before go.
                self.cancel_search()
        try:
            if self.engine is not None:
                try:
                    self.engine.quit()
                except chess.engine.EngineError:
                    pass
                finally:
                    self.engine.close()
                    self.engine = None
        finally:
            self.lock.release()


service = EngineService()


def get_best_move(fen, depth=None, mode="fast"):
    """Keep the original Python helper's string result and positional depth."""
    return service.analyze(fen, mode, depth)["move"]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/health", "/settings"):
            payload = {'app': 'chess-helper', 'pid': os.getpid()}
            if parsed.path == '/health':
                payload['status'] = 'ok'
            else:
                controlled = desktop_controlled()
                payload.update(desktop_control=controlled, enabled=True,
                               mode=read_native_mode() if controlled else 'fast')
                if controlled:
                    payload.update(self.server.engine_service.observations())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path != "/analyze":
            self.send_response(404)
            self.end_headers()
            return
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if not 0 < content_length <= 65536:
                raise ValueError("request body must be 1..65536 bytes")
            data = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(data, dict) or not data.get("fen"):
                raise ValueError("fen is required in a JSON object")
            fen = data["fen"]
            mode = data.get("mode", "fast")
            # Explicit modes always use their full timed budget, never a
            # legacy depth cap accidentally left in an extension request.
            depth = data.get("depth") if "mode" not in data else None
            if desktop_controlled():
                mode, depth = read_native_mode(), None
            make_limit(mode, depth)
        except (ValueError, TypeError, OverflowError) as exc:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(exc)}).encode())
            return

        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            # MV3 fetch must receive a response start before its 30s timer.
            # JSON permits leading whitespace; flush before waiting for the
            # engine lock as well as before starting a long search.
            self.wfile.write(b"\n")
            self.wfile.flush()
            result = self.server.engine_service.analyze(fen, mode, depth)
            self.wfile.write(json.dumps(result).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def create_server(port=8765, engine_service=None):
    server = ThreadingHTTPServer(("localhost", port), Handler)
    server.engine_service = engine_service if engine_service is not None else service
    return server


def run_server(port=8765):
    server = create_server(port)

    def stop(signum, frame):
        server.engine_service.closed = True
        server.engine_service.cancel_search()
        raise KeyboardInterrupt

    previous_sigterm = signal.signal(signal.SIGTERM, stop)
    print(f"[API] Chess API server running on http://localhost:{server.server_port}", flush=True)
    print("[API] Endpoints: GET /health, POST /analyze")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.engine_service.close(cancel_active=True)
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    run_server(int(os.environ.get('CHESS_HELPER_PORT', '8765')))
