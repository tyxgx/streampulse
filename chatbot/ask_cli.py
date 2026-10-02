"""Local runner: loads the project's .env into this process only (never printed) and asks one question."""
import json, os, sys
from pathlib import Path
for line in (Path(__file__).resolve().parents[1] / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
from agent import Assistant
from tools import Facts
a = Assistant(Facts(os.environ.get("CHAT_DATA", "/tmp/lk/chat_data")))
for q in sys.argv[1:]:
    r = a.ask(q)
    print(f"\nQ: {q}\nA: {r['answer']}\n   tools={[t['name'] for t in r['tools']]} verified={r['verified']} model={r['model']} {r['ms']}ms tokens={r['tokens']}\n   sources={[s['href'] for s in r['sources']]}")
