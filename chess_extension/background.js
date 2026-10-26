// MV3 service worker: inject page-context bridge and proxy local analysis.
const ANALYZE_URL = "http://127.0.0.1:8765/analyze";
const CHESS_PAGE = /^https:\/\/(www\.)?chess\.com\//;

chrome.runtime.onInstalled.addListener(() => {
  console.log("[Chess Helper] Extension installed");
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !["chess-helper:inject-bridge", "chess-helper:analyze", "chess-helper:settings"].includes(message.type)) return false;

  const pageUrl = (sender.tab && sender.tab.url) || sender.url || "";
  if (!CHESS_PAGE.test(pageUrl)) {
    sendResponse({ ok: false, error: "Request rejected: not from chess.com" });
    return false;
  }

  if (message.type === "chess-helper:settings") {
    fetch("http://127.0.0.1:8765/settings", { cache: "no-store", signal: AbortSignal.timeout(2500) })
      .then(response => {
        if (!response.ok) throw new Error(`Settings HTTP ${response.status}`);
        return response.json();
      })
      .then(settings => sendResponse({ ok: true, settings }))
      .catch(error => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message.type === "chess-helper:inject-bridge") {
    if (!sender.tab || sender.tab.id === undefined) {
      sendResponse({ ok: false, error: "No Chrome tab ID for page bridge" });
      return false;
    }
    chrome.scripting.executeScript({
      target: { tabId: sender.tab.id },
      files: ["page_bridge.js"],
      world: "MAIN"
    })
      .then(() => sendResponse({ ok: true }))
      .catch(error => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  fetch(ANALYZE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fen: message.fen, mode: message.mode || "fast" })
  })
    .then(async response => {
      if (!response.ok) throw new Error(`API HTTP ${response.status}`);
      return response.json();
    })
    .then(data => sendResponse({ ok: true, move: data.move }))
    .catch(error => sendResponse({ ok: false, error: error.message }));

  return true;
});
