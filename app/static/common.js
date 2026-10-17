// Shared by the host screen and the phones: a reconnecting WebSocket and safe storage.
(() => {
  "use strict";

  const wsUrl = (path) =>
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;

  // Opens a WebSocket and reopens it after drops (waits 1s, 2s ... up to 5s).
  // onOpen runs on every (re)connect, so the caller can re-announce itself.
  function connect(path, { onOpen, onMessage, onStatus }) {
    let ws = null;
    let retries = 0;
    let timer = null;
    let stopped = false;

    const open = () => {
      onStatus("connecting");
      ws = new WebSocket(wsUrl(path));
      ws.onopen = () => {
        retries = 0;
        onStatus("open");
        if (onOpen) onOpen(ws);
      };
      ws.onmessage = (event) => {
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }
        onMessage(msg);
      };
      ws.onclose = () => {
        onStatus("closed");
        if (stopped) return;
        retries += 1;
        timer = setTimeout(open, Math.min(5000, 1000 * retries));
      };
      ws.onerror = () => {};
    };

    open();

    return {
      send(obj) {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
      },
      isOpen: () => !!ws && ws.readyState === WebSocket.OPEN,
      // Drop a connection that looks dead (e.g. a phone just woke up) and start fresh.
      reset() {
        clearTimeout(timer);
        retries = 0;
        if (ws) {
          ws.onclose = null;
          ws.onmessage = null;
          try { ws.close(); } catch { /* already closed */ }
        }
        open();
      },
      stop() {
        stopped = true;
        clearTimeout(timer);
        if (ws) {
          ws.onmessage = null;
          try { ws.close(); } catch { /* already closed */ }
        }
      },
    };
  }

  // localStorage can throw (private mode, blocked site data), so never trust it.
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* ignore */ } },
    del(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
  };

  window.HOT = { connect, store };
})();
