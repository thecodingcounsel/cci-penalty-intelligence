"""CCI Penalty Intelligence - V1

Streamlit UI. This file wires together src/data.py (loading) and
src/analytics.py (statistics) - it does not contain data-loading logic or
statistical calculations itself. See README.md for architecture notes.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analytics import (
    compare_outlier_impact,
    compute_penalty_stats,
    exclude_outliers,
    format_inr,
)
from src.data import DataValidationError, get_filter_options, load_orders

DATA_PATH = "data/orders.csv"

DETAIL_COLUMNS = [
    "order_id",
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
    "is_outlier",
    "verified",
    "notes",
    "source_url",
    "pdf_url",
]

st.set_page_config(page_title="CCI Penalty Intelligence", layout="wide")


def render_disclaimer() -> None:
    st.info(
        "This tool is an analytical aid. The CCI order is the primary source. "
        "Verify every figure against the original order before relying on it."
    )


def render_stat_row(stats: dict) -> None:
    cols = st.columns(4)
    cols[0].metric("Selected cases", stats["count"])
    cols[1].metric("Mean", format_inr(stats["mean"]))
    cols[2].metric("Median", format_inr(stats["median"]))
    cols[3].metric("Total", format_inr(stats["total"]))

    cols = st.columns(4)
    cols[0].metric("Minimum", format_inr(stats["min"]))
    cols[1].metric("Maximum", format_inr(stats["max"]))
    cols[2].metric("25th percentile", format_inr(stats["p25"]))
    cols[3].metric("75th percentile", format_inr(stats["p75"]))


def main() -> None:
    st.title("CCI Penalty Intelligence")
    st.caption("Section 43A and 44 combination orders - penalty analytics for lawyers")
    render_disclaimer()

    try:
        orders = load_orders(DATA_PATH)
    except FileNotFoundError:
        st.error(f"Could not find {DATA_PATH}. Make sure the data file exists.")
        st.stop()
    except DataValidationError as exc:
        st.error(str(exc))
        st.stop()

    if orders.empty:
        st.warning("orders.csv loaded but contains no rows.")
        st.stop()

    # --- Sidebar filters ---
    options = get_filter_options(orders)
    st.sidebar.header("Filters")

    f_provision = st.sidebar.multiselect("Provision", options["provision"])
    f_year = st.sidebar.multiselect("Year", options["year"])
    f_conduct = st.sidebar.multiselect("Conduct type", options["conduct_type"])
    f_transaction = st.sidebar.multiselect("Transaction type", options["transaction_type"])
    f_sector = st.sidebar.multiselect("Sector", options["sector"])

    filtered = orders.copy()
    if f_provision:
        filtered = filtered[filtered["provision"].isin(f_provision)]
    if f_year:
        filtered = filtered[filtered["year"].isin(f_year)]
    if f_conduct:
        filtered = filtered[filtered["conduct_type"].isin(f_conduct)]
    if f_transaction:
        filtered = filtered[filtered["transaction_type"].isin(f_transaction)]
    if f_sector:
        filtered = filtered[filtered["sector"].isin(f_sector)]

    st.subheader("Cases")
    st.caption(
        "Tick or untick Include to build your own working set. "
        "No case is excluded by default except where include_default was set to False in the data."
    )

    if filtered.empty:
        st.warning("No cases match the current filters.")
        edited = filtered.assign(Include=pd.Series(dtype=bool))
    else:
        table = filtered.copy()
        table.insert(0, "Include", table["include_default"])

        display_cols = ["Include"] + DETAIL_COLUMNS + ["is_outlier", "verified"]
        # dedupe while preserving order (is_outlier/verified already in DETAIL_COLUMNS)
        seen = set()
        display_cols = [c for c in display_cols if not (c in seen or seen.add(c))]

        edited = st.data_editor(
            table[display_cols],
            hide_index=True,
            width="stretch",
            disabled=[c for c in display_cols if c != "Include"],
            column_config={
                "Include": st.column_config.CheckboxColumn("Include"),
                "decision_date": st.column_config.DateColumn("Decision date"),
                "penalty_43a": st.column_config.NumberColumn("Penalty 43A", format="%.0f"),
                "penalty_44": st.column_config.NumberColumn("Penalty 44", format="%.0f"),
                "penalty_45": st.column_config.NumberColumn("Penalty 45", format="%.0f"),
                "total_penalty": st.column_config.NumberColumn("Total penalty", format="%.0f"),
                "is_outlier": st.column_config.CheckboxColumn("Outlier"),
                "verified": st.column_config.CheckboxColumn("Verified"),
                "source_url": st.column_config.LinkColumn("Source"),
                "pdf_url": st.column_config.LinkColumn("PDF"),
            },
            key="orders_editor",
        )

    exclude_flagged = st.toggle("Exclude flagged outliers from analytics", value=False)

    included = edited[edited["Include"] == True] if not edited.empty else edited  # noqa: E712
    selected = exclude_outliers(included) if exclude_flagged else included

    st.subheader("Section 43A analytics")
    st.caption("Recalculated from the cases currently ticked Include above.")
    stats_43a = compute_penalty_stats(selected, "penalty_43a")
    render_stat_row(stats_43a)

    st.subheader("Outlier impact comparison")
    st.caption(
        "Compares all sidebar-filtered cases against the same set with flagged outliers removed "
        "(independent of the Include ticks above)."
    )
    comparison = compare_outlier_impact(filtered, "penalty_43a")
    c1, c2 = st.columns(2)
    c1.metric("Average incl. all filtered cases", format_inr(comparison["including_outliers"]["mean"]))
    c1.caption(f"{comparison['including_outliers']['count']} cases")
    c2.metric("Average excl. flagged outliers", format_inr(comparison["excluding_outliers"]["mean"]))
    c2.caption(f"{comparison['excluding_outliers']['count']} cases")

    st.subheader("Penalty by case")
    if selected.empty:
        st.caption("No cases selected - tick Include on at least one case to see a chart.")
    else:
        chart_data = selected.set_index("case_name")[["penalty_43a", "penalty_44", "penalty_45"]].fillna(0)
        st.bar_chart(chart_data)

    st.subheader("Selected case details")
    if selected.empty:
        st.caption("No cases selected.")
    else:
        st.dataframe(
            selected[DETAIL_COLUMNS],
            hide_index=True,
            width="stretch",
            column_config={
                "source_url": st.column_config.LinkColumn("Source"),
                "pdf_url": st.column_config.LinkColumn("PDF"),
            },
        )

    render_disclaimer()


if __name__ == "__main__":
    main()
