"""Legal-factor intelligence extraction from CCI order text - no LLM, no inference.

Same discipline as src/extract_fields.py: every field is filled only when a
specific, identifiable passage in the order text supports it, and every
non-blank field carries a page number and a short quoted excerpt so a
lawyer can verify it against the source PDF in one click. When the text
doesn't clearly support a category, the field is left blank rather than
guessed - a blank cell means "the order didn't make this clear enough to
extract automatically," not "this doesn't apply."

Controlled vocabularies (kept deliberately small so filtering stays useful):

conduct_type: Failure to notify | Delayed notification | Pre-closing
    consummation | Exercise of control before approval | Information /
    integration before approval | False or incomplete information |
    Multiple / mixed conduct | Other

transaction_type: Acquisition | Merger | Amalgamation | Joint Venture |
    Share Subscription | Share Purchase | Other

voluntary_disclosure / cooperation / *_before_approval / *_exercised /
    penalty_reduced: Yes | No (only when the order states it explicitly)
"""

from __future__ import annotations

import re

CONTEXT_CHARS = 200

# --- Controlled-vocabulary conduct classifiers -----------------------------------------
# Each is (label, pattern). Patterns require load-bearing phrasing actually used across the
# corpus (verified against the source texts), not a single loose keyword. Checked in this
# order because every 43A order recites the same "failure to give notice" statutory
# boilerplate regardless of the specific facts, so the generic FALLBACK_CONDUCT pattern
# below is deliberately checked LAST and only used when none of these more specific,
# fact-describing patterns match - otherwise almost every case would collide on the
# boilerplate and get bucketed as "Multiple / mixed conduct".
CONDUCT_PATTERNS = [
    ("Pre-closing consummation", re.compile(
        r"consummat\w*\s+(?:the\s+)?(?:combination|transaction|proposed\s+combination)\s+"
        r"(?:without|prior to|before)|"
        r"(?:closed|closing of)\s+the\s+transaction\s+(?:without|prior to|before)\s+"
        r"(?:notice|approval|notif)", re.IGNORECASE)),
    ("Exercise of control before approval", re.compile(
        r"exercis\w*\s+control\s+(?:over\s+\w+\s+)?(?:prior to|before)|"
        r"control\s+(?:was|had been)\s+exercised\s+(?:prior to|before)", re.IGNORECASE)),
    ("Information / integration before approval", re.compile(
        r"(?:exchange|sharing)\s+of\s+(?:competitively\s+sensitive\s+)?information\s+"
        r"(?:prior to|before)\s+(?:approval|notice)|"
        r"integrat\w+\s+(?:the\s+)?(?:business(?:es)?|operations)\s+(?:prior to|before)\s+approval",
        re.IGNORECASE)),
    ("False or incomplete information", re.compile(
        r"false\s+or\s+incomplete|material\s+particular\w*\s+(?:which\s+)?(?:was|were|is|are)?\s*"
        r"(?:false|incorrect|omitted|not disclosed)|incorrect\s+information\s+(?:in|provided)",
        re.IGNORECASE)),
    ("Delayed notification", re.compile(
        r"(?:delay(?:ed)?|belated(?:ly)?)\s+(?:in\s+)?(?:filing|notif\w+|giving\s+notice)|"
        r"notif\w+\s+(?:was\s+)?filed\s+(?:late|after\s+(?:a\s+)?delay)", re.IGNORECASE)),
]
# Generic fallback: matches the boilerplate "failed to give notice" recital found in nearly
# every 43A order. Only assigned when NO specific pattern above matched anything - see
# build_case_intelligence().
FALLBACK_CONDUCT_LABEL = "Failure to notify"
FALLBACK_CONDUCT_PATTERN = re.compile(
    r"fail\w*\s+to\s+(?:give|file)\s+notice|failure\s+to\s+notify", re.IGNORECASE
)

TRANSACTION_TYPE_PATTERNS = [
    ("Joint Venture", re.compile(r"joint\s+venture", re.IGNORECASE)),
    ("Amalgamation", re.compile(r"amalgamat\w+", re.IGNORECASE)),
    ("Merger", re.compile(r"\bmerger\s+of\b|\bscheme\s+of\s+merger\b", re.IGNORECASE)),
    ("Share Subscription", re.compile(r"subscri\w+\s+(?:to|of)\s+(?:equity\s+)?shares|"
                                       r"subscription\s+agreement", re.IGNORECASE)),
    ("Share Purchase", re.compile(r"share\s+purchase\s+agreement|purchase\s+of\s+shares|"
                                   r"acquisition\s+of\s+\d+(?:\.\d+)?%\s+(?:of\s+)?(?:the\s+)?"
                                   r"(?:equity\s+)?shar", re.IGNORECASE)),
    ("Acquisition", re.compile(r"\bacquisition\s+of\b|\backquir\w+\s+(?:control|shares|stake)\b",
                                re.IGNORECASE)),
]

