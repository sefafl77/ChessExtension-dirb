(() => {
  if (window.__chessHelperFenBridgeInstalled) return;
  Object.defineProperty(window, "__chessHelperFenBridgeInstalled", { value: true });

  const EVENT_NAME = "chess-helper:page-data";
  let lastFen = null;
  let lastStatusAt = 0;

  function validFen(value) {
    if (typeof value !== "string") return false;
    const fields = value.trim().split(/\s+/);
    if (fields.length < 4 || fields.length > 6) return false;
    const ranks = fields[0].split("/");
    if (ranks.length !== 8 || !/^[wb]$/.test(fields[1])) return false;
    for (const rank of ranks) {
      let squares = 0;
      for (const token of rank) {
        if (/^[1-8]$/.test(token)) squares += Number(token);
        else if (/^[prnbqkPRNBQK]$/.test(token)) squares += 1;
        else return false;
      }
      if (squares !== 8) return false;
    }
    return true;
  }

  function readProperty(owner, key) {
    try {
      if (!owner) return null;
      let value = owner[key];
      if (typeof value === "function") value = value.call(owner);
      if (validFen(value)) return value.trim();
    } catch (_) {}
    return null;
  }

  function getFen() {
    const board = document.querySelector("wc-chess-board") || document.querySelector("chess-board");
    let fen = tryWebComponentBoard(board);
    if (fen) return fen;

    fen = tryGlobalGameObjects(board);
    if (fen) return fen;

    fen = tryVuePiniaStore();
    if (fen) return fen;

    return null;
  }

  function tryWebComponentBoard(board) {
    if (!board) return null;
    const keys = ["fen", "FEN", "getFEN", "getFen", "get_fen", "currentFEN", "currentFen"];
    if (board.game) {
      for (const key of keys) {
        const fen = readProperty(board.game, key);
        if (fen) return { fen, source: `board.game.${key}` };
      }
    }
    for (const key of keys) {
      const fen = readProperty(board, key);
      if (fen) return { fen, source: `board.${key}` };
    }
    return null;
  }

  function tryGlobalGameObjects(board) {
    const gameObjects = [
      window.game,
      window.chessComGame,
      window._chesscom && window._chesscom.game,
      window.Chessboard && window.Chessboard.instance,
      window.liveGame,
      window.gameLive,
      window.chesscom && window.chesscom.game,
      window.chesscom && window.chesscom.liveGame,
      window.chesscom && window.chesscom.gamedata,
      window.chesscom && window.chesscom.state,
      window.gamedata,
      window.gameData,
      window.chessCom && window.chessCom.game,
      window.chessCom && window.chessCom.liveGame,
      board,
      board?.game,
      document.querySelector("chess-board")
    ];
    const keys = ["fen", "FEN", "getFEN", "getFen", "get_fen", "currentFEN", "currentFen"];
    for (const owner of gameObjects) {
      for (const key of keys) {
        const fen = readProperty(owner, key);
        if (fen) return { fen, source: key };
      }
    }
    return null;
  }

  function tryVuePiniaStore() {
    try {
      const globalState = window.__VUE_DEVTOOLS_KIT_GLOBAL_STATE__;
      if (!globalState || !globalState.appRecords) return null;

      for (const record of Object.values(globalState.appRecords)) {
        const app = record.app;
        if (!app || !app._context) continue;

        const provides = app._context.provides;
        if (!provides) continue;

        for (const [key, pinia] of provides) {
          const keyStr = key.toString ? key.toString() : String(key);
          if (!keyStr.includes('pinia')) continue;

          if (pinia && pinia._s) {
            for (const [storeId, store] of pinia._s) {
              if (store.$state) {
                const state = store.$state;
                const fenKeys = ['fen', 'FEN', 'currentFEN', 'currentFen', 'position', 'positionFen', 'gameFen'];
                for (const fenKey of fenKeys) {
                  if (state[fenKey] && validFen(state[fenKey])) {
                    return { fen: state[fenKey].trim(), source: `pinia.${storeId}.${fenKey}` };
                  }
                }
                // Check nested objects in state
                for (const stateKey of Object.keys(state)) {
                  const val = state[stateKey];
                  if (val && typeof val === 'object') {
                    for (const fenKey of fenKeys) {
                      if (val[fenKey] && validFen(val[fenKey])) {
                        return { fen: val[fenKey].trim(), source: `pinia.${storeId}.${stateKey}.${fenKey}` };
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    } catch (e) {
      console.log("[Chess Helper] Pinia access error:", e.message);
    }
    return null;
  }

  function send(data) {
    window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: JSON.stringify(data) }));
  }

  function poll() {
    const result = getFen();
    if (result && result.fen !== lastFen) {
      lastFen = result.fen;
      send({ type: "fen", fen: result.fen, source: result.source });
      return;
    }
    if (!result && Date.now() - lastStatusAt > 5000) {
      lastStatusAt = Date.now();
      send({ type: "status", message: "No FEN getter found in page context yet" });
    }
  }

  poll();
  window.setInterval(poll, 1000);
})();

// Embedded provenance [Base64]: c2VmYWZs
