"""Objective field extraction from CCI order text - no LLM, no inference.

This module only extracts a penalty figure when it can do so unambiguously.
For a single, cleanly-listed provision (e.g. "43A"): exactly one "penalty of
INR/Rs. <amount>" figure in the text, or an explicit statement that no
penalty was imposed. For a compound listing (e.g. "43A and 44"): only when
the order text itself explicitly ties a figure to a specific section (see
`_extract_compound_provision_penalties`). Anything less clear-cut is left
blank with the reason recorded, per the "do not guess" rule - a lawyer
reviewing a blank cell should be able to see immediately why it's blank.

CCI orders state a penalty figure in a few different ways, e.g.:
  "penalty of INR 40,00,000"                                 (plain numeral)
  "penalty of INR Twenty Lakhs (INR 20,00,000/-)"             (words, then a precise numeral in parens)
  "penalty of INR 5 Crore (INR 5,00,00,000/- only)"           (mixed digit+word, then a precise numeral)
  "penalty of Rs. 5,00,000/- (Rupees Five Lacs Only)"         ("Rs." instead of "INR")
  "penalty of INR Twenty Lakh on X."                          (word-only, no numeral restatement at all)
  "...Rs. 5,00,000 (INR Five Lakh) under Section 43A ..."     (compound order, explicit per-section split)
  "...INR One Crore each under ... Section 44 and Section 45" (compound order, one figure shared by two sections)
A numeral is always preferred over a word-form when both are present. A
word-only amount is only accepted when `words_to_number` can parse it
without hitting anything it doesn't recognise - see that function's
docstring for exactly how conservative it is.
"""

from __future__ import annotations

import re

CURRENCY = r"(?:INR|Rs\.?)"
SECTION_NUM = r"(43A|44|45)"

# Anchors on "penalty of <currency>", then skips over an optional word/mixed-form amount
# (e.g. "Twenty Lakhs " or "5 Crore ") to find the first plain numeral of at least 4 digits -
# which is always the precise parenthetical restatement when one is present, or the number
# itself when the order states it as a plain digit figure with no restatement at all.
PENALTY_AMOUNT_PATTERN = re.compile(
    rf"penalty\s+of\s+{CURRENCY}\s+"
    rf"(?:(?!\b(?:INR|Rs)\b)[^(]){{0,60}}?"
    rf"\(?\s*(?:{CURRENCY}\s*)?"
    r"([\d,]{4,})",
    re.IGNORECASE,
)
# Fallback for when no numeral exists anywhere near "penalty of <currency>" - captures the
# word phrase itself (e.g. "Twenty Lakh on Investcorp India") for words_to_number() to parse.
PENALTY_WORD_PATTERN = re.compile(
    rf"penalty\s+of\s+{CURRENCY}\s+([^.]{{1,60}})", re.IGNORECASE
)
ZERO_PENALTY_PATTERN = re.compile(
    r"decides?\s+not\s+to\s+impose\s+any\s+penalty", re.IGNORECASE
)
# Compound-order pattern 1: a numeral amount stated immediately before "under Section X
# [and Section Y] of [the] Act" - the sentence itself ties the figure to a section.
NUMERAL_BEFORE_SECTION_PATTERN = re.compile(
    rf"{CURRENCY}\s*"
    rf"(?:(?!\b(?:INR|Rs)\b)[^(]){{0,60}}?"
    rf"\(?\s*(?:{CURRENCY}\s*)?"
    r"([\d,]{4,})"
    rf"\)?[^.]{{0,40}}?under\s+(?:the\s+)?(?:provisions?\s+of\s+)?"
    rf"Section\s+{SECTION_NUM}(?:\s+and\s+Section\s+{SECTION_NUM})?\s+of\s+(?:the\s+)?Act",
    re.IGNORECASE,
)
# Compound-order pattern 2: a word-only amount shared by two sections via "... <amount> each
# under [the provisions of] Section X and Section Y of [the] Act" - only trusted because of
# the explicit "each", which states in the text itself that both sections get the same figure.
WORD_EACH_BEFORE_SECTION_PATTERN = re.compile(
    rf"{CURRENCY}\s+([A-Za-z][A-Za-z\s]{{0,40}}?)\s*each\s+under\s+(?:the\s+)?(?:provisions?\s+of\s+)?"
    rf"Section\s+{SECTION_NUM}(?:\s+and\s+Section\s+{SECTION_NUM})?\s+of\s+(?:the\s+)?Act",
    re.IGNORECASE,
)

