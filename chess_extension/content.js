/** Chess Helper Overlay - Content Script */
(() => {
  const PAGE_EVENT = "chess-helper:page-data";
  let boardElement = null;
  let overlay = null;
  let currentFen = null;
  let lastMove = null;
  let queuedFen = null;
  let analyzing = false;
  let lastStatusMessage = "";
  let lastStatusLogAt = 0;
  const MODES = { fast: "Cepat · 1 dtk", balanced: "Seimbang · 3 dtk", strong: "Kuat · 10 dtk", maximum: "Maksimal · 30 dtk" };
  let selectedMode = "fast";
  let positionRevision = 0;
  let panel = null;
  let helperEnabled = true;
  let desktopControlled = false;
  let settingsBusy = false;

  async function syncSettings() {
    if (settingsBusy) return;
    settingsBusy = true;
    try {
      const response = await chrome.runtime.sendMessage({ type: "chess-helper:settings" });
      if (!response || !response.ok || !response.settings) throw new Error(response?.error || "Server offline");
      const settings = response.settings;
      const wasEnabled = helperEnabled;
      helperEnabled = settings.enabled !== false;
      const wasDesktopControlled = desktopControlled;
      desktopControlled = settings.desktop_control === true;
      if (wasDesktopControlled !== desktopControlled) {
        chrome.storage.local.set({ chessHelperDesktopControl: desktopControlled }).catch(() => {});
      }
      if (panel) panel.hidden = desktopControlled;
      const modeChanged = desktopControlled && Object.prototype.hasOwnProperty.call(MODES, settings.mode) && selectedMode !== settings.mode;
      if (modeChanged) {
        selectedMode = settings.mode;
        panel.querySelector("select").value = selectedMode;
        chrome.storage.local.set({ chessHelperMode: selectedMode }).catch(() => {});
      }
      if (wasEnabled !== helperEnabled || modeChanged) {
        positionRevision += 1;
        lastMove = null;
        queuedFen = null;
        removeOverlay();
        if (helperEnabled && currentFen) {
          queuedFen = currentFen;
          drainQueue();
        }
      }
      if (!helperEnabled) setStatus("Helper berhenti · tekan Start di widget");
    } catch (error) {
      if (helperEnabled) {
        helperEnabled = false;
        positionRevision += 1;
        lastMove = null;
        queuedFen = null;
        removeOverlay();
      }
      setStatus("Helper berhenti · tekan Start di widget");
    } finally {
      settingsBusy = false;
    }
  }

  function setStatus(text) {
    const status = panel && panel.querySelector(".chess-helper-status");
    if (status) status.textContent = text;
  }

  function createModePanel() {
    if (panel) return;
    panel = document.createElement("section");
    panel.id = "chess-helper-controls";
    panel.hidden = desktopControlled;
    panel.setAttribute("aria-label", "Pengaturan Chess Helper");
    const label = document.createElement("label");
    label.htmlFor = "chess-helper-mode";
    label.textContent = "CHESS HELPER";
    const select = document.createElement("select");
    select.id = "chess-helper-mode";
    select.setAttribute("aria-label", "Mode analisis");
    for (const [value, name] of Object.entries(MODES)) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = name;
      select.appendChild(option);
    }
    select.value = selectedMode;
    select.addEventListener("change", () => {
      selectedMode = select.value;
      chrome.storage.local.set({ chessHelperMode: selectedMode }).catch(error => console.warn("[Chess Helper] Mode not saved:", error.message));
      positionRevision += 1;
      removeOverlay();
      lastMove = null;
      setStatus(analyzing ? "Menunggu analisis sebelumnya…" : "Mode tersimpan");
      if (currentFen) {
        queuedFen = currentFen;
        drainQueue();
      }
    });
    const status = document.createElement("div");
    status.className = "chess-helper-status";
    status.setAttribute("role", "status");
    status.textContent = "Menunggu posisi papan…";
    panel.append(label, select, status);
    document.body.appendChild(panel);
  }

  function findBoard() {
    const selectors = [
      "wc-chess-board#board-play-computer",
      "wc-chess-board#board-live",
      "wc-chess-board#board-game",
      "wc-chess-board#board",
      "wc-chess-board.board",
      "wc-chess-board",
      "chess-board",
      "#board-play-computer",
      "#board-live",
      "#board-game",
      "#board-layout-chessboard",
      "#board",
      ".board-layout-board",
      ".board"
    ];
    for (const selector of selectors) {
      const candidate = document.querySelector(selector);
      if (!candidate) continue;
      const rect = candidate.getBoundingClientRect();
      if (rect.width > 100 && rect.height > 100 && Math.abs(rect.width - rect.height) / Math.max(rect.width, rect.height) < 0.15) {
        return candidate;
      }
    }
    return null;
  }

  function removeOverlay() {
    if (overlay) overlay.remove();
    overlay = null;
  }

  function isFlipped(board) {
    const ownClass = typeof board.className === "string"
      ? board.className
      : (board.className && board.className.baseVal) || "";
    const parentClass = board.parentElement && typeof board.parentElement.className === "string"
      ? board.parentElement.className
      : "";
    return /(^|\s)flipped(\s|$)/i.test(`${ownClass} ${parentClass}`) ||
      board.getAttribute("data-orientation") === "black";
  }

  function getOverlay(board) {
    if (overlay && overlay.parentElement === board) return overlay;
    removeOverlay();

    const style = window.getComputedStyle(board);
    if (style.position === "static") board.style.position = "relative";

    overlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    overlay.setAttribute("viewBox", "0 0 8 8");
    overlay.setAttribute("preserveAspectRatio", "none");
    overlay.setAttribute("aria-hidden", "true");
    overlay.classList.add("chess-helper-overlay");
    Object.assign(overlay.style, {
      position: "absolute",
      inset: "0",
      width: "100%",
      height: "100%",
      overflow: "visible",
      pointerEvents: "none",
      zIndex: "999999"
    });
    board.appendChild(overlay);
    return overlay;
  }

  function drawArrow(board, move) {
    if (!board || !/^[a-h][1-8][a-h][1-8][qrbn]?$/i.test(move)) return;
    const svg = getOverlay(board);
    svg.replaceChildren();

    const files = "abcdefgh";
    const flipped = isFlipped(board);
    const coord = square => {
      const file = files.indexOf(square[0]);
      const rank = Number(square[1]);
      return flipped
        ? { x: 7 - file + 0.5, y: rank - 1 + 0.5 }
        : { x: file + 0.5, y: 8 - rank + 0.5 };
    };
    const from = coord(move.slice(0, 2));
    const to = coord(move.slice(2, 4));
    const ns = "http://www.w3.org/2000/svg";

    // Presentation only: use the same square coordinates and orientation.
    const shape = (tag, attrs) => {
      const element = document.createElementNS(ns, tag);
      for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
      return element;
    };
    const defs = shape("defs", {});
    const gradient = shape("linearGradient", {
      id: "chess-helper-gold", gradientUnits: "userSpaceOnUse",
      x1: from.x, y1: from.y, x2: to.x, y2: to.y
    });
    for (const [offset, color] of [["0%", "#d9a633"], ["48%", "#ffe9a3"], ["100%", "#f6c655"]]) {
      gradient.appendChild(shape("stop", { offset, "stop-color": color }));
    }
    defs.appendChild(gradient);
    const marker = shape("marker", {
      id: "chess-helper-arrowhead", markerWidth: "0.62", markerHeight: "0.62",
      refX: "0.46", refY: "0.31", orient: "auto", markerUnits: "userSpaceOnUse"
    });
    marker.appendChild(shape("path", {
      d: "M0.10,0.07 Q0.07,0.04 0.12,0.08 L0.53,0.28 Q0.58,0.31 0.53,0.34 L0.12,0.54 Q0.07,0.57 0.10,0.50 L0.20,0.31 Z",
      fill: "#f7d577", stroke: "#352a16", "stroke-width": "0.038", "stroke-linejoin": "round"
    }));
    defs.appendChild(marker);
    svg.appendChild(defs);

    const group = shape("g", { class: "chess-helper-arrow-group", fill: "none", "stroke-linecap": "round" });
    group.appendChild(shape("circle", {
      cx: from.x, cy: from.y, r: "0.32", fill: "rgba(246,198,85,.13)",
      stroke: "rgba(255,230,159,.8)", "stroke-width": "0.025"
    }));
    group.appendChild(shape("rect", {
      x: to.x - 0.44, y: to.y - 0.44, width: "0.88", height: "0.88", rx: "0.12",
      fill: "rgba(246,198,85,.14)", stroke: "rgba(255,224,143,.85)", "stroke-width": "0.026"
    }));
    group.appendChild(shape("line", {
      class: "chess-helper-arrow-outline", x1: from.x, y1: from.y, x2: to.x, y2: to.y,
      stroke: "rgba(37,29,15,.85)", "stroke-width": "0.20"
    }));
    group.appendChild(shape("line", {
      x1: from.x, y1: from.y, x2: to.x, y2: to.y,
      stroke: "url(#chess-helper-gold)", "stroke-width": "0.13",
      "marker-end": "url(#chess-helper-arrowhead)"
    }));
    svg.appendChild(group);
  }

  async function drainQueue() {
    if (!helperEnabled || analyzing || !queuedFen) return;
    analyzing = true;
    const fen = queuedFen;
    const revision = positionRevision;
    const mode = selectedMode;
    queuedFen = null;
    setStatus(`Menganalisis · ${MODES[mode]}`);
    try {
      const response = await chrome.runtime.sendMessage({
        type: "chess-helper:analyze",
        fen,
        mode
      });
      if (!response || !response.ok) throw new Error(response?.error || "No response from extension worker");
      if (typeof response.move !== "string" || !/^[a-h][1-8][a-h][1-8][qrbn]?$/i.test(response.move)) {
        throw new Error(`Invalid move from API: ${response.move}`);
      }
      if (helperEnabled && fen === currentFen && revision === positionRevision) {
        lastMove = response.move;
        boardElement = findBoard() || boardElement;
        if (boardElement) drawArrow(boardElement, response.move);
        setStatus(`${response.move.slice(0, 2)} → ${response.move.slice(2, 4)} · ${MODES[mode]}`);
        console.log(`[Chess Helper] Best move ${response.move} (FEN via page ${lastFenSource})`);
      }
    } catch (error) {
      console.error("[Chess Helper] Analysis failed:", error.message);
      if (revision === positionRevision) setStatus(`Belum ada panduan · ${error.message}`);
    } finally {
      analyzing = false;
      if (queuedFen) drainQueue();
    }
  }

  let lastFenSource = "unknown";
  function onPageData(event) {
    let data;
    try {
      data = JSON.parse(event.detail);
    } catch (_) {
      return;
    }

    if (data.type === "status") {
      if (data.message !== lastStatusMessage || Date.now() - lastStatusLogAt > 15000) {
        lastStatusMessage = data.message;
        lastStatusLogAt = Date.now();
        console.warn(`[Chess Helper] ${data.message}`);
      }
      return;
    }
    if (data.type !== "fen" || typeof data.fen !== "string" || data.fen === currentFen) return;

    currentFen = data.fen;
    positionRevision += 1;
    lastMove = null;
    lastFenSource = data.source || "page API";
    queuedFen = data.fen;
    removeOverlay();
    console.log(`[Chess Helper] Position received from page (${lastFenSource})`);
    drainQueue();
  }

  function injectPageBridge() {
    chrome.runtime.sendMessage({ type: "chess-helper:inject-bridge" }, response => {
      if (chrome.runtime.lastError) {
        console.error("[Chess Helper] Page bridge injection failed:", chrome.runtime.lastError.message);
      } else if (!response || !response.ok) {
        console.error("[Chess Helper] Page bridge injection failed:", response?.error || "No response");
      } else {
        console.log("[Chess Helper] Page bridge injected into chess.com context");
      }
    });
  }

  async function init() {
    try {
      const saved = await chrome.storage.local.get(["chessHelperMode", "chessHelperDesktopControl"]);
      if (Object.prototype.hasOwnProperty.call(MODES, saved.chessHelperMode)) selectedMode = saved.chessHelperMode;
      desktopControlled = saved.chessHelperDesktopControl === true;
    } catch (error) {
      console.warn("[Chess Helper] Defaulting to fast mode:", error.message);
    }
    createModePanel();
    window.addEventListener(PAGE_EVENT, onPageData);
    boardElement = findBoard();
    if (boardElement) console.log("[Chess Helper] Board found");
    else console.warn("[Chess Helper] Waiting for chessboard element");
    await syncSettings();
    injectPageBridge();
    window.setInterval(syncSettings, 1000);

    window.setInterval(() => {
      const nextBoard = findBoard();
      if (nextBoard !== boardElement) {
        removeOverlay();
        boardElement = nextBoard;
        if (boardElement) {
          console.log("[Chess Helper] Board attached");
          if (lastMove) drawArrow(boardElement, lastMove);
        }
      }
    }, 1000);
  }

  console.log("[Chess Helper] Content script loaded (bridge mode)");
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
