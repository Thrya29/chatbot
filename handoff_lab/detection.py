"""Signal detection: what a support assistant must notice before it answers.

Design choices (see docs/design.md):
  * complaint, vulnerability, fraud and urgency are tuned for RECALL: a miss is a
    conduct failure, a false alarm costs a human a few seconds.
  * irritation is tuned for PRECISION and never routes on its own: telling a calm
    client "you seem upset" is itself irritating.
  * this layer is deterministic and auditable. An optional LLM layer
    (llm_detector.py) can add recall on paraphrases; it may only ADD flags,
    never remove one raised here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


# --- v2 patterns. Grouped by CONCEPT, not copied from test phrases (see docs/design.md).
_HUMAN_NOUN = r"(?:a |an |the |your |real |actual |some )*(?:human|person|someone|somebody|agent|advisor|adviser|manager|team|representative|operator|customer service)s?"
ASKED_FOR_HUMAN = _rx(
    rf"\b(?:speak|talk|chat|get through)(?:ing)? (?:to|with) {_HUMAN_NOUN}\b",
    rf"\b(?:want|need|get me|give me|put me through to|connect me (?:to|with)|transfer me to)\s+{_HUMAN_NOUN}\b",
    r"\bput me through\b", r"\bcustomer services?\b", r"\b(?:real|actual) (?:human|person)\b",
    r"\bis there (?:a|an) (?:actual |real )?(?:human|person)\b",
    r"\b(?:someone|somebody) (?:real|human|from (?:your team|support|the team))\b",
    r"\b(?:call|phone|ring) (?:me|someone|somebody)\b", r"\bmessage me back\b",
    r"^\W*(?:a |an )?(?:human|person|agent|operator|representative)s?\b(?:\W+\w+){0,2}\W*$",
)
EXPLICIT_COMPLAINT = _rx(r"\bcomplain(?:t|ts|ing)?\b", r"\bformal complaint\b", r"\bescalate this\b",
                         r"\b(?:raise|open|file) a (?:complaint|dispute)\b", r"\bdispute\b",
                         r"\b(?:want|need|demand)(?:ing)? (?:a |my |that |it |the money )?refund(?:ed)?\b",
                         r"\brefund (?:me|my)\b")
LEGAL_REGULATOR = _rx(
    r"\bfca\b", r"\bfinancial conduct authority\b", r"\bregulator\b", r"\bombudsman\b", r"\bsolicitor\b",
    r"\blawyer\b", r"\blegal action\b", r"\bsue\b", r"\bcourt\b", r"\bfos\b", r"\breport(?:ing)? you\b",
)
DISSATISFACTION = _rx(
    r"\bunacceptable\b", r"\bdisgrace(?:ful)?\b", r"\btheft\b", r"\bstole\b", r"\brobbed\b", r"\bscam\b",
    r"\bripped off\b", r"\brip[- ]off\b", r"\buseless\b", r"\bridiculous\b", r"\bappalling\b",
    r"\b(?:terrible|poor|awful|bad) service\b", r"\breally poor\b", r"\bworst\b(?! case)", r"\bthis is a joke\b",
    r"\bnot good enough\b", r"\bkeep telling me the same\b", r"\bsame thing again\b", r"\bfed up\b",
    r"\bshocking\b", r"\bunbelievable\b", r"\blet down\b", r"\bnot happy\b", r"\bunhappy\b",
    r"\bthanks for nothing\b", r"\bcompensat(?:e|ion)\b", r"\bnever (?:got|received) (?:a )?(?:reply|response)\b",
    r"\bspeak to (?:a|your) manager\b", r"\bcost me\b",
)
# Self-harm is NEVER suppressed by negation: "I'm not going to hurt myself" still reaches a
# person. That false positive is accepted on purpose (see evals results).
SELF_HARM = _rx(
    r"\bend (?:it all|my life)\b", r"\bkill(?:ing)? myself\b", r"\btake my (?:own )?life\b",
    r"\bdon'?t want to (?:live|be here|be alive|wake up)\b", r"\bsuicid",
    r"\b(?:no|what'?s the|whats the) point (?:in |of )?(?:living|going on|being alive|being here)\b",
    r"\bnot worth living\b", r"\bhurt(?:ing)? myself\b", r"\bbetter off without me\b", r"\bself[- ]harm",
)
# Vulnerability, grouped by the four FCA drivers (FG21/1): health, life events, resilience, capability.
VULNERABILITY = _rx(
    # health
    r"\bin hospital\b", r"\bdiagnosed\b", r"\bchemo", r"\bcancer\b", r"\billness\b", r"\bmental health\b",
    r"\bdepress", r"\banxi(?:ety|ous)\b", r"\bpanic(?:king)?\b", r"\bcan'?t sleep\b", r"\bdementia\b",
    # life events
    r"\bpassed away\b", r"\b(?:died|bereave)", r"\bexecutor\b", r"\bredundan", r"\blost my job\b", r"\bdivorce",
    # resilience
    r"\b(?:pay|afford)(?:ing)? (?:my |the )?(?:rent|bills|mortgage)\b", r"\bfor (?:my |the )?(?:rent|mortgage|bills)\b",
    r"\b(?:rent|mortgage) payment\b", r"\bcan'?t afford\b", r"\bin (?:a lot of )?debt\b", r"\bmy debts?\b",
    r"\bborrowed\b", r"\b(?:loan|credit card) to (?:fund|trade|deposit)\b", r"\bhardship\b",
    r"\bstruggling (?:financially|to cope|with money|to pay)\b", r"\bdesperate\b", r"\bat breaking point\b",
    # loss of control / harm from trading
    r"\bcan'?t cope\b", r"\blost (?:nearly |almost )?everything\b", r"\beverything(?:'?s| is) gone\b",
    r"\bcan'?t (?:stop|control)\b", r"\baddict", r"\bgambl", r"\bblock me from\b", r"\bself[- ]exclu",
    r"\bdon'?t know what to do anymore\b",
    r"\b(?:wife|husband|partner|family) (?:doesn'?t|does not|don'?t|do not) know\b",
    r"\btell my (?:family|wife|husband|partner|kids)\b",
    # capability
    r"\bnot my first language\b", r"\bi'?m (?:[89]\d|1[01]\d)\b", r"\b(?:[89]\d) years old\b",
    r"\bpower of attorney\b", r"\b(?:grandson|granddaughter|son|daughter|carer) (?:usually )?helps me\b",
)
FRAUD = _rx(
    r"\bunrecogni[sz]ed\b", r"\bunauthori[sz]ed\b", r"\bdon'?t recogni[sz]e\b",
    r"\b(?:did(?:n'?t| not)|never) (?:make|made|request|requested|authori[sz]e|authori[sz]ed|place|placed)\b",
    r"\bnot me\b", r"\bwasn'?t me\b", r"\bhacked\b", r"\bcompromised\b", r"\bfraud",
    r"\bwithout my (?:permission|consent|knowledge)\b",
    r"\bsomeone (?:else )?(?:has )?(?:accessed|logged into|took|taken|changed|moved)\b",
    r"\b(?:asked|wanted) (?:me )?for my (?:login|password|code|pin|one[- ]time)",
    r"\bsaid (?:he|she|they)(?:'?s| is| are| was| were) from (?:your|the) (?:company|team|bank)\b",
    r"\bstolen account\b",
)
ACCESS = _rx(
    r"\blost (?:my )?phone\b", r"\bnew phone\b", r"\bauthenticator\b", r"\b2fa\b", r"\btwo[- ]factor\b",
    r"\blocked out\b", r"\bcan'?t (?:log|sign) ?in\b", r"\bcan'?t get into\b", r"\binvalid code\b",
    r"\b(?:sms|verification|login) code (?:never|doesn'?t|isn'?t|not)\b",
)
URGENCY = _rx(
    r"\bmarket opens?\b", r"\bin \d+ ?(?:min|mins|minutes)\b", r"\bopen positions?\b",
    r"\bmargin call\b", r"\babout to be closed\b", r"\bclose(?:d)? out\b", r"\bright now\b",
    r"\burgent(?:ly)?\b", r"\basap\b",
)
IRRITATION_STRONG = _rx(r"!{2,}", r"\?{2,}", r"\bseriously\b", r"\bcome on\b", r"\bfor god'?s sake\b")
CAPS_WORD = re.compile(r"\b[A-Z]{3,}\b")
ADVICE = _rx(
    r"\bshould i (?:buy|sell|close|hold|open|keep|cut)\b", r"\bis it a good time to\b",
    r"\bwhat would you do\b", r"\bdo you think\b",
    r"\bwill (?:the )?(?:market|price|it|\w+) (?:go|rise|fall|recover|drop)\b",
)
NEGATION_BEFORE = re.compile(r"(?:\bnot|n't|\bnever|\bno)\s+(?:\w+\s+)?$", re.IGNORECASE)

# The rules are English-only. Anything clearly in another language goes to a person rather
# than being guessed at: an English keyword list silently misses "Ich wurde gehackt".
_STOP = {
    "en": {"the", "i", "my", "is", "to", "a", "and", "you", "it", "of", "in", "me", "have", "can", "not", "this"},
    "es": {"quiero", "hablar", "con", "una", "el", "la", "mi", "cuenta", "por", "favor", "que", "es", "dinero", "persona", "y"},
    "fr": {"je", "veux", "une", "le", "la", "mon", "compte", "est", "pas", "plainte", "avec", "de", "déposer", "argent"},
    "de": {"ich", "wurde", "mein", "konto", "nicht", "ist", "und", "gehackt", "bitte", "geld", "mit", "der", "die"},
}


def guess_language(text: str) -> str:
    words = re.findall(r"[a-zà-ÿß']+", text.lower())
    if len(words) < 2:
        return "en"
    scores = {lang: sum(w in s for w in words) for lang, s in _STOP.items()}
    best = max(scores, key=scores.get)
    return best if best != "en" and scores[best] >= 2 and scores[best] > scores["en"] else "en"


TOPIC_RULES = [
    ("unauthorised_activity", FRAUD),
    ("withdrawal_howto", _rx(r"\bhow (?:do|can|would) i (?:withdraw|add|change|set up)\b", r"\bdifferent bank account\b", r"\bnew bank account\b")),
    ("withdrawal_not_received", _rx(r"\bwithdraw", r"\bmoney (?:hasn'?t|has not|not) (?:arrived|come)", r"\bwhere is my money\b", r"\bpayout\b")),
    ("deposit", _rx(r"\bdeposit")),
    ("close_out_dispute", _rx(r"\bclosed my position\b", r"\bclose(?:d)? out\b", r"\bstopped out\b", r"\bslippage\b", r"\bworst possible price\b")),
    ("account_access", ACCESS),
    ("kyc", _rx(r"\bverif", r"\bkyc\b", r"\bdocuments? (?:rejected|declined)\b", r"\bproof of (?:address|id)\b")),
    ("platform", _rx(r"\bapp (?:crash|won'?t|keeps)\b", r"\bplatform\b", r"\bchart", r"\berror\b")),
]


@dataclass
class Detection:
    flags: set[str] = field(default_factory=set)
    evidence: dict[str, str] = field(default_factory=dict)
    topic: str = "other"
    lang: str = "en"

    def add(self, flag: str, match: re.Match | None = None, text: str = "") -> None:
        self.flags.add(flag)
        if match is not None:
            self.evidence.setdefault(flag, match.group(0))
        elif text:
            self.evidence.setdefault(flag, text)


def detect(message: str, history: list[str] | None = None, same_issue_contacts: int = 0) -> Detection:
    """Detect signals in the latest client message, using earlier turns for repeats.

    same_issue_contacts: earlier contacts about the same issue (e.g. same withdrawal ID
    in the last 7 days), as a CRM or data connector would report it.
    """
    history = history or []
    d = Detection()
    text = message

    for flag, rx in (
        ("asked_for_human", ASKED_FOR_HUMAN),
        ("self_harm_risk", SELF_HARM),
        ("possible_vulnerability", VULNERABILITY),
        ("suspected_unauthorised", FRAUD),
        ("account_access", ACCESS),
        ("time_critical", URGENCY),
        ("advice_request", ADVICE),
    ):
        m = rx.search(text)
        if m:
            d.add(flag, m)

    m_explicit, m_legal, m_dis = EXPLICIT_COMPLAINT.search(text), LEGAL_REGULATOR.search(text), DISSATISFACTION.search(text)
    # "I'm not complaining" is not a complaint. Negation only ever relaxes the complaint
    # family; it never relaxes self-harm, vulnerability or fraud.
    if m_explicit and NEGATION_BEFORE.search(text[max(0, m_explicit.start() - 20):m_explicit.start()]):
        m_explicit = None
    if m_legal:
        d.add("legal_mention", m_legal)
        d.add("complaint", m_legal)
    if m_explicit:
        d.add("complaint", m_explicit)
    if m_dis and "complaint" not in d.flags:
        d.add("possible_complaint", m_dis)

    d.lang = guess_language(text)
    if d.lang != "en":
        d.add("non_english", text=d.lang)

    if "self_harm_risk" in d.flags:
        d.add("possible_vulnerability", text="self-harm language")

    # Irritation: precision-tuned. Needs a strong marker, or several shouted words.
    m_irr = IRRITATION_STRONG.search(text)
    caps = [w for w in CAPS_WORD.findall(text) if w not in {"FCA", "FOS", "KYC", "ISA", "CFD", "ID", "GBP", "USD", "EUR", "UK", "NOW"}]
    if m_irr or len(caps) >= 2 or ("NOW" in text and len(text) < 60):
        d.add("frustrated", m_irr, text=" ".join(caps) or "NOW")
    if m_dis and "frustrated" not in d.flags:
        d.add("frustrated", m_dis)

    topic_rx = None
    for topic, rx in TOPIC_RULES:
        if rx.search(text) or any(rx.search(h) for h in history[-3:]):
            d.topic, topic_rx = topic, rx
            break

    # Repeat contact: earlier turns or contacts about the same issue.
    prior_same = same_issue_contacts + (sum(1 for h in history if topic_rx.search(h)) if topic_rx else 0)
    if prior_same >= 2:
        d.add("repeat_contact", text=f"{prior_same} earlier contacts on this issue")
    return d
