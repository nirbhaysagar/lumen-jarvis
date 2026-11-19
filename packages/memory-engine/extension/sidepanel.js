/* Side panel: status first, Save second. Talks to content.js + local Lumen.
 * Server must run: python3 server.py (http://127.0.0.1:8765).
 */
const LUMEN = "http://127.0.0.1:8765";
let snap = null;

async function tab() {
  const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
  return t;
}

async function refresh() {
  const dot = document.getElementById("dot");
  const stext = document.getElementById("stext");
  const save = document.getElementById("save");
  const prev = document.getElementById("prev");
  try {
    const t = await tab();
    if (!t || !/chatgpt\.com|chat\.openai\.com|claude\.ai/.test(new URL(t.url).hostname)) {
      dot.className = "dot bad"; stext.textContent = "Open a ChatGPT or Claude chat.";
      save.disabled = true; return;
    }
    snap = await chrome.tabs.sendMessage(t.id, { type: "sheldon:get" });
    if (snap && snap.ok) {
      dot.className = "dot ok";
      stext.textContent = `${snap.messages.length} messages · ${snap.platform}`;
      save.disabled = false;
      prev.textContent = snap.messages.slice(-6)
        .map(m => `${m.role}: ${m.text.slice(0, 140)}`).join("\n\n");
    } else {
      // Degraded, never silent: user sees it, we still offer Manual save.
      dot.className = "dot bad";
      stext.textContent = "Page changed — auto-read degraded. Save still works on retry.";
      save.disabled = false;
      prev.textContent = "";
    }
  } catch (e) {
    dot.className = "dot bad";
    stext.textContent = "Reload the chat tab once, then reopen this panel.";
    save.disabled = true;
  }
}

async function loadChains() {
  try {
    const chains = await fetch(LUMEN + "/chains").then(r => r.json());
    const sel = document.getElementById("chain");
    for (const c of chains.filter(c => c.id !== "default")) {
      const o = document.createElement("option");
      o.value = c.id; o.textContent = c.id;
      sel.appendChild(o);
    }
  } catch { /* server down: Auto-file still queues locally below */ }
}

document.getElementById("save").onclick = async () => {
  const out = document.getElementById("out");
  if (!snap || !snap.ok) { out.textContent = "Nothing readable — retry in a few seconds."; return; }
  const chain = document.getElementById("chain").value;
  const text = snap.messages.map(m => `${m.role}: ${m.text}`).join("\n");
  out.textContent = "Saving…";
  try {
    const r = await fetch(LUMEN + "/ingest", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, chain_id: chain, origin: "ext-" + (snap.platform || "chat") })
    }).then(r => r.json());
    const landed = [...new Set(r.map(x => x.chain_id))].join(", ");
    out.textContent = `Saved ${r.length} cards → ${landed}.`;
  } catch {
    // Server down: queue locally, offer retry. Never lose the capture.
    const q = (await chrome.storage.local.get("sheldon.queue"))["sheldon.queue"] || [];
    q.push({ text, chain, at: Date.now() });
    await chrome.storage.local.set({ "sheldon.queue": q });
    out.textContent = `Server off — queued (${q.length}). Start server.py, reopen panel, Save again.`;
  }
};

loadChains().then(refresh);
setInterval(refresh, 8000); // live status while streaming

