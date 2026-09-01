"""Analytics layer for CCI Penalty Intelligence.

Pure functions only: every function here takes a DataFrame (and maybe a
column name) and returns numbers or a DataFrame. Nothing in this module
imports Streamlit or knows about the sidebar, checkboxes, or CSV files.
That separation is what lets V2 reuse these exact functions from a RAG
answer, an API endpoint, or a notebook without touching the UI at all.
"""

from __future__ import annotations

import pandas as pd

PENALTY_STAT_KEYS = ["count", "mean", "median", "min", "max", "total", "p25", "p75"]


def compute_penalty_stats(df: pd.DataFrame, column: str = "penalty_43a") -> dict:
    """Compute count/mean/median/min/max/total/p25/p75 for a penalty column.

    Operates on whatever rows are already selected by the caller (filtered,
    included, outliers excluded, etc. - this function does not filter).
    Returns all-zero stats instead of raising when there is no data to
    summarise, so an empty selection never crashes the app.
    """
    if df.empty or column not in df.columns:
        values = pd.Series(dtype=float)
    else:
        values = pd.to_numeric(df[column], errors="coerce").dropna()

    if values.empty:
        return {key: 0 for key in PENALTY_STAT_KEYS}

    return {
        "count": int(values.count()),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "min": float(values.min()),
        "max": float(values.max()),
        "total": float(values.sum()),
        "p25": float(values.quantile(0.25)),
        "p75": float(values.quantile(0.75)),
    }


def exclude_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Return only rows not flagged is_outlier=True."""
    if df.empty or "is_outlier" not in df.columns:
        return df
    return df[df["is_outlier"] != True]  # noqa: E712


def compare_outlier_impact(df: pd.DataFrame, column: str = "penalty_43a") -> dict:
    """Compare penalty stats including vs excluding flagged outliers, for the same base set of rows."""
    return {
        "including_outliers": compute_penalty_stats(df, column),
        "excluding_outliers": compute_penalty_stats(exclude_outliers(df), column),
    }


def format_inr(value) -> str:
    """Format a rupee amount using the Indian lakh/crore convention.

    Examples: 500000 -> '₹5.00 Lakh', 12500000 -> '₹1.25 Crore', 0 -> '₹0'.
    Returns '-' for missing/NaN values instead of raising.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"

    value = float(value)
    sign = "-" if value < 0 else ""
    value = abs(value)

    if value >= 1_00_00_000:
        return f"{sign}₹{value / 1_00_00_000:.2f} Crore"
    if value >= 1_00_000:
        return f"{sign}₹{value / 1_00_000:.2f} Lakh"
    return f"{sign}₹{value:,.0f}"
