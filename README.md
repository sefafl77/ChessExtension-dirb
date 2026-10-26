# ChessExtension-dirb

> Stockfish-powered move suggestions for Chess.com bot games, surfaced as a gold board arrow and controlled by a lightweight desktop widget.

<p align="center">
  <img src="assets/screenshots/in-game-arrow.png" alt="Chess.com bot board with the Chess Helper move arrow" width="720">
</p>
<p align="center"><em>In-game view: a suggested move is drawn on the board; the helper does not move pieces.</em></p>

<p align="center">
  <img src="assets/screenshots/bot-selection.png" alt="Chess.com bot selection screen with the helper arrow" width="720">
  <img src="assets/screenshots/widget-control.png" alt="Chess Helper desktop widget showing active status and controls" width="260">
</p>
<p align="center"><sub>Account handles in the submitted Chess.com screenshots have been blurred.</sub></p>

## What it does

ChessExtension-dirb reads the board position from the Chess.com page, asks a local Stockfish API for a move, then renders a non-interactive arrow over the board. The GTK widget owns the API process and provides Start/Stop and analysis-mode controls. Moves are suggestions only; the helper does not click or play moves.

The verified browser workflow is Chess.com **Play Computer** (`/play/computer`). A headless browser regression test uses a local fixture, not a live Chess.com account. Review/live extraction paths are best-effort and are not claimed as live-tested.

## Components

| Path | Purpose |
| --- | --- |
| `chess_api.py` | Localhost HTTP API and persistent Stockfish engine |
| `chess_extension/` | Manifest V3 extension, page-context FEN bridge, and gold overlay |
| `widget/` | GTK desktop controls and process lifecycle controller |
| `tests/` | API, widget, extension-fixture, and service-worker regression tests |
| `assets/screenshots/` | Redacted product screenshots used in this README |

## Analysis modes

| Widget mode | Search budget |
| --- | ---: |
| Fast | 1 second |
| Balanced | 3 seconds |
| Strong | 10 seconds |
| Maximum | 30 seconds |

All modes use the same Stockfish strength; the selected mode changes the thinking-time budget.

## Requirements (Kali/Debian)

```sh
sudo apt install stockfish python3-venv python3-gi gir1.2-gtk-3.0
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
```

Google Chrome is required to use the extension and run the browser regression test. Node.js is needed for the service-worker test.

## Quick start

1. Clone the repository to a stable local directory.
2. In Chrome, open `chrome://extensions`, enable **Developer mode**, select **Load unpacked**, and choose this checkout's `chess_extension/` directory.
3. Start the widget from the repository root:

   ```sh
   .venv/bin/python widget/chess-helper-widget.py
   ```

4. Open Chess.com **Play Computer**. Choose a mode in the widget and click **Start**. The widget starts and owns the API; do not launch `chess_api.py` separately.
5. Confirm the gold arrow appears on the board. Click **Stop** when finished; this stops only the API process started by the widget.

The widget persists its selected mode. The API binds to `127.0.0.1:8765`; it is not intended to be exposed to the network.

## Tests

```sh
.venv/bin/python -m pytest -q tests/test_api.py tests/test_widget_core.py tests/test_widget_ui.py tests/test_extension_browser.py
node tests/test_worker.cjs
```

The browser test expects `google-chrome` on `PATH`; set `CHROME_BIN` if needed. The GTK test requires the system PyGObject/GTK packages above. API lifecycle tests use isolated local ports and clean up their processes.

Validated in the development environment: **73 passed, 1 skipped**; the service-worker test passed. Only dependency/deprecation warnings were reported.

## Attribution and reuse

Project creator: **sefafl**

GitHub maintainer: **dirbusterSync**

Attribution markers are comments or display metadata only; they do not change program behavior. The repository is private and has no reuse license. Do not remove attribution when sharing authorized copies. The `main` branch is protected against force-pushes and deletion; authorized users can still make normal commits.

## Privacy

The screenshots were supplied for this project and the Chess.com account handles have been blurred. Personal widget settings, browser profiles, logs, credentials, private keys, backups, and unrelated captures are not included.
