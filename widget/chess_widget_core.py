#!/usr/bin/env python3
"""Synchronous, GTK-free native Chess Helper settings and lifecycle core.

The UI must call these methods from workers. Constructing a controller is inert.
Only the native widget writes settings; the API and extension are readers.
"""
import fcntl
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse

MODES = ('fast', 'balanced', 'strong', 'maximum')
DEFAULT_CONFIG = Path.home() / '.config/kali-rice/chess-helper-widget.json'


class WidgetController:
    def __init__(self, config_path=DEFAULT_CONFIG, api_path=None,
                 base_url='http://127.0.0.1:8765', *, log_path=None, startup_timeout=5):
        self.config_path = Path(config_path).expanduser()
        if api_path is None:
            candidate = Path(__file__).resolve().parent.parent / 'chess_api.py'
            api_path = candidate if candidate.is_file() else Path.home() / 'chess_api.py'
        self.api_path = str(Path(api_path).expanduser())
        self.base_url = base_url.rstrip('/')
        url = urlparse(self.base_url)
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost')
                or url.path or url.query or url.fragment or url.username or url.password):
            raise ValueError('base_url must be a local HTTP origin')
        self._host, self._port = url.hostname, url.port or 80
        self.log_path = Path(log_path).expanduser() if log_path else Path.home() / '.cache/chess-helper/server.log'
        self.startup_timeout = startup_timeout
        self.process = None
        self._lock = threading.RLock()
        self._error = None

    def load_state(self):
        """Read persisted state without writing defaults or starting a process."""
        with self._lock:
            try:
                state = json.loads(self.config_path.read_text())
            except FileNotFoundError:
                state = {}
            if not isinstance(state, dict):
                raise ValueError('settings must contain a JSON object')
            if 'mode' in state and state['mode'] not in MODES:
                raise ValueError('mode must be fast, balanced, strong, or maximum')
            return {'mode': 'fast', **state}

    def save_state(self, patch):
        """Atomically merge a patch, retaining all unknown existing keys."""
        if not isinstance(patch, dict):
            raise ValueError('settings patch must be a dict')
        if 'mode' in patch and patch['mode'] not in MODES:
            raise ValueError('mode must be fast, balanced, strong, or maximum')
        with self._lock:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with self.config_path.with_name(self.config_path.name + '.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                state = {**self.load_state(), **patch}
                name = None
                try:
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                                     dir=self.config_path.parent,
                                                     prefix='.' + self.config_path.name + '.',
                                                     delete=False) as stream:
                        name = stream.name
                        json.dump(state, stream, indent=2)
                        stream.write('\n')
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(name, self.config_path)
                finally:
                    if name and os.path.exists(name):
                        os.unlink(name)
                return state

    def set_mode(self, mode):
        """Persist the native mode and return a complete UI-ready snapshot."""
        with self._lock:
            self.save_state({'mode': mode})
            return self.snapshot()

    def _get(self, path):
        connection = http.client.HTTPConnection(self._host, self._port, timeout=0.3)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            if response.status == 200:
                data = json.loads(response.read(65536))
                return data if isinstance(data, dict) else None
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        return None

    @staticmethod
    def _identity(health):
        return bool(health and health.get('app') == 'chess-helper'
                    and health.get('status') == 'ok' and type(health.get('pid')) is int
                    and health['pid'] > 0)

    def _port_occupied(self):
        try:
            with socket.create_connection((self._host, self._port), timeout=0.2):
                return True
        except OSError:
            return False

    # Ownership/process provenance: dirbusterSync.
    def start(self):
        """Spawn one detached API, accepting readiness only from its exact PID."""
        with self._lock:
            self._error = None
            if self.process is not None and self.process.poll() is None:
                return self.snapshot()
            if self._port_occupied():
                self._error = 'Address already in use; refusing to adopt or replace an existing server'
                return self.snapshot()
            try:
                self.load_state()  # Do not start with a corrupt native configuration.
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                env = {**os.environ, 'CHESS_HELPER_DESKTOP_CONTROL': '1',
                       'CHESS_HELPER_SETTINGS_FILE': str(self.config_path.resolve()),
                       'CHESS_HELPER_PORT': str(self._port), 'PYTHONDONTWRITEBYTECODE': '1'}
                with self.log_path.open('ab') as log:
                    self.process = subprocess.Popen([sys.executable, '-u', self.api_path],
                                                    env=env, stdin=subprocess.DEVNULL,
                                                    stdout=log, stderr=subprocess.STDOUT,
                                                    start_new_session=True)
                deadline = time.monotonic() + self.startup_timeout
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise RuntimeError(f'API exited with code {self.process.returncode}; see {self.log_path}')
                    health = self._get('/health')
                    if self._identity(health):
                        if health['pid'] != self.process.pid:
                            raise RuntimeError('Address already in use by a different API PID')
                        return self.snapshot()
                    time.sleep(0.05)
                raise RuntimeError(f'API startup timed out; see {self.log_path}')
            except (OSError, ValueError, RuntimeError) as exc:
                self._terminate_owned()
                self._error = str(exc)
                return self.snapshot()

    def _terminate_owned(self):
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Only our still-live session leader's group, never a listener PID.
                if process.poll() is None and os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
        self.process = None

    def stop(self):
        """Stop only the subprocess owned by this controller instance."""
        with self._lock:
            if self.process is None:
                if self._port_occupied():
                    self._error = 'Server is not owned by this widget; refusing to stop it'
                else:
                    self._error = None
                return self.snapshot()
            try:
                self._terminate_owned()
                self._error = None
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._error = f'Could not stop owned API: {exc}'
            return self.snapshot()

    def snapshot(self):
        with self._lock:
            error = self._error
            try:
                mode = self.load_state()['mode']
            except (OSError, ValueError) as exc:
                mode, error = 'fast', f'Cannot read settings: {exc}'
            process = self.process
            owned = process is not None and process.poll() is None
            health = self._get('/health')
            running = self._identity(health)
            if owned and running and health['pid'] != process.pid:
                running, error = False, 'Listener PID does not match owned API'
            if process is not None and not owned:
                error = error or f'API exited with code {process.returncode}; see {self.log_path}'
            elif owned and not running:
                error = error or 'Owned API is not responding with a valid identity'
            elif not running and self._port_occupied():
                error = error or 'Address already in use by an unrecognized server'
            status = 'Running' if running else 'Stopped'
            if running and not owned:
                status = 'Running (not owned)'
            result = {'running': running, 'mode': mode, 'status': 'Error' if error else status,
                      'owned': owned, 'pid': process.pid if owned else health['pid'] if running else None,
                      'error': error}
            if running:
                settings = self._get('/settings')
                if (settings and settings.get('app') == 'chess-helper'
                        and settings.get('pid') == result['pid']):
                    for key in ('busy', 'last_move'):
                        if key in settings:
                            result[key] = settings[key]
                    if result.get('busy') and not error:
                        result['status'] = 'Thinking'
            return result
