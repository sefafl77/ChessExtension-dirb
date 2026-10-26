# ChessExtension-dirb

Local Chess.com bot-position helper: a Chrome extension reads the current board position, a loopback-only Python API asks Stockfish for a move, and a small GTK desktop widget starts/stops the helper and selects the analysis budget. It displays a suggested arrow; it does not play moves for you.

## Components

- `chess_api.py` — localhost API and persistent Stockfish engine.
- `chess_extension/` — Manifest V3 extension, page bridge, and move overlay.
- `widget/` — GTK control widget and owned-process controller.
- `tests/` — API, widget, browser-fixture, and service-worker regression tests.

The verified browser flow is Chess.com `/play/computer`. The headless browser test uses a local fixture, not a live Chess.com account. Review/live board extraction paths are best-effort and are not represented as live-tested here.

## Requirements (Kali/Debian)

```sh
sudo apt install stockfish python3-venv python3-gi gir1.2-gtk-3.0
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
```

Google Chrome is needed to use the extension and to run the browser regression test. Node.js is needed for the service-worker test.

## Run

1. Clone this repository to a stable local directory.
2. In Chrome, open `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select this repository's `chess_extension/` directory.
3. Start the desktop widget:

   ```sh
   .venv/bin/python widget/chess-helper-widget.py
   ```

4. Open Chess.com and go to **Play Computer**. Choose an analysis mode in the widget and click **Start**. The widget owns the API process; do not start `chess_api.py` separately.
5. Use **Stop** in the widget when finished. This stops only the API process it started.

The widget persists the selected mode and uses the repository's `chess_api.py` when run from this checkout. The default analysis budgets are Fast 1 s, Balanced 3 s, Strong 10 s, and Maximum 30 s. All modes use the same Stockfish engine strength.

## Tests

```sh
.venv/bin/python -m pytest -q tests/test_api.py tests/test_widget_core.py tests/test_widget_ui.py tests/test_extension_browser.py
node tests/test_worker.cjs
```

The browser test requires `google-chrome` on `PATH`; set `CHROME_BIN` if the executable has another name. The GTK UI test needs the system GTK/PyGObject packages listed above. API lifecycle tests launch a temporary localhost server and clean it up.

## Local data and safety

The extension communicates with the API only over `127.0.0.1:8765`. Personal widget settings and logs remain under the user's home directory and are not part of this repository. Keep browser profiles, credentials, private keys, screenshots, backups, and local configuration out of commits.
