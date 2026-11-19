/* Front-Side capture: reads the chat you already see, structures it for Lumen.
 *
 * WHAT: watches the page for new messages, keeps {role, text} in memory,
 *   reports health (ok/degraded) + count to the side panel.
 * WHY multi-fallback selectors: ChatGPT/Claude redesign often; one brittle
 *   selector = dead product. We try specific → generic → accessibility tree,
 *   and SHOW degradation instead of silently capturing nothing.
 * HOW: MutationObserver re-scans on DOM change (debounced). Streaming replies
 *   settle: we only snapshot when no mutations for 2s or on Save.
 */
(() => {
  const HOST = location.hostname;
  const PLATFORM = HOST.includes("claude") ? "claude" : "chatgpt";

  // Ordered specific → generic. First set that yields >=1 message wins.
  const SELECTORS = {
    chatgpt: [
      "[data-message-author-role]",          // role attr directly on turn
      '[data-testid^="conversation-turn"]',  // turn wrapper
      "main article",                        // message articles
      "main [role='presentation']"
    ],
    claude: [
      "[data-testid='user-message']",
      ".font-claude-message",
      "[data-is-streaming]",
      "main [class*='message' i]"
    ]
  };
  const GENERIC = ["article", "[role='article']", "main div[data-message-id]"];

  let lastSnapshot = { messages: [], at: 0, ok: false, selector: null };
  let scanTimer = null;

  function textOf(el) {
    // Skip buttons/code-toolbar chrome; keep message prose + code.
    const clone = el.cloneNode(true);
    clone.querySelectorAll("button").forEach(b => b.remove());
    return (clone.innerText || "").trim();
  }

  function roleOf(el, platform) {
    const a = el.getAttribute("data-message-author-role");
    if (a) return a === "assistant" ? "assistant" : "user";
    const t = el.getAttribute("data-testid") || "";
    if (/user/i.test(t)) return "user";
    if (el.matches && el.matches("[data-is-streaming]")) return "assistant";
    // Fallback: alternating assumption is WRONG; mark unknown instead.
    return "unknown";
  }

  function extractWith(selectorSets) {
    for (const sel of selectorSets) {
      let els = [];
      try { els = [...document.querySelectorAll(sel)]; } catch { continue; }
      const msgs = [];
      for (const el of els) {
        const text = textOf(el);
        if (text.length < 2) continue;
        // Avoid nested double-count: skip elements contained in an already-taken one.
        if (msgs.some(m => m.el.contains(el))) continue;
        msgs.push({ role: roleOf(el, PLATFORM), text, el });
      }
      // De-dupe identical consecutive (streaming re-renders).
      const dedup = msgs.filter((m, i) => i === 0 || m.text !== msgs[i - 1].text);
      if (dedup.length >= 1) return { messages: dedup, selector: sel };
    }
    return { messages: [], selector: null };
  }

  function scan() {
    const sets = [...(SELECTORS[PLATFORM] || []), ...GENERIC];
    const { messages, selector } = extractWith(sets);
    lastSnapshot = {
      messages: messages.map(({ role, text }) => ({ role, text })),
      at: Date.now(), ok: messages.length >= 1, selector
    };
    chrome.storage.local.set({
      ["sheldon." + PLATFORM]: {
        ok: lastSnapshot.ok, count: lastSnapshot.messages.length,
        selector, url: location.href, at: lastSnapshot.at
      }
    });
  }

  function schedule() {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, 1500); // settle streaming before snapshot
  }

  new MutationObserver(schedule).observe(document.documentElement, {
    childList: true, subtree: true, characterData: true
  });
  scan();

  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    if (msg.type === "sheldon:get") {
      scan(); // fresh snapshot on demand
      reply({ ...lastSnapshot, platform: PLATFORM });
    }
    return true;
  });
})();

