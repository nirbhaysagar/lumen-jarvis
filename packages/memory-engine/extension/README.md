# Sheldon Capture (extension v0.1)

Front-Side capture for Lumen. Reads the ChatGPT/Claude chat you already
have open, shows health + preview, Saves into your brain.

## Install (2 min, Chrome/Arc/Brave)

1. `python3 server.py` running (Save target).
2. Open `chrome://extensions` → Developer mode ON → **Load unpacked** →
   select this `extension/` folder.
3. Open a ChatGPT or Claude chat → click the Sheldon icon (opens side panel).

## Use

- Status dot: black = reading N messages; hollow = page changed, retry.
- Chain: `Auto-file` (recommended) or pin one workspace.
- **Save conversation** → cards appear in Sheldon (`http://localhost:8765`).
- Server off? Capture queues locally, flushes on next install/start.

## Honest limits (v0.1)

- Selectors verified against ChatGPT/Claude DOM at build time; both redesign
  often. The status dot exists precisely for this — if it goes hollow after
  a site update, tell me and we add the new selector (the 20%-forever tax
  from the Context Graph analysis).
- No auto-capture yet: explicit Save per conversation (validates the habit
  before we automate). Perplexity/Cursor/Gemini: next selectors batch.

