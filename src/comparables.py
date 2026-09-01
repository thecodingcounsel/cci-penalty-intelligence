"""Comparable-case engine for CCI Penalty Intelligence.

Scores every case against a base case (an existing precedent, or a
lawyer-built hypothetical fact pattern) using a transparent, weighted sum
of explainable factors - never an opaque single similarity number. Every
score comes with a plain-English reasons list a lawyer can read and
challenge.

A factor only contributes to a case's score when it can actually be
evaluated on BOTH sides (base and candidate) - a case is never penalised
for a field neither side has data on. `max_points` on the result tells you
how much of the full weight scheme was actually applicable, so a case
matched on 90/100 available points is shown honestly, not diluted by
fields nobody could fill in.
"""

from __future__ import annotations

import re

import pandas as pd

# (field, weight) - tune here, not scattered through the scoring logic.
WEIGHTS = {
    "conduct_type": 25,
    "transaction_type": 15,
    "closed_before_approval": 10,
    "voluntary_disclosure": 10,
    "cooperation": 10,
    "delay_duration_days": 10,
    "penalty_43a": 10,
    "factors_overlap": 5,
    "time_period": 5,
}

_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "with", "by", "was", "were",
    "is", "are", "that", "this", "as", "at", "be", "has", "have", "had", "which", "commission",
    "act", "section", "party", "parties",
}


def build_case_profile(row: pd.Series) -> dict:
    """Extract the fields comparables scoring needs from a merged orders+intelligence row."""
    return {
        "order_id": row.get("order_id"),
        "case_name": row.get("case_name"),
        "decision_date": row.get("decision_date"),
        "conduct_type": _clean(row.get("conduct_type")),
        "transaction_type": _clean(row.get("transaction_type")),
        "closed_before_approval": _clean(row.get("closed_before_approval")),
        "voluntary_disclosure": _clean(row.get("voluntary_disclosure")),
        "cooperation": _clean(row.get("cooperation")),
        "delay_duration_days": _num(row.get("delay_duration_days")),
        "penalty_43a": _num(row.get("penalty_43a")),
        "mitigating_factors": _clean(row.get("mitigating_factors")),
        "aggravating_factors": _clean(row.get("aggravating_factors")),
    }


def score_pair(base: dict, candidate: dict) -> tuple[float, int, int, list[str]]:
    """Compare two case profiles (dicts with the same keys as build_case_profile).

    Returns (score_0_to_100, points_earned, max_points_applicable, reasons).
    """
    earned = 0.0
    max_points = 0
    reasons: list[str] = []

    for field in ("conduct_type", "transaction_type", "closed_before_approval",
                  "voluntary_disclosure", "cooperation"):
        b, c = base.get(field), candidate.get(field)
        if not b or not c:
            continue
        weight = WEIGHTS[field]
        max_points += weight
        label = _field_label(field)
        if b == c:
            earned += weight
            reasons.append(f"✓ Same {label} ({b})")
        else:
            reasons.append(f"✗ Different {label} ({c} vs {b})")

    for field in ("delay_duration_days", "penalty_43a"):
        b, c = base.get(field), candidate.get(field)
        if b is None or c is None:
            continue
        weight = WEIGHTS[field]
        max_points += weight
        frac = _proximity(b, c)
        earned += weight * frac
        label = _field_label(field)
        if frac >= 0.7:
            reasons.append(f"✓ Similar {label}")
        elif frac >= 0.35:
            reasons.append(f"~ Somewhat similar {label}")
        else:
            reasons.append(f"✗ Different {label}")

    b_year = _year(base.get("decision_date"))
    c_year = _year(candidate.get("decision_date"))
    if b_year and c_year:
        weight = WEIGHTS["time_period"]
        max_points += weight
        frac = max(0.0, 1 - abs(b_year - c_year) / 6)
        earned += weight * frac
        if frac >= 0.7:
            reasons.append("✓ Similar time period")
        elif frac < 0.35:
            reasons.append("✗ Different time period")

    # filter(None, ...) drops None/empty entries before joining - naively f-string-interpolating
    # a None value would otherwise produce the literal text "None", which tokenizes as a real
    # word and spuriously inflates overlap between two cases with no actual factor data.
    base_factors = " ".join(filter(None, [base.get("mitigating_factors"), base.get("aggravating_factors")]))
    candidate_factors = " ".join(filter(None, [candidate.get("mitigating_factors"), candidate.get("aggravating_factors")]))
    factor_overlap = _text_overlap(base_factors, candidate_factors)
    if factor_overlap is not None:
        weight = WEIGHTS["factors_overlap"]
        max_points += weight
        earned += weight * factor_overlap
        if factor_overlap >= 0.3:
            reasons.append("✓ Overlapping mitigating/aggravating factors")
        else:
            reasons.append("✗ Different aggravating/mitigating factors")

    score = round(100 * earned / max_points, 1) if max_points > 0 else 0.0
    return score, round(earned), max_points, reasons


