"""Data quality validation for CCI Penalty Intelligence.

Runs a set of explainable checks across the full dataset and writes every
finding to data/quality_report.csv. This script never "fixes" anything it
finds uncertain - it only flags it, by design, so a lawyer reviewing the
corpus can see exactly what needs a second look before relying on it.

Usage:
    python quality_check.py
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.data import load_full_dataset

OUTPUT_PATH = "data/quality_report.csv"
ACT_YEAR = 2002  # the Competition Act, 2002 - no order can predate it

INTERPRETIVE_FIELDS = [
    "conduct_type", "transaction_type", "notification_issue", "mitigating_factors", "aggravating_factors",
]


def run() -> pd.DataFrame:
    df = load_full_dataset()
    findings = []

    findings += _check_duplicate_ids(df)
    findings += _check_duplicate_registration_numbers(df)
    findings += _check_impossible_dates(df)
    findings += _check_malformed_penalties(df)
    findings += _check_total_penalty_consistency(df)
    findings += _check_missing_urls(df)
    findings += _check_missing_provenance(df)

    report = pd.DataFrame(findings, columns=["order_id", "check", "severity", "detail"])
    report.to_csv(OUTPUT_PATH, index=False)
    return report


def _check_duplicate_ids(df: pd.DataFrame) -> list[dict]:
    dupes = df["order_id"][df["order_id"].duplicated(keep=False)]
    return [
        {"order_id": oid, "check": "duplicate_order_id", "severity": "error",
         "detail": f"order_id {oid!r} appears {(dupes == oid).sum()} times"}
        for oid in sorted(dupes.unique())
    ]


def _check_duplicate_registration_numbers(df: pd.DataFrame) -> list[dict]:
    non_blank = df[df["combination_registration_no"].astype(str).str.strip() != ""]
    dupes = non_blank["combination_registration_no"][non_blank["combination_registration_no"].duplicated(keep=False)]
    findings = []
    for reg_no in sorted(dupes.unique()):
        matching_ids = df.loc[df["combination_registration_no"] == reg_no, "order_id"].tolist()
        findings.append({
            "order_id": "; ".join(matching_ids), "check": "duplicate_registration_no", "severity": "warning",
            "detail": f"combination_registration_no {reg_no!r} shared by {matching_ids}",
        })
    return findings


def _check_impossible_dates(df: pd.DataFrame) -> list[dict]:
    findings = []
    today = pd.Timestamp(datetime.now().date())
    for _, row in df.iterrows():
        date = row["decision_date"]
        if pd.isna(date):
            findings.append({"order_id": row["order_id"], "check": "missing_decision_date",
                              "severity": "warning", "detail": "decision_date could not be parsed"})
            continue
        if date.year < ACT_YEAR or date > today:
            findings.append({"order_id": row["order_id"], "check": "impossible_decision_date",
                              "severity": "error", "detail": f"decision_date {date.date()} is out of plausible range"})
    return findings


def _check_malformed_penalties(df: pd.DataFrame) -> list[dict]:
    findings = []
    for _, row in df.iterrows():
        for col in ("penalty_43a", "penalty_44", "penalty_45", "total_penalty"):
            value = row[col]
            if pd.notna(value) and value < 0:
                findings.append({"order_id": row["order_id"], "check": "negative_penalty",
                                  "severity": "error", "detail": f"{col} = {value} is negative"})
    return findings


def _check_total_penalty_consistency(df: pd.DataFrame) -> list[dict]:
    findings = []
    for _, row in df.iterrows():
        parts = [row["penalty_43a"], row["penalty_44"], row["penalty_45"]]
        present = [p for p in parts if pd.notna(p)]
        if not present or pd.isna(row["total_penalty"]):
            continue
        expected = sum(present)
        if abs(expected - row["total_penalty"]) > 1:  # rupee-level rounding tolerance
            findings.append({
                "order_id": row["order_id"], "check": "total_penalty_mismatch", "severity": "error",
                "detail": f"sum of extracted sections = {expected}, but total_penalty = {row['total_penalty']}",
            })
    return findings


def _check_missing_urls(df: pd.DataFrame) -> list[dict]:
    findings = []
    for _, row in df.iterrows():
        if str(row["source_url"]).strip() == "":
            findings.append({"order_id": row["order_id"], "check": "missing_source_url",
                              "severity": "warning", "detail": "source_url is blank"})
        if str(row["pdf_url"]).strip() == "":
            findings.append({"order_id": row["order_id"], "check": "missing_pdf_url",
                              "severity": "warning", "detail": "pdf_url is blank"})
    return findings


def _check_missing_provenance(df: pd.DataFrame) -> list[dict]:
    """An interpretive field with a value but no supporting excerpt is a trust problem -
    it would present generated intelligence with nothing to verify it against."""
    findings = []
    for _, row in df.iterrows():
        has_interpretive_value = any(str(row.get(f, "")).strip() != "" for f in INTERPRETIVE_FIELDS)
        has_provenance = (
            str(row.get("intelligence_source_text", "")).strip() != ""
            or str(row.get("penalty_source_text", "")).strip() != ""
        )
        if has_interpretive_value and not has_provenance:
            findings.append({
                "order_id": row["order_id"], "check": "provenance_missing", "severity": "error",
                "detail": "interpretive field(s) populated but no source excerpt recorded",
            })
    return findings


if __name__ == "__main__":
    report = run()
    print(f"Wrote {len(report)} finding(s) to {OUTPUT_PATH}")
    if not report.empty:
        print(report["check"].value_counts().to_string())