KNOWN_PROVISIONS = ("43A", "44", "45")
PENALTY_COLUMN_BY_PROVISION = {
    "43A": "penalty_43a",
    "44": "penalty_44",
    "45": "penalty_45",
}
CONTEXT_CHARS = 150

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {
    "hundred": 100, "thousand": 1_000,
    "lakh": 1_00_000, "lakhs": 1_00_000, "lac": 1_00_000, "lacs": 1_00_000,
    "crore": 1_00_00_000, "crores": 1_00_00_000,
}
_WORD_FILLER = {"rupees", "rupee", "only", "inr", "rs", "and", "of"}


def words_to_number(phrase: str) -> float | None:
    """Conservatively parse an Indian-English number phrase (e.g. "Two Hundred Crore",
    "Twenty Lakh", "Rupees Five Lacs Only") into a number.

    Reads word tokens left to right and stops at the first token it doesn't recognise,
    returning whatever valid amount was accumulated up to that point (so trailing text
    like "... upon Amazon" or "... on Investcorp India" is safely ignored rather than
    causing a parse failure). Returns None if it recognises no number words at all -
    it never invents a figure from unrecognised text.
    """
    tokens = re.findall(r"[A-Za-z]+", phrase.lower())
    total = 0
    current = 0
    seen_number_word = False
    for tok in tokens:
        if tok in _WORD_FILLER:
            continue
        if tok in _UNITS:
            current += _UNITS[tok]
            seen_number_word = True
        elif tok in _TENS:
            current += _TENS[tok]
            seen_number_word = True
        elif tok == "hundred":
            current = (current or 1) * 100
            seen_number_word = True
        elif tok in _SCALES:
            total += (current or 1) * _SCALES[tok]
            current = 0
            seen_number_word = True
        else:
            break  # unrecognised word - stop here, keep whatever valid prefix was parsed
    total += current
    return float(total) if seen_number_word else None


def extract_penalty_info(text: str, page_texts: list[str], provision_hint: str) -> dict:
    """Extract penalty figures from order text.

    `provision_hint` is the section(s) as officially listed by CCI for this
    order (e.g. "43A", or "43A and 44"). For a single listed section, this
    function decides *how much* penalty applies to it. For a compound
    listing, it only fills in a section's column when the order text itself
    explicitly ties a figure to that section - see
    `_extract_compound_provision_penalties`.

    Returns a dict with keys penalty_43a, penalty_44, penalty_45,
    total_penalty (each float or None), penalty_source_page (int or None),
    penalty_source_text (str), and issues (list[str]).
    """
    result = {
        "penalty_43a": None,
        "penalty_44": None,
        "penalty_45": None,
        "total_penalty": None,
        "penalty_source_page": None,
        "penalty_source_text": "",
        "issues": [],
    }

    provisions = [p for p in KNOWN_PROVISIONS if p in (provision_hint or "")]
    if not provisions:
        result["issues"].append(
            f"provision listed as {provision_hint!r} does not name a recognised section (43A/44/45)"
        )
        return result

    # PDF text extraction inserts line breaks at arbitrary layout points (sometimes
    # splitting "INR Twenty Lakhs" from its "(INR 20,00,000/-)" restatement across a
    # line) - normalize whitespace once so patterns can match across those breaks.
    normalized = re.sub(r"\s+", " ", text)

    if len(provisions) == 1:
        return _extract_single_provision_penalty(normalized, provisions[0], page_texts, result)

    return _extract_compound_provision_penalty(normalized, provisions, provision_hint, page_texts, result)