VOLUNTARY_YES = re.compile(
    r"(?:disclosed|informed|brought\s+to\s+the\s+(?:notice|attention))\s+"
    r"(?:the\s+(?:transaction|combination)\s+)?voluntarily|voluntary\s+disclosure|suo\s+moto|suo\s+motu",
    re.IGNORECASE,
)
VOLUNTARY_NO = re.compile(
    r"did\s+not\s+(?:voluntarily\s+)?disclose|came\s+to\s+(?:the\s+)?(?:knowledge|notice)\s+of\s+"
    r"the\s+Commission\s+(?:through|via|from)\s+(?:a\s+)?(?:complaint|media|third\s+party)",
    re.IGNORECASE,
)
COOPERATION_FULL = re.compile(
    r"(?:extended|provided)\s+(?:full\s+|complete\s+)?cooperation|fully\s+cooperat\w+|"
    r"cooperat\w+\s+throughout", re.IGNORECASE,
)
COOPERATION_PARTIAL = re.compile(r"cooperat\w+", re.IGNORECASE)

LENIENT_PATTERN = re.compile(r"lenient\s+view|nominal\s+penalty", re.IGNORECASE)
MAXIMUM_PATTERN = re.compile(r"maximum\s+penalty|levy\s+the\s+maximum", re.IGNORECASE)

DELAY_PATTERN = re.compile(
    r"delay\s+of\s+(?:about\s+|approximately\s+)?(\d+)\s*(day|month|year)s?", re.IGNORECASE
)
TRANSACTION_VALUE_PATTERN = re.compile(
    r"(?:consideration|transaction\s+value|deal\s+value)\s+of\s+(?:INR|Rs\.?)\s*"
    r"([\d,]+(?:\.\d+)?)\s*(crore|lakh)s?", re.IGNORECASE,
)
CONTROL_RIGHTS_PATTERN = re.compile(
    r"(?:information|veto|minutes?|observer)\s+right\w*\s+(?:was|were|had\s+been)?\s*exercised|"
    r"exercis\w+\s+(?:the\s+)?(?:information|veto|minutes?|observer)\s+right",
    re.IGNORECASE,
)

MITIGATING_ANCHOR = re.compile(r"mitigating\s+factor\w*[^.]{0,250}\.", re.IGNORECASE)
AGGRAVATING_ANCHOR = re.compile(r"aggravating\s+factor\w*[^.]{0,250}\.", re.IGNORECASE)

# The opening framing sentence of a CCI order almost always states the case in one line,
# e.g. "Proceedings under Section 43A of the Competition Act, 2002 ... in relation to notice
# filed by X" or "Proceedings against Y under Section 43A ...". Extractive, not generated.
ISSUE_SUMMARY_PATTERN = re.compile(
    r"(Proceedings\s+(?:under|against)[^.]{10,300}?(?:Act,?\s*2002\)?[^.]{0,150})?)\.", re.IGNORECASE
)


