// Host screen: shows the join code, who is connected, and runs the connection test.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const { connect } = window.HOT;

  const key = new URLSearchParams(location.search).get("key") || "";
  let state = { players: [], locked: false, buzz_id: 0 };
  let everOpened = false;

  fetch("/api/join-url")
    .then((res) => res.json())
    .then((data) => { $("join-url").textContent = data.url; })
    .catch(() => {});

  const conn = connect("/ws/host?key=" + encodeURIComponent(key), {
    onStatus(status) {
      const live = status === "open";
      if (live) everOpened = true;
      $("conn-dot").className = "dot " + (live ? "on" : "wait");
      $("conn-text").textContent = live ? "Live" : everOpened ? "Reconnecting" : "Connecting";
    },
    onMessage(msg) {
      if (msg.type === "state") {
        state = msg;
        render();
      }
    },
  });

  // Idle proxies drop quiet connections, so send a small heartbeat.
  setInterval(() => conn.send({ type: "ping" }), 25000);

  const pingClass = (ms) => (ms < 120 ? "good" : ms < 350 ? "ok" : "slow");

  function cell(text, className) {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    return td;
  }

  function render() {
    const players = state.players;
    const online = players.filter((p) => p.connected).length;
    const offline = players.length - online;

    $("count").textContent = online;
    $("count-sub").textContent =
      (online === 1 ? "player here" : "players here") + (offline ? `, ${offline} offline` : "");

    $("lock").textContent = state.locked ? "Unlock the game" : "Lock the game";
    $("lock").classList.toggle("on", state.locked);
    $("empty").hidden = players.length > 0;

    if (state.buzz_id > 0) {
      const replied = players.filter((p) => p.buzz_ack_ms !== null).length;
      const waiting = players.filter((p) => p.connected && p.buzz_ack_ms === null).length;
      $("buzz-summary").textContent = `Buzz ${state.buzz_id}: ${replied} replied, ${waiting} still waiting.`;
    } else {
      $("buzz-summary").textContent = "";
    }

    const rows = $("rows");
    rows.replaceChildren();
    for (const p of players) {
      const tr = document.createElement("tr");
      if (!p.connected) tr.className = "offline";

      const nameCell = document.createElement("td");
      const wrap = document.createElement("span");
      wrap.className = "name";
      const dot = document.createElement("span");
      dot.className = "dot" + (p.connected ? " on" : "");
      wrap.append(dot, document.createTextNode(p.name));
      nameCell.append(wrap);

      const rtt = p.connected && p.rtt_ms !== null ? `${p.rtt_ms} ms` : "-";
      const rttCell = cell(rtt, "num " + (p.connected && p.rtt_ms !== null ? pingClass(p.rtt_ms) : ""));

      let buzz = "-";
      if (state.buzz_id > 0) {
        if (p.buzz_ack_ms !== null) buzz = `${p.buzz_ack_ms} ms`;
        else if (p.connected) buzz = "waiting";
      }
      const buzzCell = cell(buzz, "num");

      const actionCell = document.createElement("td");
      const kick = document.createElement("button");
      kick.className = "kick";
      kick.type = "button";
      kick.textContent = "✕";
      kick.setAttribute("aria-label", `Remove ${p.name}`);
      kick.addEventListener("click", () => {
        if (confirm(`Remove ${p.name} from the game?`)) conn.send({ type: "kick", id: p.id });
      });
      actionCell.append(kick);

      tr.append(nameCell, rttCell, buzzCell, actionCell);
      rows.append(tr);
    }
  }

  $("buzz").addEventListener("click", () => conn.send({ type: "buzz" }));
  $("lock").addEventListener("click", () => conn.send({ type: "lock", locked: !state.locked }));

  render();
})();