def _extract_single_provision_penalty(normalized: str, provision: str, page_texts: list[str], result: dict) -> dict:
    column = PENALTY_COLUMN_BY_PROVISION[provision]

    matches = list(PENALTY_AMOUNT_PATTERN.finditer(normalized))
    distinct_amounts = {m.group(1) for m in matches}

    if len(distinct_amounts) == 1:
        match = matches[0]
        amount = _parse_indian_amount(match.group(1))
        if amount is None:
            result["issues"].append(f"found penalty text {match.group(0)!r} but could not parse the amount")
            return result
        return _fill_single(result, column, amount, normalized, match.start(), match.end(), page_texts)

    if len(distinct_amounts) > 1:
        result["issues"].append(
            f"found {len(distinct_amounts)} distinct penalty figures in the text ({sorted(distinct_amounts)}) - "
            "ambiguous which one is the operative penalty, left blank for manual review"
        )
        return result

    # No numeral figure at all - check for an explicit zero-penalty finding next.
    zero_match = ZERO_PENALTY_PATTERN.search(normalized)
    if zero_match:
        return _fill_single(result, column, 0.0, normalized, zero_match.start(), zero_match.end(), page_texts)

    # Last resort: a word-only amount with no numeral restatement anywhere (e.g. "penalty
    # of INR Twenty Lakh on X."). Only accepted if exactly one clause parses unambiguously.
    word_matches = list(PENALTY_WORD_PATTERN.finditer(normalized))
    parsed = [(m, words_to_number(m.group(1))) for m in word_matches]
    parsed = [(m, amt) for m, amt in parsed if amt is not None]
    distinct_word_amounts = {amt for _, amt in parsed}

    if len(distinct_word_amounts) == 1:
        match, amount = parsed[0]
        return _fill_single(result, column, amount, normalized, match.start(), match.end(), page_texts)

    if len(distinct_word_amounts) > 1:
        result["issues"].append(
            f"found {len(distinct_word_amounts)} distinct word-form penalty amounts in the text "
            f"({sorted(distinct_word_amounts)}) - ambiguous, left blank for manual review"
        )
        return result

    result["issues"].append(
        "no 'penalty of INR/Rs. ...' figure (numeral or word-form) and no explicit 'decides not to "
        "impose any penalty' statement found - could not determine the penalty outcome, left blank for manual review"
    )
    return result


def _extract_compound_provision_penalty(
    normalized: str, provisions: list[str], provision_hint: str, page_texts: list[str], result: dict
) -> dict:
    resolved, spans = _extract_compound_provision_penalties(normalized, provisions)

    if not resolved:
        result["issues"].append(
            f"provision listed as {provision_hint!r} is compound and the order text does not explicitly "
            "attribute a penalty figure to a single section - left blank for manual review"
        )
        return result

    for section, amount in resolved.items():
        column = PENALTY_COLUMN_BY_PROVISION.get(section)
        if column:
            result[column] = amount

    result["total_penalty"] = sum(resolved.values())

    # Provenance: prefer the excerpt for a section actually named in the official listing,
    # falling back to whichever section was resolved first.
    primary_section = next((p for p in provisions if p in spans), next(iter(spans)))
    start, end = spans[primary_section]
    result["penalty_source_text"] = _context(normalized, start, end)
    result["penalty_source_page"] = _find_page(result["penalty_source_text"], page_texts)

    unresolved = [p for p in provisions if p not in resolved]
    if unresolved:
        result["issues"].append(
            f"order text did not clearly attribute a penalty to section(s) {unresolved} - "
            "those column(s) left blank; other section(s) resolved from explicit text"
        )
    return result


