"""Build reports/index.html: evaluation results + measurement simulation, one static page.

    python -m handoff_lab.report        (run evals/run_evals.py first)
"""
from __future__ import annotations

import html
import io
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .metrics import mix_shift, wilson  # noqa: E402
from .simulate import TOPICS, responses_needed, simulate, summarise  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BLUE, ORANGE, GREY, INK, MUTED = "#2a78d6", "#eb6834", "#a3a29c", "#0b0b0b", "#52514e"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": "#d6d5d0", "axes.labelcolor": MUTED,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#ecebe7", "grid.linewidth": 0.8, "axes.axisbelow": True,
    "svg.fonttype": "none",
})


def _svg(fig) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight", transparent=True)
    plt.close(fig)
    s = buf.getvalue()
    return s[s.index("<svg"):]


def chart_eval(ev: dict, v1: dict) -> str:
    rows = [("Rules v1\nblind held-out", v1["held-out"]["reach_person"], True),
            ("Rules v2\ndev (tuned on)", ev["dev"]["reach_person"], False),
            ("Rules v2\nheld-out v1 (seen)", ev["held-out v1 (seen while building rules v2)"]["reach_person"], False),
            ("Rules v2\nblind held-out v2", ev["held-out v2 (blind)"]["reach_person"], True)]
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    for i, (label, r, blind) in enumerate(rows):
        lo, hi = wilson(r["ok"], r["n"])
        rate = r["ok"] / r["n"]
        ax.bar(i, rate * 100, width=0.55, color=BLUE if blind else GREY, zorder=2)
        ax.errorbar(i, rate * 100, yerr=[[100 * (rate - lo)], [100 * (hi - rate)]], color=INK, capsize=4, lw=1)
        ax.text(i, hi * 100 + 3, f"{rate:.0%}  ({r['ok']}/{r['n']})", ha="center", color=INK, fontsize=9)
    ax.set_xticks(range(len(rows)), [r[0] for r in rows])
    ax.set_ylim(0, 112)
    ax.set_ylabel("Reached a person when needed (%)")
    ax.grid(axis="x", visible=False)
    return _svg(fig)


def chart_resolution(s: dict) -> str:
    fig, ax = plt.subplots(figsize=(5.2, 2.3))
    vals = [("Containment\n(no handoff)", s["containment"], GREY), ("True resolution\n(no re-contact)",
                                                                    s["true_resolution"], BLUE)]
    for i, (label, v, c) in enumerate(vals):
        ax.barh(i, v * 100, height=0.5, color=c, zorder=2)
        ax.text(v * 100 + 1, i, f"{v:.1%}", va="center", color=INK)
    ax.set_yticks(range(2), [v[0] for v in vals])
    ax.set_xlim(0, 50)
    ax.invert_yaxis()
    ax.set_xlabel("% of all chats")
    ax.grid(axis="y", visible=False)
    return _svg(fig)


def chart_csat(s: dict) -> str:
    fig, ax = plt.subplots(figsize=(5.8, 2.8))
    groups = ["AI-resolved", "Handed off"]
    rep = [s["reported_csat_ai"], s["reported_csat_handed"]]
    tru = [s["true_csat_ai"], s["true_csat_handed"]]
    w = 0.34
    for i in range(2):
        ax.bar(i - w / 2 - 0.01, rep[i], w, color=BLUE, zorder=2, label="Dashboard (survey answers)" if i == 0 else None)
        ax.bar(i + w / 2 + 0.01, tru[i], w, color=ORANGE, zorder=2, label="All chats (simulated truth)" if i == 0 else None)
        ax.text(i - w / 2, rep[i] + 0.05, f"{rep[i]:.2f}", ha="center", fontsize=9, color=INK)
        ax.text(i + w / 2, tru[i] + 0.05, f"{tru[i]:.2f}", ha="center", fontsize=9, color=INK)
    ax.set_xticks(range(2), groups)
    ax.set_ylim(1, 5)
    ax.set_ylabel("CSAT (1–5)")
    ax.legend(frameon=False, loc="upper right", fontsize=9)
    ax.grid(axis="x", visible=False)
    return _svg(fig)


