# ♟️ ChessExtension-dirb

**Learn chess positions with Stockfish suggestions.** A Chrome extension draws a gold move arrow, and a GTK widget handles Start/Stop and analysis modes.

<p align="center">
  <img src="assets/screenshots/in-game-arrow.png" alt="Chess.com bot board with a Chess Helper move arrow" width="720">
</p>
<p align="center"><em>The arrow suggests a move; it never plays one for you.</em></p>

<p align="center">
  <img src="assets/screenshots/bot-selection.png" alt="Chess.com bot selection screen with the helper arrow" width="720">
  <img src="assets/screenshots/widget-control.png" alt="Chess Helper widget with status and controls" width="260">
</p>
<p align="center"><sub>Chess.com account handles in these screenshots are blurred.</sub></p>

## ✨ What it does

- Reads the board position from Chess.com and asks a local Stockfish API for a candidate move.
- Displays that move as a non-interactive board arrow.
- Lets the desktop widget start/stop the API and choose the search budget.
- Does **not** click, move pieces, or play a game automatically.

The verified browser flow is Chess.com **Play Computer** (`/play/computer`). The automated browser test uses a local fixture, not a live Chess.com account. Review/live extraction paths are best-effort and are not claimed as live-tested.

## 🧩 Project map

| Path | Role |
| --- | --- |
| `chess_api.py` | Localhost API and persistent Stockfish engine |
| `chess_extension/` | Chrome Manifest V3 extension, page bridge, and arrow overlay |
| `widget/` | GTK desktop widget and API process controller |
| `tests/` | API, widget, browser-fixture, and service-worker tests |
| `assets/screenshots/` | Redacted screenshots shown above |

## ⚡ Analysis modes

| Mode | Search budget |
| --- | ---: |
| 🟢 Fast | 1 second |
| 🟡 Balanced | 3 seconds |
| 🟠 Strong | 10 seconds |
| 🔴 Maximum | 30 seconds |

All modes use the same Stockfish strength; only thinking time changes.

## 🚀 Quick start (Kali/Debian)

Install the system packages and Python dependencies:

```sh
sudo apt install stockfish python3-venv python3-gi gir1.2-gtk-3.0
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
```

Then:

1. Clone this repository to a stable folder.
2. In Chrome, open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select `chess_extension/`.
3. From the repository root, start the widget:

   ```sh
   .venv/bin/python widget/chess-helper-widget.py
   ```

4. Open Chess.com **Play Computer**, choose a mode, and click **Start** in the widget.
5. Check for the gold arrow. Click **Stop** when done; the widget stops only the API it owns.

The widget launches the API for you—do **not** run `chess_api.py` separately. The API listens on `127.0.0.1:8765`; do not expose it to the network. Google Chrome is required for the extension; Node.js is used by the service-worker test.

## 🧪 Tests

```sh
.venv/bin/python -m pytest -q tests/test_api.py tests/test_widget_core.py tests/test_widget_ui.py tests/test_extension_browser.py
node tests/test_worker.cjs
```

The browser test expects `google-chrome` on `PATH` (`CHROME_BIN` can override it); the GTK test needs the system GTK/PyGObject packages above.

**Last verified locally:** 73 passed, 1 skipped; service-worker test passed. Dependency/deprecation warnings may appear with system Python, GTK, and python-chess.

## ⚠️ Educational use & disclaimer

1. **Learning only.** This project is intended for chess study and learning from bot positions. It is not affiliated with or endorsed by Chess.com.
2. **Follow platform rules.** Do not use it where outside assistance is prohibited. Check Chess.com’s current terms and rules before use; you are responsible for complying with them and applicable law.
3. **Account risk.** Use it at your own risk. To the maximum extent allowed by law, the creator and maintainer disclaim responsibility for suspensions, bans, rating restrictions, loss of access, or other consequences caused by use or misuse, including rule violations.
4. **No warranty.** The software is provided “as is”; there is no promise that using it is allowed or that an account will be safe. This disclaimer cannot override rights that cannot legally be waived or guarantee immunity from enforcement.

## 🧾 Attribution & cloning/forking

- **Project creator:** `sefafl`
- **GitHub maintainer:** `dirbusterSync`
- Cloning or forking for **personal, non-commercial chess learning** is permitted when you keep both credits, this README and disclaimer, and identify substantial changes.
- This is limited permission, not a general open-source license. Commercial use or removing attribution is not granted. The creator reserves the right to pursue remedies available under applicable law for unauthorized use or credit removal.
- Source files contain plaintext and encoded attribution marks in comments/metadata. They do not change behavior; they are provenance clues, not copy protection or conclusive proof of ownership.

## 🔒 Privacy & repo status

- The repository is **private during development and review**; a public release may happen later.
- `main` is protected against force-push and deletion; authorized users can still make normal commits.
- Chess.com interface elements are third-party property; this project is independent. Screenshot handles are blurred.
- Personal settings, browser profiles, logs, credentials, private keys, backups, and unrelated captures are not included.
