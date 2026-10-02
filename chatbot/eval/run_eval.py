"""
Run the golden set through the assistant and score it.

    python eval/run_eval.py --data /path/chat_data [--golden eval/golden.json] [--limit N]

Scores per case: numbers (every expected figure appears in the answer within tolerance),
tool selection, required/forbidden text, and refusal behaviour (no figures + admits/declines).
Writes eval/results/latest.json and latest.md.
"""
import argparse, json, os, re, sys, time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
for line in (HERE.parents[1] / ".env").read_text().splitlines() if (HERE.parents[1] / ".env").exists() else []:
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from agent import Assistant, parse_numbers
from tools import Facts

REFUSAL_MARKERS = ["only answer", "only help", "can only", "cannot", "can't", "can not", "don't have", "do not have", "not available",
                   "no data", "unknown", "couldn't find", "could not find", "not found", "unable", "doesn't include", "does not include",
                   "not in the", "isn't in", "not something", "outside", "does not provide", "doesn't provide", "no information", "did not find",
                   "i don't", "no chart data", "not tracked", "not part of"]
KEY_LIKE = re.compile(r"(gsk_|AIza|sk-[A-Za-z0-9]{10,})")


def score(case, res):
    e, ans = case["expect"], res["answer"]
    nums = [v for v, *_ in parse_numbers(ans)]
    checks = {}
    if e["numbers"]:
        miss = []
        for n in e["numbers"]:
            tol = n["tol_pct"] / 100.0
            ok = any(abs(v - n["value"]) <= max(tol * abs(n["value"]), 0.5 if tol == 0 else 0) for v in nums)
            if not ok:
                miss.append(n["label"])
        checks["numbers"] = not miss
        if miss:
            checks["missing_numbers"] = miss
    if e["tools_any"]:
        checks["tool"] = any(t["name"] in e["tools_any"] and t["ok"] for t in res["tools"])
    if e["contains_any"]:
        checks["contains"] = any(x.lower() in ans.lower() for x in e["contains_any"])
    if e["must_not_contain"]:
        checks["must_not"] = not any(x.lower() in ans.lower() for x in e["must_not_contain"])
    if e["refusal"]:
        big = [v for v, tagged, *_ in parse_numbers(ans) if v >= 1000 or tagged]
        admits = res["blocked"] or any(m in ans.lower() for m in REFUSAL_MARKERS) or any(not t["ok"] for t in res["tools"])
        checks["refusal"] = (not big) and admits and not KEY_LIKE.search(ans)
    return all(v for k, v in checks.items() if isinstance(v, bool)), checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--golden", default=str(HERE / "golden.json"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--pause", type=float, default=0.5)
    a = ap.parse_args()
    gold = json.load(open(a.golden))
    bot = Assistant(Facts(a.data))
    if str(bot.facts.as_of) != gold["as_of"]:
        print(f"WARNING: data as_of {bot.facts.as_of} != golden as_of {gold['as_of']}; regenerate golden.json", file=sys.stderr)
    out, by_cat = [], defaultdict(lambda: [0, 0])
    for i, case in enumerate(gold["cases"][: a.limit]):
        res = bot.ask(case["question"])
        ok, checks = score(case, res)
        by_cat[case["category"]][0] += ok
        by_cat[case["category"]][1] += 1
        out.append({"id": case["id"], "category": case["category"], "question": case["question"], "pass": ok, "checks": checks,
                    "answer": res["answer"], "tools": [t["name"] for t in res["tools"]], "verified": res["verified"],
                    "model": res["model"], "ms": res["ms"], "tokens": res["tokens"]})
        print(f"[{'PASS' if ok else 'FAIL'}] {case['id']:<34} {res['ms']:>5}ms {res['model']:<26} {'' if ok else checks}", flush=True)
        time.sleep(a.pause)
    n = len(out)
    passed = sum(o["pass"] for o in out)
    summary = {"as_of": gold["as_of"], "cases": n, "passed": passed, "pass_rate": round(100 * passed / n, 1),
               "by_category": {k: {"passed": v[0], "total": v[1]} for k, v in by_cat.items()},
               "verified_rate": round(100 * sum(1 for o in out if o["verified"]) / n, 1),
               "avg_ms": round(sum(o["ms"] for o in out) / n),
               "p95_ms": sorted(o["ms"] for o in out)[int(0.95 * (n - 1))],
               "avg_tokens_in": round(sum(o["tokens"]["in"] for o in out) / n),
               "run_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    (HERE / "results").mkdir(exist_ok=True)
    json.dump({"summary": summary, "cases": out}, open(HERE / "results" / "latest.json", "w"), indent=1, ensure_ascii=False)
    md = [f"# Chatbot eval ({summary['run_at']})", "", f"Data as of {summary['as_of']}. **{passed}/{n} passed ({summary['pass_rate']}%)**.", "",
          "| Category | Passed |", "|---|---|"] + [f"| {k} | {v['passed']}/{v['total']} |" for k, v in summary["by_category"].items()] + \
         ["", f"Verified-answer rate {summary['verified_rate']}%, avg latency {summary['avg_ms']} ms, p95 {summary['p95_ms']} ms.", "", "## Failures", ""]
    for o in out:
        if not o["pass"]:
            md.append(f"- **{o['id']}**: {o['question']}\n  - checks: {o['checks']}\n  - answer: {o['answer'][:300]}")
    (HERE / "results" / "latest.md").write_text("\n".join(md))
    print("\n" + json.dumps(summary, indent=1))
    sys.exit(0 if summary["pass_rate"] >= 85 else 1)


if __name__ == "__main__":
    main()
