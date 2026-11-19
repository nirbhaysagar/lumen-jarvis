"""Import ChatGPT export into Lumen. Run: python3 import_chatgpt.py conversations.json [--chain X]
Same pipeline the UI's Bring-old-chats button uses (see lumen/api.import_conversations).
"""
import json, sys
sys.path.insert(0, ".")
from lumen import api as L

path = sys.argv[1] if len(sys.argv) > 1 else "conversations.json"
chain = "auto"
if "--chain" in sys.argv:
    chain = sys.argv[sys.argv.index("--chain") + 1]
try:
    data = json.load(open(path))
except FileNotFoundError:
    print(f"No {path} found. Export from ChatGPT: Settings → Data controls → Export, unzip, then run again.")
    sys.exit(1)

r = L.import_conversations(data, chain_id=chain)
print(f"Imported {r['imported']} conversations into: {', '.join(r['chains']) or '(none)'}.")