def mix_example() -> dict:
    """Raise containment +10 pts on the four easiest topics; per-topic handed-off CSAT unchanged."""
    easy = {"KYC", "Platform", "Statements", "Product info"}

    def handed_mix(cont):
        w = {t: TOPICS[t][0] * (1 - cont[t]) for t in TOPICS}
        tot = sum(w.values())
        return {t: v / tot for t, v in w.items()}, tot

    before = {t: TOPICS[t][1] for t in TOPICS}
    after = {t: min(0.95, c + 0.10) if t in easy else c for t, c in before.items()}
    mix_a, ho_a = handed_mix(before)
    mix_b, ho_b = handed_mix(after)
    rates = {t: TOPICS[t][3] for t in TOPICS}
    r = mix_shift(rates, mix_a, rates, mix_b)
    r.update(containment_before=1 - ho_a, containment_after=1 - ho_b,
             handed_before=sum(mix_a[t] * rates[t] for t in TOPICS),
             handed_after=sum(mix_b[t] * rates[t] for t in TOPICS))
    return r


CSS = """
:root{--bg:#f6f5f1;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--rule:#e2e1dc;--accent:#2a78d6;--warn:#b45309;--chart:#fcfcfb}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121211;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--rule:#33332f;--accent:#3987e5;--warn:#f59e0b;--chart:#fcfcfb}}
:root[data-theme="dark"]{--bg:#121211;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--rule:#33332f;--accent:#3987e5;--warn:#f59e0b;--chart:#fcfcfb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:920px;margin:0 auto;padding:32px 16px 64px}h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:19px;margin:40px 0 8px;padding-top:16px;border-top:1px solid var(--rule)}p,li{color:var(--ink2);max-width:72ch}
.lede{font-size:16px}.card{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:16px;margin:14px 0;overflow-x:auto}
.chart{background:var(--chart);border-radius:8px;padding:8px}.chart svg{max-width:100%;height:auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:16px 0}
.kpi{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:12px 14px}.kpi b{display:block;font-size:26px;color:var(--ink)}
.kpi span{font-size:13px;color:var(--ink2)}table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--rule);vertical-align:top}th{color:var(--ink2);font-weight:600}
.warn{border-left:3px solid var(--warn)}code{font-size:13px}a{color:var(--accent)}
"""


def build() -> Path:
    ev = json.loads((ROOT / "reports" / "eval_results.json").read_text())
    v1 = json.loads((ROOT / "reports" / "baseline" / "eval_rules_v1.json").read_text())
    s = summarise(simulate())
    mix = mix_example()
    blind = ev["held-out v2 (blind)"]
    g = blind["gates"]
    power = [(d, responses_needed(d, 1.15)) for d in (0.1, 0.2, 0.3)]
    monthly = {"AI-resolved": round(1900 * 0.09), "Handed off": round(3100 * 0.22),
               "One topic's handoffs (Funding)": round(5000 * 0.19 * 0.71 * 0.22)}

    miss_rows = "".join(
        f"<tr><td>{html.escape(m['text'][:90])}</td><td>{html.escape('; '.join(m['problems']))}</td></tr>"
        for m in blind["misses"])
    power_rows = "".join(
        f"<tr><td>{d:.1f} points</td><td>{n:,}</td><td>"
        + ", ".join(f"{k}: {n / v:.1f} months" for k, v in monthly.items()) + "</td></tr>" for d, n in power)

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Handoff Lab Results</title>
<style>{CSS}</style></head><body><main>
<h1>Handoff Lab: results</h1>
<p class="lede">An AI-assistant-to-human handoff for a regulated trading platform's support chat, built end to end
against mocks, then measured honestly. All data is synthetic. Generated by <code>make report</code>.</p>

<div class="kpis">
<div class="kpi"><b>{blind['reach_person']['rate']:.0%}</b><span>blind held-out: reached a person when needed (rules only)</span></div>
<div class="kpi"><b>{g['selfharm_to_vulnerable_recall']['ok']}/{g['selfharm_to_vulnerable_recall']['n']}</b><span>blind self-harm messages routed to the vulnerable-client team</span></div>
<div class="kpi"><b>{s['true_resolution']:.0%}</b><span>true resolution vs {s['containment']:.0%} containment</span></div>
<div class="kpi"><b>{s['abandoned_responses']}/{s['abandoned']}</b><span>queue abandoners who answered the survey</span></div>
</div>

