/* Background worker: opens the panel on icon click, flushes queued captures.
 * WHY a worker at all: content scripts die with the tab; the queue must
 * survive. MV3 wakes this on events only — no persistent background page.
 */
chrome.action.onClicked.addListener(async (tab) => {
  await chrome.sidePanel.open({ tabId: tab.id });
});

chrome.runtime.onInstalled.addListener(async () => {
  // Try flushing anything saved while the server was off.
  const { ["sheldon.queue"]: q } = await chrome.storage.local.get("sheldon.queue");
  if (!q || !q.length) return;
  const rest = [];
  for (const item of q) {
    try {
      await fetch("http://127.0.0.1:8765/ingest", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: item.text, chain_id: item.chain, origin: "ext-queue" })
      });
    } catch { rest.push(item); }
  }
  await chrome.storage.local.set({ "sheldon.queue": rest });
});