def build_case_intelligence(order_id: str, text: str, page_texts: list[str], penalty_source_text: str) -> dict:
    """Derive the legal_intelligence.csv row for one order from its extracted text.

    Returns a flat dict matching the legal_intelligence.csv schema (see
    src/data.py LEGAL_INTELLIGENCE_COLUMNS). Every field left blank means the
    text didn't clearly support a value - never a guess.
    """
    row = {col: "" for col in LEGAL_INTELLIGENCE_COLUMNS}
    row["order_id"] = order_id

    if not text or not text.strip():
        row["intelligence_issues"] = "no extracted text available (PDF unreadable or scanned)"
        return row

    normalized = re.sub(r"\s+", " ", text)
    issues = []

    conduct_labels = []
    conduct_spans = []
    for label, pattern in CONDUCT_PATTERNS:
        m = pattern.search(normalized)
        if m:
            conduct_labels.append(label)
            conduct_spans.append(m.span())

    if len(conduct_labels) == 1:
        row["conduct_type"] = conduct_labels[0]
        row["gun_jumping_type"] = conduct_labels[0]
        start, end = conduct_spans[0]
    elif len(conduct_labels) > 1:
        row["conduct_type"] = "Multiple / mixed conduct"
        row["gun_jumping_type"] = "; ".join(conduct_labels)
        start, end = conduct_spans[0]
    else:
        fallback = FALLBACK_CONDUCT_PATTERN.search(normalized)
        if fallback:
            row["conduct_type"] = FALLBACK_CONDUCT_LABEL
            row["gun_jumping_type"] = FALLBACK_CONDUCT_LABEL
            start, end = fallback.span()
        else:
            issues.append("no recognised conduct pattern found - conduct_type left blank")
            start, end = None, None

    if start is not None:
        row["notification_issue"] = _sentence_containing(normalized, start, end)

    # transaction_type
    transaction_type_span = None
    for label, pattern in TRANSACTION_TYPE_PATTERNS:
        m = pattern.search(normalized)
        if m:
            row["transaction_type"] = label
            transaction_type_span = m.span()
            break
    else:
        issues.append("no recognised transaction-type pattern found")

    # voluntary_disclosure
    if VOLUNTARY_YES.search(normalized):
        row["voluntary_disclosure"] = "Yes"
    elif VOLUNTARY_NO.search(normalized):
        row["voluntary_disclosure"] = "No"

    # cooperation
    if COOPERATION_FULL.search(normalized):
        row["cooperation"] = "Full"
    elif COOPERATION_PARTIAL.search(normalized):
        row["cooperation"] = "Partial"

    # penalty_reduced
    if LENIENT_PATTERN.search(normalized):
        row["penalty_reduced"] = "Yes"
    elif MAXIMUM_PATTERN.search(normalized):
        row["penalty_reduced"] = "No"

    # mitigating / aggravating factors - extractive sentence(s) around the anchor phrase
    mit = MITIGATING_ANCHOR.search(normalized)
    if mit:
        row["mitigating_factors"] = mit.group(0).strip()
    agg = AGGRAVATING_ANCHOR.search(normalized)
    if agg:
        row["aggravating_factors"] = agg.group(0).strip()

    # delay duration
    delay = DELAY_PATTERN.search(normalized)
    if delay:
        qty, unit = delay.groups()
        days = {"day": 1, "month": 30, "year": 365}[unit.lower()]
        row["delay_duration_days"] = str(int(qty) * days)

    # transaction value
    tv = TRANSACTION_VALUE_PATTERN.search(normalized)
    if tv:
        amount, unit = tv.groups()
        try:
            value = float(amount.replace(",", ""))
            multiplier = 1_00_00_000 if unit.lower() == "crore" else 1_00_000
            row["transaction_value"] = str(value * multiplier)
        except ValueError:
            pass

    # control / information rights exercised before approval
    if CONTROL_RIGHTS_PATTERN.search(normalized):
        row["information_rights_exercised"] = "Yes"

    # derived closed/control-before-approval flags - only when the underlying conduct
    # pattern itself was matched (never inferred independently)
    if "Pre-closing consummation" in conduct_labels:
        row["closed_before_approval"] = "Yes"
    if "Exercise of control before approval" in conduct_labels:
        row["control_exercised_before_approval"] = "Yes"

    # legal issue summary - the order's own opening framing sentence (searched only in the
    # first ~3000 chars, i.e. the caption/recitals, so a later stray "Proceedings..." mention
    # deep in a long order is never picked up instead of the true opening line)
    issue_match = ISSUE_SUMMARY_PATTERN.search(normalized[:3000])
    if issue_match:
        row["legal_issue_summary"] = issue_match.group(1).strip()

    # penalty rationale - reuse the already-extracted penalty clause's surrounding sentence,
    # since it's the same passage a lawyer would want provenance for
    if penalty_source_text:
        row["penalty_rationale"] = penalty_source_text
        row["outcome_summary"] = penalty_source_text

    # shared provenance: prefer the conduct-pattern match, then the penalty clause, then
    # whatever supported transaction_type - so ANY populated interpretive field always has
    # something to verify it against (a lawyer should never see a value with no excerpt).
    if start is not None:
        excerpt = _context(normalized, start, end)
        row["intelligence_source_text"] = excerpt
        row["intelligence_source_page"] = _find_page(excerpt, page_texts)
    elif penalty_source_text:
        row["intelligence_source_text"] = penalty_source_text
        row["intelligence_source_page"] = _find_page(penalty_source_text, page_texts)
    elif transaction_type_span is not None:
        excerpt = _context(normalized, *transaction_type_span)
        row["intelligence_source_text"] = excerpt
        row["intelligence_source_page"] = _find_page(excerpt, page_texts)

    row["intelligence_issues"] = "; ".join(issues)
    return row


def _sentence_containing(text: str, start: int, end: int) -> str:
    """Return the full sentence (period to period) containing a match span."""
    left = text.rfind(".", 0, start)
    right = text.find(".", end)
    left = left + 1 if left != -1 else 0
    right = right if right != -1 else min(len(text), end + CONTEXT_CHARS)
    return text[left:right].strip()


def _context(text: str, start: int, end: int) -> str:
    excerpt = text[max(0, start - CONTEXT_CHARS): end + CONTEXT_CHARS]
    return re.sub(r"\s+", " ", excerpt).strip()


def _find_page(excerpt: str, page_texts: list[str]) -> str:
    if not excerpt or not page_texts:
        return ""
    needle = excerpt[-40:].strip() if len(excerpt) > 40 else excerpt.strip()
    if not needle:
        return ""
    for page_num, page_text in enumerate(page_texts, start=1):
        if needle in re.sub(r"\s+", " ", page_text):
            return str(page_num)
    return ""


LEGAL_INTELLIGENCE_COLUMNS = [
    "order_id",
    "transaction_type",
    "conduct_type",
    "notification_issue",
    "gun_jumping_type",
    "voluntary_disclosure",
    "cooperation",
    "mitigating_factors",
    "aggravating_factors",
    "penalty_rationale",
    "legal_issue_summary",
    "outcome_summary",
    "transaction_value",
    "delay_duration_days",
    "date_of_closing",
    "date_of_notification",
    "closed_before_approval",
    "control_exercised_before_approval",
    "information_rights_exercised",
    "penalty_reduced",
    "intelligence_source_page",
    "intelligence_source_text",
    "intelligence_issues",
]