def find_comparables(
    base: dict, corpus: pd.DataFrame, exclude_order_ids: set | None = None, top_n: int = 10
) -> pd.DataFrame:
    """Score every case in `corpus` (a merged orders+intelligence DataFrame) against `base`.

    Returns the top_n matches sorted by score, as a DataFrame with columns:
    order_id, case_name, decision_date, penalty_43a, total_penalty, comparable_score,
    points_earned, points_possible, reasons (list[str]).
    """
    exclude_order_ids = exclude_order_ids or set()
    results = []
    for _, row in corpus.iterrows():
        if row.get("order_id") in exclude_order_ids or row.get("order_id") == base.get("order_id"):
            continue
        candidate = build_case_profile(row)
        score, earned, possible, reasons = score_pair(base, candidate)
        if possible == 0:
            continue  # nothing comparable could even be evaluated - not a meaningful match
        results.append({
            "order_id": row.get("order_id"),
            "case_name": row.get("case_name"),
            "decision_date": row.get("decision_date"),
            "penalty_43a": row.get("penalty_43a"),
            "total_penalty": row.get("total_penalty"),
            "source_url": row.get("source_url"),
            "comparable_score": score,
            "points_earned": earned,
            "points_possible": possible,
            "reasons": reasons,
        })

    if not results:
        return pd.DataFrame(columns=[
            "order_id", "case_name", "decision_date", "penalty_43a", "total_penalty",
            "source_url", "comparable_score", "points_earned", "points_possible", "reasons",
        ])

    df = pd.DataFrame(results).sort_values("comparable_score", ascending=False)
    return df.head(top_n).reset_index(drop=True)


def _field_label(field: str) -> str:
    return {
        "conduct_type": "conduct type",
        "transaction_type": "transaction type",
        "closed_before_approval": "closing-before-approval status",
        "voluntary_disclosure": "voluntary disclosure status",
        "cooperation": "cooperation level",
        "delay_duration_days": "delay period",
        "penalty_43a": "penalty magnitude",
    }.get(field, field)


def _proximity(a: float, b: float) -> float:
    """1.0 = identical, decaying toward 0.0 as the two values diverge (relative to the larger)."""
    denom = max(abs(a), abs(b), 1.0)
    return max(0.0, 1 - abs(a - b) / denom)


def _text_overlap(a: str, b: str) -> float | None:
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a or not tokens_b:
        return None
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else None


def _tokenize(text: str) -> set:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return {w for w in words if len(w) > 3 and w not in _STOPWORDS}


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def _num(value) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def _year(date_value) -> int | None:
    """pd.Timestamp(None) returns NaT rather than raising, and NaT.year is nan - which is
    truthy in Python, so a caller doing `if year:` would wrongly treat a missing date as
    present. Explicitly return None for that case instead.
    """
    if date_value is None:
        return None
    try:
        ts = pd.Timestamp(date_value)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(ts) else ts.year
