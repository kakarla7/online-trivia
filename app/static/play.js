// Phone page: join with a name, stay connected, report latency, answer test buzzes.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const { connect, store } = window.HOT;

  const screens = {
    join: $("screen-join"),
    lobby: $("screen-lobby"),
    blocked: $("screen-blocked"),
  };
  const show = (name) => {
    for (const [key, el] of Object.entries(screens)) el.hidden = key !== name;
  };

  const MESSAGES = {
    name: "Enter a name to join.",
    locked: "The host has locked the game. Ask them to unlock it.",
    full: "The game is full.",
  };
  const STALE_MS = 8000; // no pong for this long on an "open" socket means it died quietly

  let conn = null;
  let pingTimer = null;
  let wakeLock = null;
  let everOpened = false;
  let lastPong = 0;

  function setStatus(state) {
    const live = state === "open";
    $("status-dot").className = "dot " + (live ? "on" : "wait");
    $("status-text").textContent = live ? "Connected" : everOpened ? "Reconnecting" : "Connecting";
    if (!live) $("rtt").textContent = "-";
  }

  function stopAll() {
    if (conn) conn.stop();
    conn = null;
    clearInterval(pingTimer);
  }

  function start(name) {
    stopAll();
    everOpened = false;
    conn = connect("/ws/play", {
      onStatus(state) {
        if (state === "open") everOpened = true;
        setStatus(state);
        if (!screens.join.hidden) {
          $("join-error").textContent = state === "closed" ? "Can't reach the game yet. Retrying." : "";
        }
      },
      onOpen(ws) {
        ws.send(JSON.stringify({ type: "join", name, token: store.get("hot_token") || undefined }));
      },
      onMessage: handle,
    });
  }

  function handle(msg) {
    switch (msg.type) {
      case "welcome":
        store.set("hot_token", msg.token);
        store.set("hot_name", msg.name);
        $("me").textContent = msg.name;
        $("join-btn").disabled = false;
        $("join-btn").textContent = "Join the game";
        lastPong = performance.now();
        show("lobby");
        startPings();
        keepAwake();
        break;
      case "pong": {
        lastPong = performance.now();
        const rtt = Math.max(0, Math.round(lastPong - msg.t));
        $("rtt").textContent = rtt + " ms";
        if (conn) conn.send({ type: "rtt", ms: rtt });
        break;
      }
      case "buzz":
        onBuzz(msg.id);
        break;
      case "error":
        stopAll();
        resetJoinButton();
        $("join-error").textContent = MESSAGES[msg.code] || "Couldn't join. Try again.";
        show("join");
        break;
      case "kicked":
        stopAll();
        store.del("hot_token");
        blocked("The host removed you", "Ask the host if this was a mistake, then join again.");
        break;
      case "replaced":
        stopAll();
        blocked("Open on another screen", "This game is open somewhere else. Close that one, or join again here.");
        break;
    }
  }

  function startPings() {
    clearInterval(pingTimer);
    const tick = () => {
      checkAlive();
      if (conn) conn.send({ type: "ping", t: performance.now() });
    };
    tick();
    pingTimer = setInterval(tick, 3000);
  }

  // Phones sleep and networks switch; if the socket looks open but went silent, rebuild it.
  function checkAlive() {
    if (conn && conn.isOpen() && performance.now() - lastPong > STALE_MS) {
      lastPong = performance.now();
      conn.reset();
    }
  }

  function onBuzz(id) {
    if (conn) conn.send({ type: "buzz_ack", id });
    if (navigator.vibrate) navigator.vibrate(200);
    document.body.classList.remove("flash");
    void document.body.offsetWidth; // restart the animation if buzzes arrive back to back
    document.body.classList.add("flash");
    $("buzz-note").textContent = `Buzz ${id} received. The host can see your reply.`;
  }

  async function keepAwake() {
    try {
      if ("wakeLock" in navigator && !wakeLock) {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      }
    } catch { /* not supported or denied: the game still works */ }
  }

  function blocked(title, text) {
    $("blocked-title").textContent = title;
    $("blocked-text").textContent = text;
    show("blocked");
  }

  function resetJoinButton() {
    $("join-btn").disabled = false;
    $("join-btn").textContent = "Join the game";
  }

  document.body.addEventListener("animationend", () => document.body.classList.remove("flash"));

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !screens.lobby.hidden) {
      keepAwake();
      checkAlive();
    }
  });

  $("join-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("name").value.trim();
    if (!name) {
      $("join-error").textContent = MESSAGES.name;
      return;
    }
    $("join-error").textContent = "";
    $("join-btn").disabled = true;
    $("join-btn").textContent = "Joining";
    start(name);
  });

  $("leave").addEventListener("click", () => {
    if (conn) conn.send({ type: "leave" });
    stopAll();
    store.del("hot_token");
    store.del("hot_name");
    $("name").value = "";
    $("buzz-note").textContent = "";
    show("join");
  });

  $("blocked-back").addEventListener("click", () => {
    $("join-error").textContent = "";
    show("join");
  });

  // Returning phone: rejoin straight away with the saved seat.
  const savedName = store.get("hot_name");
  $("name").value = savedName || "";
  if (savedName && store.get("hot_token")) {
    $("me").textContent = savedName;
    show("lobby");
    start(savedName);
  } else {
    show("join");
  }
})();
