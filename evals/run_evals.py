"""Score the detector + router against labelled sets and write honest results.

    python evals/run_evals.py            # both sets
    python evals/run_evals.py --gate     # exit 1 if a safety gate fails (used in CI)

Writes reports/eval_results.json and reports/eval_results.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from handoff_lab.detection import detect                      # noqa: E402
from handoff_lab.metrics import confusion, prf, rule_of_three, wilson  # noqa: E402
from handoff_lab.router import decide                         # noqa: E402

SIGNALS = {
    "complaint": {"complaint", "possible_complaint"},
    "vuln": {"possible_vulnerability", "self_harm_risk"},
    "selfharm": {"self_harm_risk"},
    "fraud": {"suspected_unauthorised"},
    "human": {"asked_for_human"},
    "access": {"account_access"},
}
# Gates are on the dev set's behaviour AND reported on held-out. They are deliberately
# about the costly direction: a missed self-harm or fraud case is a conduct failure.
GATES = {"selfharm_to_vulnerable_recall": 1.0, "fraud_to_security_recall": 1.0}


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / "evals" / name).read_text().splitlines() if line.strip()]


DETECTOR = {"fn": lambda text, history: detect(text, history)}


def predict(row: dict) -> dict:
    det = DETECTOR["fn"](row["text"], row.get("history") or [])
    dec = decide(det)
    pred = {s for s, flags in SIGNALS.items() if flags & dec.flags}
    return {"signals": sorted(pred), "handoff": dec.action == "handoff", "queue": dec.queue,
            "priority": dec.priority, "flags": sorted(dec.flags), "topic": det.topic}


def score(rows: list[dict]) -> dict:
    preds = [predict(r) for r in rows]
    per_signal = {}
    for s in SIGNALS:
        y = [s in r["signals"] for r in rows]
        p = [s in pr["signals"] for pr in preds]
        cm = confusion(y, p)
        m = prf(cm)
        pos = cm["tp"] + cm["fn"]
        per_signal[s] = {**cm, **m, "support": pos,
                         "recall_ci": wilson(cm["tp"], pos) if pos else None}

    def gate(sig: str, queue: str) -> dict:
        idx = [i for i, r in enumerate(rows) if sig in r["signals"]]
        ok = sum(1 for i in idx if preds[i]["queue"] == queue)
        return {"n": len(idx), "ok": ok, "recall": ok / len(idx) if idx else None,
                "ci": wilson(ok, len(idx)) if idx else None,
                "upper_bound_if_zero_misses": rule_of_three(len(idx)) if idx and ok == len(idx) else None}

    need = [i for i, r in enumerate(rows) if r["human_needed"]]
    no_need = [i for i, r in enumerate(rows) if not r["human_needed"]]
    reached = sum(1 for i in need if preds[i]["handoff"])
    false_esc = sum(1 for i in no_need if preds[i]["handoff"])
    q_ok = sum(1 for i in need if preds[i]["handoff"] and preds[i]["queue"] == rows[i]["queue"])

    misses = []
    for r, pr in zip(rows, preds):
        problems = []
        missed = sorted(set(r["signals"]) - set(pr["signals"]))
        extra = sorted(set(pr["signals"]) - set(r["signals"]))
        if missed:
            problems.append("missed " + ",".join(missed))
        if extra:
            problems.append("false " + ",".join(extra))
        if r["human_needed"] and not pr["handoff"]:
            problems.append("should have reached a person")
        if not r["human_needed"] and pr["handoff"]:
            problems.append("escalated unnecessarily")
        if r["human_needed"] and pr["handoff"] and r["queue"] != pr["queue"]:
            problems.append(f"wrong queue {pr['queue']} (expected {r['queue']})")
        if problems:
            misses.append({"id": r["id"], "text": r["text"], "tags": r["tags"], "problems": problems,
                           "pred_queue": pr["queue"], "pred_flags": pr["flags"]})
    by_tag: dict[str, list[int]] = {}
    miss_ids = {m["id"] for m in misses}
    for r in rows:
        for t in r["tags"]:
            by_tag.setdefault(t, [0, 0])
            by_tag[t][0] += 1
            by_tag[t][1] += r["id"] in miss_ids
    return {
        "n": len(rows), "per_signal": per_signal,
        "reach_person": {"n": len(need), "ok": reached, "rate": reached / len(need) if need else None,
                         "ci": wilson(reached, len(need))},
        "false_escalation": {"n": len(no_need), "count": false_esc,
                             "rate": false_esc / len(no_need) if no_need else None,
                             "ci": wilson(false_esc, len(no_need))},
        "queue_accuracy": {"n": len(need), "ok": q_ok, "rate": q_ok / len(need) if need else None},
        "gates": {"selfharm_to_vulnerable_recall": gate("selfharm", "PriorityVulnerable"),
                  "fraud_to_security_recall": gate("fraud", "Security")},
        "by_tag": {t: {"n": n, "with_problems": k} for t, (n, k) in sorted(by_tag.items())},
        "misses": misses,
    }


def _pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def _ci(ci):
    return "" if not ci else f" (95% CI {100 * ci[0]:.0f}–{100 * ci[1]:.0f}%)"


def markdown(results: dict) -> str:
    out = ["# Detection and routing evaluation\n",
           "Generated by `python evals/run_evals.py`. All conversations are synthetic and hand-labelled.",
           "- **dev**: what the rules were tuned on. Expect it to look good.",
           "- **held-out v1**: written blind for rules v1, which reached a person in only 37% of the cases that "
           "needed one (frozen in `reports/baseline/eval_rules_v1.md`). Rules v2 were then rewritten by concept after "
           "reading those misses, so this set is no longer blind and its v2 score is optimistic.",
           "- **held-out v2 (blind)**: written after rules v2 were frozen and before running them on it. "
           "**This is the honest number.**",
           "",
           "Samples are small, so read the confidence intervals, not the point estimates.\n"]
    for name, r in results.items():
        out.append(f"\n## {name} set (n = {r['n']})\n")
        rp, fe, qa = r["reach_person"], r["false_escalation"], r["queue_accuracy"]
        out.append(f"- Reached a person when one was needed: **{rp['ok']}/{rp['n']} = {_pct(rp['rate'])}**{_ci(rp['ci'])}")
        out.append(f"- Escalated when not needed: **{fe['count']}/{fe['n']} = {_pct(fe['rate'])}**{_ci(fe['ci'])}")
        out.append(f"- Right queue when a person was needed: **{qa['ok']}/{qa['n']} = {_pct(qa['rate'])}**")
        for g, v in r["gates"].items():
            ub = (f"; zero misses in {v['n']} still allows up to ~{100 * v['upper_bound_if_zero_misses']:.0f}% "
                  "(rule of three)") if v.get("upper_bound_if_zero_misses") else ""
            out.append(f"- Gate `{g}`: {v['ok']}/{v['n']} = {_pct(v['recall'])}{_ci(v['ci'])}{ub}")
        out.append("\n| Signal | Support | TP | FP | FN | Precision | Recall | Recall 95% CI |")
        out.append("|---|---|---|---|---|---|---|---|")
        for s, m in r["per_signal"].items():
            out.append(f"| {s} | {m['support']} | {m['tp']} | {m['fp']} | {m['fn']} | {_pct(m['precision'])} | "
                       f"{_pct(m['recall'])} | {_ci(m['recall_ci']).strip(' ()').replace('95% CI ', '') or 'n/a'} |")
        out.append(f"\n### Every case with a problem ({len(r['misses'])})\n")
        out.append("| id | text | problems | predicted queue |")
        out.append("|---|---|---|---|")
        for m in r["misses"]:
            out.append(f"| {m['id']} | {m['text'][:70].replace('|', '/')} | {'; '.join(m['problems'])} | "
                       f"{m['pred_queue'] or '-'} |")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--llm", action="store_true", help="rules + model layer (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()
    suffix = ""
    if args.llm:
        import os
        from handoff_lab.llm_detector import anthropic_complete, hybrid_detect
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("--llm needs ANTHROPIC_API_KEY"); return 2
        DETECTOR["fn"] = lambda text, history: hybrid_detect(text, history, anthropic_complete)
        suffix = "_llm"
    results = {"dev": score(load("dev.jsonl")),
               "held-out v1 (seen while building rules v2)": score(load("heldout.jsonl")),
               "held-out v2 (blind)": score(load("heldout2.jsonl"))}
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / f"eval_results{suffix}.json").write_text(json.dumps(results, indent=2))
    (out / f"eval_results{suffix}.md").write_text(
        markdown(results).replace("# Detection and routing evaluation", "# Detection and routing evaluation"
                                  + (" (rules + model layer)" if suffix else " (rules only)"), 1))
    for name, r in results.items():
        print(f"{name}: reach-person {_pct(r['reach_person']['rate'])}, false-escalation "
              f"{_pct(r['false_escalation']['rate'])}, queue {_pct(r['queue_accuracy']['rate'])}, "
              f"problems {len(r['misses'])}/{r['n']}")
    failed = [f"{name}:{g}" for name, r in results.items() if name == "dev"
              for g, v in r["gates"].items() if v["recall"] is not None and v["recall"] < GATES[g]]
    if args.gate and failed:
        print("GATE FAILED:", failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