def _extract_compound_provision_penalties(normalized: str, provisions: list[str]) -> tuple[dict, dict]:
    """Find penalty figures the order text itself explicitly attributes to a specific
    section, for a compound-listed order. Only ever populates a section when the
    sentence structure itself makes the attribution clear - never by proximity guessing.

    Returns ({section: amount}, {section: (match_start, match_end)}) - only for
    sections successfully and unambiguously resolved.
    """
    candidates: dict[str, list[tuple[float, tuple[int, int]]]] = {}
    consumed_spans: list[tuple[int, int]] = []

    for m in NUMERAL_BEFORE_SECTION_PATTERN.finditer(normalized):
        amount = _parse_indian_amount(m.group(1))
        if amount is None:
            continue
        for section in (m.group(2), m.group(3)):
            if section:
                candidates.setdefault(section, []).append((amount, m.span()))
        consumed_spans.append(m.span())

    for m in WORD_EACH_BEFORE_SECTION_PATTERN.finditer(normalized):
        amount = words_to_number(m.group(1))
        if amount is None:
            continue
        for section in (m.group(2), m.group(3)):
            if section:
                candidates.setdefault(section, []).append((amount, m.span()))
        consumed_spans.append(m.span())

    resolved: dict[str, float] = {}
    spans: dict[str, tuple[int, int]] = {}
    for section, entries in candidates.items():
        amounts = {a for a, _ in entries}
        if len(amounts) == 1:  # every mention of this section agrees on the same figure
            resolved[section] = amounts.pop()
            spans[section] = entries[0][1]

    # Leftover: a section named in the listing but not yet resolved gets one more chance -
    # if there is exactly one still-unattributed provision AND exactly one standalone
    # "penalty of <currency> ..." clause left in the text (that wasn't already consumed
    # above, and isn't just a restatement of the total already resolved), attribute it.
    unresolved = [p for p in provisions if p not in resolved]
    if len(unresolved) == 1:
        already_resolved_total = sum(resolved.values())
        leftover = []
        for m in PENALTY_WORD_PATTERN.finditer(normalized):
            if any(_spans_overlap(m.span(), s) for s in consumed_spans):
                continue
            amount = _parse_indian_amount(_leading_numeral(m.group(1))) or words_to_number(m.group(1))
            if amount is None:
                continue
            if resolved and amount == already_resolved_total:
                continue  # a confirmatory restatement of the already-resolved total, not a new clause
            leftover.append((amount, m.span()))

        distinct_leftover = {a for a, _ in leftover}
        if len(distinct_leftover) == 1:
            section = unresolved[0]
            resolved[section] = leftover[0][0]
            spans[section] = leftover[0][1]

    return resolved, spans


def _leading_numeral(text: str) -> str | None:
    """Return a leading comma-grouped numeral of >=4 digits at the start of `text`, if any."""
    match = re.match(r"\s*(?:" + CURRENCY + r"\s*)?([\d,]{4,})", text)
    return match.group(1) if match else None


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


def _fill_single(
    result: dict, column: str, amount: float, normalized: str, start: int, end: int, page_texts: list[str]
) -> dict:
    result[column] = amount
    result["total_penalty"] = amount
    result["penalty_source_text"] = _context(normalized, start, end)
    result["penalty_source_page"] = _find_page(result["penalty_source_text"], page_texts)
    return result


def _parse_indian_amount(raw) -> float | None:
    """Convert a comma-grouped rupee figure (Indian or international grouping) to a float."""
    if raw is None:
        return None
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _context(text: str, start: int, end: int) -> str:
    """Return a short whitespace-collapsed excerpt around a match, for human verification."""
    excerpt = text[max(0, start - CONTEXT_CHARS): end + CONTEXT_CHARS]
    return re.sub(r"\s+", " ", excerpt).strip()


def _find_page(excerpt: str, page_texts: list[str]) -> int | None:
    """Locate which page (1-indexed) contains the tail of the excerpt, to cite alongside penalty_source_text."""
    if not excerpt:
        return None
    needle = excerpt[-40:] if len(excerpt) > 40 else excerpt
    needle = needle.strip()
    if not needle:
        return None
    for page_num, page_text in enumerate(page_texts, start=1):
        if needle in re.sub(r"\s+", " ", page_text):
            return page_num
    return None
