"""Data ingestion layer for CCI Penalty Intelligence.

This is the ONLY module that knows where the data lives and what format it
is in. In V1 that's data/orders.csv. In V2 this file is the one place that
changes to read from SQLite instead — src/analytics.py and app.py should
never need to change, because both only ever see the clean DataFrame that
load_orders() returns.
"""

from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = [
    "order_id",
    "combination_registration_no",
    "case_name",
    "decision_date",
    "provision",
    "penalty_43a",
    "penalty_44",
    "penalty_45",
    "total_penalty",
    "penalty_source_page",
    "penalty_source_text",
    "conduct_type",
    "transaction_type",
    "sector",
    "notification_issue",
    "gun_jumping_type",
    "voluntary_disclosure",
    "cooperation",
    "mitigating_factors",
    "aggravating_factors",
    "source_url",
    "pdf_url",
    "include_default",
    "is_outlier",
    "verified",
    "notes",
]

NUMERIC_COLUMNS = ["penalty_43a", "penalty_44", "penalty_45", "total_penalty"]
BOOLEAN_COLUMNS = ["include_default", "is_outlier", "verified"]
TEXT_COLUMNS = [
    c for c in REQUIRED_COLUMNS
    if c not in NUMERIC_COLUMNS + BOOLEAN_COLUMNS + ["decision_date"]
]


class DataValidationError(Exception):
    """Raised when the source data is missing required columns."""


def load_orders(path: str = "data/orders.csv") -> pd.DataFrame:
    """Load, validate and clean the orders dataset.

    Always returns a DataFrame with every column in REQUIRED_COLUMNS present
    and correctly typed (dates parsed, penalties numeric, flags boolean),
    even if the source file has blank cells or malformed values. Bad or
    missing values become NaN / False / "" rather than raising, so a messy
    row never crashes the app - it just won't count toward numeric stats.
    """
    df = pd.read_csv(path)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(
            f"{path} is missing required columns: {', '.join(missing)}"
        )

    df = df[REQUIRED_COLUMNS].copy()

    df["decision_date"] = pd.to_datetime(df["decision_date"], errors="coerce")
    df["year"] = df["decision_date"].dt.year

    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in BOOLEAN_COLUMNS:
        df[col] = _to_bool(df[col])

    for col in TEXT_COLUMNS:
        df[col] = df[col].fillna("").astype(str).str.strip()

    return df


def _to_bool(series: pd.Series) -> pd.Series:
    """Parse True/False/Yes/No-like text into real booleans, defaulting anything unclear to False."""
    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .map({"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False})
        .fillna(False)
    )


def get_filter_options(df: pd.DataFrame) -> dict:
    """Return sorted unique values for each sidebar filter, with blanks dropped."""

    def text_options(col: str) -> list:
        values = df[col].dropna().unique().tolist()
        return sorted(v for v in values if str(v).strip() != "")

    years = df["year"].dropna().unique().tolist()

    return {
        "provision": text_options("provision"),
        "year": sorted(int(y) for y in years),
        "conduct_type": text_options("conduct_type"),
        "transaction_type": text_options("transaction_type"),
        "sector": text_options("sector"),
    }
