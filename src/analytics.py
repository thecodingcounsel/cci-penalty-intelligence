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


def flag_statistical_outliers(df: pd.DataFrame, column: str = "penalty_43a") -> pd.DataFrame:
    """Add a `statistical_outlier` boolean column using the standard IQR rule (below
    Q1-1.5*IQR or above Q3+1.5*IQR), computed fresh from whatever rows are passed in.

    This is a transparent, explainable statistical flag - never a manual "this case is
    famous so it must be an outlier" label. With fewer than 4 valid values, IQR isn't
    meaningful, so nothing is flagged rather than flagging on a near-empty distribution.
    """
    df = df.copy()
    values = pd.to_numeric(df[column], errors="coerce") if column in df.columns else pd.Series(dtype=float)
    valid = values.dropna()

    if len(valid) < 4:
        df["statistical_outlier"] = False
        return df

    q1, q3 = valid.quantile(0.25), valid.quantile(0.75)
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    df["statistical_outlier"] = ((values < lower) | (values > upper)).fillna(False)
    return df


def exclude_outliers(df: pd.DataFrame, column: str = "statistical_outlier") -> pd.DataFrame:
    """Return only rows not flagged as an outlier in `column` (default: the computed
    statistical_outlier flag; pass column="is_outlier" for the legacy manual flag).
    """
    if df.empty or column not in df.columns:
        return df
    return df[df[column] != True]  # noqa: E712


def compare_outlier_impact(df: pd.DataFrame, column: str = "penalty_43a") -> dict:
    """Compare penalty stats including vs excluding flagged statistical outliers, for the
    same base set of rows. Recomputes the statistical_outlier flag on this exact set, so the
    comparison reflects outliers relative to whatever's currently selected/filtered.
    """
    flagged = flag_statistical_outliers(df, column)
    return {
        "including_outliers": compute_penalty_stats(flagged, column),
        "excluding_outliers": compute_penalty_stats(exclude_outliers(flagged), column),
    }


def format_inr(value) -> str:
    """Format a rupee amount using the Indian lakh/crore convention.

    Examples: 500000 -> '₹5 Lakh', 15000000 -> '₹1.5 Crore', 2000000000 -> '₹200 Crore',
    0 -> '₹0'. Trailing zeros are trimmed (up to 2 decimal places) so a round figure like
    ₹5 Lakh never displays as '₹5.00 Lakh'. Returns '-' for missing/NaN values instead of raising.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"

    value = float(value)
    sign = "-" if value < 0 else ""
    value = abs(value)

    if value >= 1_00_00_000:
        return f"{sign}₹{_trim(value / 1_00_00_000)} Crore"
    if value >= 1_00_000:
        return f"{sign}₹{_trim(value / 1_00_000)} Lakh"
    return f"{sign}₹{value:,.0f}"


def _trim(number: float) -> str:
    """'5.00' -> '5', '1.50' -> '1.5', '1.23' -> '1.23'."""
    return f"{number:.2f}".rstrip("0").rstrip(".")