<h2>1. The headline: keyword rules don't generalise</h2>
<p>Rules v1 were tuned on 40 dev conversations and then scored on a held-out set written beforehand: they reached a
person in 37% of the cases that needed one. Rules v2 were rewritten by concept after reading those misses, so the v1 set
is no longer blind (grey). A second held-out set was written after v2 was frozen. On it, v2 reaches
<b>{blind['reach_person']['rate']:.0%}</b>. Blue bars are the honest numbers. Error bars are 95% Wilson intervals.</p>
<div class="card"><div class="chart">{chart_eval(ev, v1)}</div></div>
<div class="card warn"><p><b>What this means for the design.</b> The rules stay, because they are fast, auditable and
cannot be prompt-injected. But they are a floor, not a detector. The model layer (<code>llm_detector.py</code>) can only
<i>add</i> flags, so a wrong or manipulated model can at worst send someone to a person. It is scored with
<code>make eval-llm</code> when an API key is present. It hasn't been run in this repository, so no number is claimed
for it. Until a model layer clears the self-harm and fraud gates on blind data, a visible "talk to a person" option
on every turn is the safety net.</p></div>
<details class="card"><summary><b>Every blind held-out case with a problem ({len(blind['misses'])})</b></summary>
<table><tr><th>Message</th><th>Problem</th></tr>{miss_rows}</table></details>

<h2>2. Containment is not resolution</h2>
<p>The simulated month is calibrated to the data pack (5,000 chats, 38% contained). The brief says 12% of contained
chats come back within 48 hours. Counting those as resolved overstates the AI by about four points of all chats.</p>
<div class="card"><div class="chart">{chart_resolution(s)}</div></div>

<h2>3. The survey flatters both cohorts</h2>
<p>About 9% of AI-resolved and 22% of handed-off clients answer. Clients who abandon the queue almost never answer:
{s['abandoned_responses']} of {s['abandoned']} in this simulation. So the dashboard reads high. The orange bars use every
chat's (simulated) score. The gap is the part of client experience that CSAT can't see, which is why re-contact
and abandonment are tracked alongside it as behavioural metrics.</p>
<div class="card"><div class="chart">{chart_csat(s)}</div></div>

<h2>4. Mix shift: handed-off CSAT can fall when nothing got worse</h2>
<p>Suppose containment rises 10 points on the four easiest topics (KYC, Platform, Statements, Product info), and no
agent changes anything. Overall containment goes from {mix['containment_before']:.1%} to {mix['containment_after']:.1%}. The
chats left for humans are now harder, so handed-off CSAT moves from {mix['handed_before']:.2f} to {mix['handed_after']:.2f}
(<b>{mix['total']:+.3f}</b>): {mix['mix_effect']:+.3f} from mix and {mix['rate_effect']:+.3f} from performance. Without the
decomposition, that dip would be blamed on the handoff.</p>

<h2>5. How long before a CSAT change is even measurable?</h2>
<p>Survey responses needed <b>per arm</b> to detect a change at 95% confidence with 80% power (SD ≈ 1.15 from the
simulation), compared with the responses one month produces at today's volumes.</p>
<div class="card"><table><tr><th>Change to detect</th><th>Responses per arm</th><th>Months to collect</th></tr>
{power_rows}</table></div>
<p>A 0.3-point move in handed-off CSAT is readable within a month. The same move for one topic's handoffs takes
about two months. A 0.1-point move takes a quarter across all handoffs and about a year for AI-resolved chats. So the primary success metrics are behavioural: re-ask
rate, re-contact, abandonment, time to first human reply. CSAT is a guardrail that must not fall.</p>

<h2>6. What the human agent sees</h2>
<p>The demo runs the six case conversations end to end through the mock assistant platform, the bridge and mock
Genesys. See <a href="agent_view.html">agent_view.html</a> and <a href="demo_transcripts.md">demo_transcripts.md</a>.</p>
</main></body></html>"""
    out = ROOT / "reports" / "index.html"
    out.write_text(page)
    return out


if __name__ == "__main__":
    print("wrote", build())
