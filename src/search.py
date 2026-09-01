"""Corpus search for CCI Penalty Intelligence.

A small, dependency-free TF-IDF-style keyword search over the case corpus -
no vector database, no embeddings API. At 68-a-few-hundred documents this
is plenty: each query re-scores the whole corpus in well under a second,
so there is no index file to keep in sync with the data.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import pandas as pd

SEARCH_FIELDS = [
    "case_name",
    "combination_registration_no",
    "legal_issue_summary",
    "penalty_rationale",
    "notification_issue",
    "conduct_type",
    "mitigating_factors",
    "aggravating_factors",
]

_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "with", "by", "was", "were",
    "is", "are", "that", "this", "as", "at", "be", "has", "have", "had", "which",
}


def search(df: pd.DataFrame, query: str, top_n: int = 15) -> pd.DataFrame:
    """Rank every row in `df` against `query` using TF-IDF over SEARCH_FIELDS.

    Returns a DataFrame (empty if the query is blank or matches nothing) with
    columns: order_id, case_name, decision_date, total_penalty, source_url,
    snippet (a short excerpt from whichever field matched), score.
    """
    query_terms = _tokenize(query)
    if not query_terms:
        return _empty_result()

    docs = []
    for _, row in df.iterrows():
        text = " ".join(str(row.get(f, "")) for f in SEARCH_FIELDS)
        text += " " + _structured_field_phrases(row)
        docs.append(_tokenize(text))

    n_docs = len(docs)
    doc_freq = Counter()
    for terms in docs:
        doc_freq.update(set(terms))

    scores = []
    for terms in docs:
        term_counts = Counter(terms)
        doc_len = max(len(terms), 1)
        score = 0.0
        for q in query_terms:
            if q not in term_counts:
                continue
            tf = term_counts[q] / doc_len
            idf = math.log((n_docs + 1) / (doc_freq.get(q, 0) + 1)) + 1
            score += tf * idf
        scores.append(score)

    results = df.copy()
    results["score"] = scores
    results = results[results["score"] > 0].sort_values("score", ascending=False).head(top_n)

    if results.empty:
        return _empty_result()

    results["snippet"] = results.apply(lambda r: _best_snippet(r, query_terms), axis=1)

    return results[[
        "order_id", "case_name", "decision_date", "total_penalty", "source_url", "snippet", "score",
    ]].reset_index(drop=True)


def _best_snippet(row: pd.Series, query_terms: set) -> str:
    """Pick whichever SEARCH_FIELDS value contains the most query terms, as the result snippet."""
    best_field, best_hits = "", -1
    for field in SEARCH_FIELDS:
        value = str(row.get(field, "")).strip()
        if not value:
            continue
        hits = sum(1 for t in query_terms if t in value.lower())
        if hits > best_hits:
            best_hits, best_field = hits, value
    return (best_field[:220] + "…") if len(best_field) > 220 else best_field


def _structured_field_phrases(row: pd.Series) -> str:
    """Turn structured Yes/No fields into searchable phrases, so a query like 'voluntary
    disclosure' finds cases where that field is Yes, without treating the whole column as
    free text (voluntary_disclosure/cooperation are not in SEARCH_FIELDS themselves).
    """
    phrases = []
    if str(row.get("voluntary_disclosure", "")).strip().lower() == "yes":
        phrases.append("voluntary disclosure")
    if str(row.get("cooperation", "")).strip():
        phrases.append(f"{row['cooperation']} cooperation")
    if str(row.get("transaction_type", "")).strip():
        phrases.append(str(row["transaction_type"]))
    return " ".join(phrases)


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "order_id", "case_name", "decision_date", "total_penalty", "source_url", "snippet", "score",
    ])
