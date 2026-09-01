"""CCI Penalty Intelligence - final product UI.

Streamlit UI only. All data loading lives in src/data.py, all statistics in
src/analytics.py, all comparable-case scoring in src/comparables.py, all
search in src/search.py, all legal-factor extraction in src/intelligence.py.
This file wires them together and renders five tabs - it contains no
scraping, PDF, or extraction logic of its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.analytics import (
    compare_outlier_impact,
    compute_penalty_stats,
    exclude_outliers,
    flag_statistical_outliers,
    format_inr,
)
from src.comparables import build_case_profile, find_comparables
from src.data import DataValidationError, add_quality_flags, get_filter_options, load_full_dataset
from src.search import search as search_corpus

DATA_PATH = "data/orders.csv"
METADATA_PATH = "data/corpus_metadata.json"

st.set_page_config(page_title="CCI Penalty Intelligence", layout="wide")


# ------------------------------------------------------------------------------------------
# Data loading
# ------------------------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _load() -> pd.DataFrame:
    df = load_full_dataset(DATA_PATH)
    df = add_quality_flags(df)
    df = flag_statistical_outliers(df, "penalty_43a")
    return df


def _corpus_last_updated() -> str:
    path = Path(METADATA_PATH)
    if not path.exists():
        return "unknown (run update_corpus.py)"
    try:
        meta = json.loads(path.read_text())
        return meta.get("last_updated_utc", "unknown")[:10]
    except (json.JSONDecodeError, OSError):
        return "unknown"


def render_disclaimer() -> None:
    st.info(
        "This tool is an analytical aid. The CCI order is the primary source. "
        "Verify every figure and legal conclusion against the original order before relying on it."
    )


def render_quality_badges(row: pd.Series) -> None:
    badges = []
    badges.append("📄 Source text" if row.get("source_text_available") else "🔍 OCR required")
    badges.append("💰 Penalty extracted" if row.get("penalty_extracted") else "💰 Penalty not extracted")
    if row.get("legal_intelligence_available"):
        badges.append("🧠 Legal intelligence available")
    badges.append("✅ Human verified" if row.get("human_verified") else "🤖 Auto-extracted, unverified")
    st.caption(" · ".join(badges))


# ------------------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------------------

def main() -> None:
    st.title("CCI Penalty Intelligence")
    st.caption("Section 43A / 44 precedent, penalty benchmarking and comparable-case intelligence.")

    try:
        df = _load()
    except FileNotFoundError:
        st.error(f"Could not find {DATA_PATH}. Run ingest.py first.")
        st.stop()
    except DataValidationError as exc:
        st.error(str(exc))
        st.stop()

    if df.empty:
        st.warning("orders.csv loaded but contains no rows.")
        st.stop()

    render_disclaimer()
    st.caption(f"Corpus last updated: {_corpus_last_updated()}  ·  {len(df)} orders in the dataset")

    tab_overview, tab_benchmark, tab_explorer, tab_comparables, tab_search = st.tabs(
        ["Overview", "Penalty Benchmark", "Case Explorer", "Find Comparables", "Corpus Search"]
    )

    with tab_overview:
        render_overview(df)
    with tab_benchmark:
        render_benchmark(df)
    with tab_explorer:
        render_case_explorer(df)
    with tab_comparables:
        render_comparables_tab(df)
    with tab_search:
        render_search_tab(df)


# ------------------------------------------------------------------------------------------
# Tab 1 - Overview
# ------------------------------------------------------------------------------------------

def render_overview(df: pd.DataFrame) -> None:
    st.subheader("Corpus overview")

    penalized = df[df["penalty_43a"].notna()]
    stats = compute_penalty_stats(df, "penalty_43a")
    years = df["year"].dropna()
    year_range = f"{int(years.min())}–{int(years.max())}" if not years.empty else "-"

    c1, c2, c3 = st.columns(3)
    c1.metric("Total orders", len(df))
    c2.metric("Orders with a Section 43A penalty", len(penalized))
    c3.metric("Year range", year_range)

    c4, c5, c6 = st.columns(3)
    c4.metric("Median Section 43A penalty", format_inr(stats["median"]))
    c5.metric("Mean Section 43A penalty", format_inr(stats["mean"]))
    c6.metric("Largest penalty", format_inr(stats["max"]))

    st.divider()
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("**Penalty trend by year**")
        if not penalized.empty:
            yearly = penalized.groupby(penalized["decision_date"].dt.year)["penalty_43a"].median()
            st.bar_chart(yearly.rename("Median Section 43A penalty (INR)"))
        else:
            st.caption("No penalty data to chart yet.")

    with col_b:
        st.markdown("**Distribution of penalties**")
        if not penalized.empty:
            st.bar_chart(penalized.set_index("case_name")["penalty_43a"].sort_values(ascending=False))
        else:
            st.caption("No penalty data to chart yet.")

    st.markdown("**Most common conduct types**")
    conduct_counts = df[df["conduct_type"] != ""]["conduct_type"].value_counts()
    if not conduct_counts.empty:
        st.bar_chart(conduct_counts)
    else:
        st.caption("No conduct-type intelligence extracted yet - run build_intelligence.py.")


# ------------------------------------------------------------------------------------------
# Tab 2 - Penalty Benchmark
# ------------------------------------------------------------------------------------------

DETAIL_COLUMNS = [
    "order_id", "case_name", "decision_date", "provision",
    "penalty_43a", "penalty_44", "penalty_45", "total_penalty",
    "penalty_source_page", "penalty_source_text",
    "conduct_type", "transaction_type", "sector",
    "notification_issue", "gun_jumping_type", "voluntary_disclosure", "cooperation",
    "mitigating_factors", "aggravating_factors",
    "statistical_outlier", "verified", "notes", "source_url", "pdf_url",
]


def render_benchmark(df: pd.DataFrame) -> None:
    st.subheader("Penalty benchmark")
    st.caption(
        "Every case has an Include checkbox - exclude any case you don't want in the benchmark. "
        "Nothing is excluded by default except where the dataset's own include_default says so."
    )

    options = get_filter_options(df)
    f1, f2, f3 = st.columns(3)
    f_year = f1.multiselect("Year", options["year"])
    f_provision = f2.multiselect("Provision", options["provision"])
    f_conduct = f3.multiselect("Conduct type", options["conduct_type"])

    f4, f5, f6 = st.columns(3)
    f_transaction = f4.multiselect("Transaction type", options["transaction_type"])
    f_voluntary = f5.multiselect("Voluntary disclosure", options["voluntary_disclosure"])
    f_cooperation = f6.multiselect("Cooperation", options["cooperation"])

    f_sector = []
    if options["sector"]:
        f_sector = st.multiselect("Sector", options["sector"])

    filtered = df.copy()
    for col, selected in [
        ("year", f_year), ("provision", f_provision), ("conduct_type", f_conduct),
        ("transaction_type", f_transaction), ("voluntary_disclosure", f_voluntary),
        ("cooperation", f_cooperation), ("sector", f_sector),
    ]:
        if selected:
            filtered = filtered[filtered[col].isin(selected)]

    st.markdown("**Cases**")
    if filtered.empty:
        st.warning("No cases match the current filters.")
        edited = filtered.assign(Include=pd.Series(dtype=bool))
    else:
        table = filtered.copy()
        table.insert(0, "Include", table["include_default"])
        display_cols = ["Include"] + DETAIL_COLUMNS
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
                "statistical_outlier": st.column_config.CheckboxColumn("Statistical outlier"),
                "verified": st.column_config.CheckboxColumn("Verified"),
                "source_url": st.column_config.LinkColumn("Source"),
                "pdf_url": st.column_config.LinkColumn("PDF"),
            },
            key="benchmark_editor",
        )

    exclude_flagged = st.toggle(
        "Exclude statistical outliers from the benchmark (IQR rule - see Overview for what this flags)",
        value=False,
    )

    included = edited[edited["Include"] == True] if not edited.empty else edited  # noqa: E712
    selected_df = exclude_outliers(included) if exclude_flagged else included

    st.divider()
    st.subheader("Section 43A statistics")
    stats = compute_penalty_stats(selected_df, "penalty_43a")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Selected cases", stats["count"])
    c2.metric("MEDIAN", format_inr(stats["median"]))
    c3.metric("Mean", format_inr(stats["mean"]))
    c4.metric("Total", format_inr(stats["total"]))
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Minimum", format_inr(stats["min"]))
    c6.metric("Maximum", format_inr(stats["max"]))
    c7.metric("25th percentile", format_inr(stats["p25"]))
    c8.metric("75th percentile", format_inr(stats["p75"]))
    st.caption(
        "The median is often the more reliable benchmark: a single exceptional penalty "
        "(a large or unusually lenient case) can swing the mean without changing what a "
        "typical case actually looks like. Compare the two above."
    )

    st.subheader("Outlier impact")
    st.caption("All filtered cases, with vs. without statistically flagged outliers (independent of the Include ticks above).")
    comparison = compare_outlier_impact(filtered, "penalty_43a")
    c1, c2 = st.columns(2)
    c1.metric("Average incl. all filtered cases", format_inr(comparison["including_outliers"]["mean"]))
    c1.caption(f"{comparison['including_outliers']['count']} cases")
    c2.metric("Average excl. statistical outliers", format_inr(comparison["excluding_outliers"]["mean"]))
    c2.caption(f"{comparison['excluding_outliers']['count']} cases")

    st.subheader("Penalty by case")
    if selected_df.empty:
        st.caption("No cases selected.")
    else:
        chart_data = selected_df.set_index("case_name")[["penalty_43a", "penalty_44", "penalty_45"]].fillna(0)
        st.bar_chart(chart_data)

    st.subheader("Selected case details")
    if selected_df.empty:
        st.caption("No cases selected.")
    else:
        st.dataframe(
            selected_df[DETAIL_COLUMNS], hide_index=True, width="stretch",
            column_config={"source_url": st.column_config.LinkColumn("Source"), "pdf_url": st.column_config.LinkColumn("PDF")},
        )


# ------------------------------------------------------------------------------------------
# Tab 3 - Case Explorer
# ------------------------------------------------------------------------------------------

def render_case_explorer(df: pd.DataFrame) -> None:
    st.subheader("Case explorer")

    query = st.text_input("Filter by case name or registration number", key="explorer_filter")
    options_df = df
    if query:
        mask = (
            df["case_name"].str.contains(query, case=False, na=False)
            | df["combination_registration_no"].str.contains(query, case=False, na=False)
        )
        options_df = df[mask]

    if options_df.empty:
        st.warning("No cases match that filter.")
        return

    labels = options_df.apply(
        lambda r: f"{r['case_name'][:70]} — {r['decision_date'].date() if pd.notna(r['decision_date']) else '?'}", axis=1
    )
    choice = st.selectbox("Select a case", options=options_df.index, format_func=lambda i: labels.loc[i])
    row = df.loc[choice]

    render_case_profile(row)


def render_case_profile(row: pd.Series) -> None:
    st.markdown(f"## {row['case_name']}")
    render_quality_badges(row)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Registration no.", row["combination_registration_no"] or "-")
    c2.metric("Decision date", str(row["decision_date"].date()) if pd.notna(row["decision_date"]) else "-")
    c3.metric("Provision", row["provision"] or "-")
    c4.metric("Total penalty", format_inr(row["total_penalty"]))

    with st.expander("Legal issue & outcome", expanded=True):
        st.markdown(f"**Legal issue:** {row['legal_issue_summary'] or '_not extracted_'}")
        st.markdown(f"**Conduct:** {row['conduct_type'] or '_not extracted_'}")
        st.markdown(f"**Transaction type:** {row['transaction_type'] or '_not extracted_'}")
        st.markdown(f"**Outcome:** {row['outcome_summary'] or '_not extracted_'}")

    with st.expander("Penalty rationale"):
        st.write(row["penalty_rationale"] or "_not extracted_")

    with st.expander("Mitigating & aggravating factors"):
        st.markdown(f"**Mitigating factors:** {row['mitigating_factors'] or '_not identified_'}")
        st.markdown(f"**Aggravating factors:** {row['aggravating_factors'] or '_not identified_'}")
        st.markdown(f"**Voluntary disclosure:** {row['voluntary_disclosure'] or '_not stated_'}")
        st.markdown(f"**Cooperation:** {row['cooperation'] or '_not stated_'}")

    with st.expander("Source excerpts (provenance)"):
        if row["penalty_source_text"]:
            st.markdown(f"**Penalty finding** (page {row['penalty_source_page'] or '?'}):")
            st.caption(row["penalty_source_text"])
        if row["intelligence_source_text"]:
            st.markdown(f"**Legal-factor finding** (page {row['intelligence_source_page'] or '?'}):")
            st.caption(row["intelligence_source_text"])
        if not row["penalty_source_text"] and not row["intelligence_source_text"]:
            st.caption("No source excerpt captured for this case.")

    st.markdown("**Official source**")
    st.markdown(f"[CCI order page]({row['source_url']})  ·  [PDF]({row['pdf_url']})" if row["source_url"] else "_not available_")


# ------------------------------------------------------------------------------------------
# Tab 4 - Find Comparables
# ------------------------------------------------------------------------------------------

def render_comparables_tab(df: pd.DataFrame) -> None:
    st.subheader("Find comparables")
    st.caption(
        "Select an existing precedent, or build a hypothetical fact pattern, to find the "
        "most comparable CCI orders and benchmark the likely penalty range."
    )

    mode = st.radio("Mode", ["Select an existing case", "Build a fact pattern"], horizontal=True)

    base = None
    exclude_ids: set = set()

    if mode == "Select an existing case":
        labels = df.apply(
            lambda r: f"{r['case_name'][:70]} — {r['decision_date'].date() if pd.notna(r['decision_date']) else '?'}", axis=1
        )
        choice = st.selectbox("Precedent case", options=df.index, format_func=lambda i: labels.loc[i], key="comparables_case_select")
        base_row = df.loc[choice]
        base = build_case_profile(base_row)
        exclude_ids = {base_row["order_id"]}
        st.caption(
            f"Base case: **{base['case_name']}** — conduct: {base['conduct_type'] or '?'}, "
            f"transaction: {base['transaction_type'] or '?'}"
        )

    else:
        opts = get_filter_options(df)
        c1, c2 = st.columns(2)
        conduct = c1.selectbox("Conduct", [""] + opts["conduct_type"])
        transaction = c2.selectbox("Transaction type", [""] + opts["transaction_type"])
        c3, c4 = st.columns(2)
        closed_before = c3.selectbox("Closed before approval?", ["", "Yes", "No"])
        voluntary = c4.selectbox("Voluntary disclosure", ["", "Yes", "No"])
        c5, c6 = st.columns(2)
        cooperation = c5.selectbox("Cooperation", ["", "Full", "Partial"])
        delay_days = c6.number_input("Approximate delay (days)", min_value=0, value=0, step=1)

        base = {
            "order_id": None,
            "case_name": "Hypothetical fact pattern",
            "decision_date": None,
            "conduct_type": conduct or None,
            "transaction_type": transaction or None,
            "closed_before_approval": closed_before or None,
            "voluntary_disclosure": voluntary or None,
            "cooperation": cooperation or None,
            "delay_duration_days": float(delay_days) if delay_days else None,
            "penalty_43a": None,
            "mitigating_factors": None,
            "aggravating_factors": None,
        }

    results = find_comparables(base, df, exclude_order_ids=exclude_ids, top_n=10)

    if results.empty:
        st.warning("No comparable cases could be scored - not enough overlapping data between this case/fact pattern and the corpus.")
        return

    st.markdown("**Adjust the comparable set**")
    results_table = results.copy()
    results_table.insert(0, "Include", True)
    edited = st.data_editor(
        results_table[["Include", "order_id", "case_name", "decision_date", "penalty_43a", "comparable_score"]],
        hide_index=True, width="stretch",
        disabled=["order_id", "case_name", "decision_date", "penalty_43a", "comparable_score"],
        column_config={
            "Include": st.column_config.CheckboxColumn("Include"),
            "comparable_score": st.column_config.NumberColumn("Comparable score (%)", format="%.1f"),
            "penalty_43a": st.column_config.NumberColumn("Penalty 43A", format="%.0f"),
        },
        key="comparables_editor",
    )
    included_ids = set(edited.loc[edited["Include"] == True, "order_id"])  # noqa: E712
    benchmark_set = results[results["order_id"].isin(included_ids)]

    st.divider()
    st.subheader("Comparable-set benchmark")
    stats = compute_penalty_stats(benchmark_set, "penalty_43a")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Comparable cases", stats["count"])
    c2.metric("Median", format_inr(stats["median"]))
    c3.metric("Mean", format_inr(stats["mean"]))
    c4.metric("Range", f"{format_inr(stats['min'])} – {format_inr(stats['max'])}")
    c5, c6 = st.columns(2)
    c5.metric("25th percentile", format_inr(stats["p25"]))
    c6.metric("75th percentile", format_inr(stats["p75"]))

    st.subheader("Why each case matches")
    for _, r in results[results["order_id"].isin(included_ids)].iterrows():
        with st.expander(f"{r['comparable_score']}%  —  {r['case_name'][:70]} ({r['points_earned']}/{r['points_possible']} pts)"):
            st.markdown(f"**Penalty:** {format_inr(r['penalty_43a'])}  ·  **Date:** {r['decision_date'].date() if pd.notna(r['decision_date']) else '?'}")
            for reason in r["reasons"]:
                st.markdown(f"- {reason}")
            if r["source_url"]:
                st.markdown(f"[Case profile / source]({r['source_url']})")


# ------------------------------------------------------------------------------------------
# Tab 5 - Corpus Search
# ------------------------------------------------------------------------------------------

def render_search_tab(df: pd.DataFrame) -> None:
    st.subheader("Search CCI penalty precedent")
    st.caption("Examples: pre-closing integration · voluntary disclosure · delay in notification · share purchase agreement · exercise of control")

    query = st.text_input("Search CCI penalty precedent…", key="corpus_search_query")
    if not query:
        return

    results = search_corpus(df, query, top_n=15)
    if results.empty:
        st.warning("No matches found.")
        return

    st.caption(f"{len(results)} result(s)")
    for _, r in results.iterrows():
        with st.container(border=True):
            date_str = r["decision_date"].date() if pd.notna(r["decision_date"]) else "?"
            st.markdown(f"**{r['case_name']}**  —  {date_str}  ·  {format_inr(r['total_penalty'])}")
            if r["snippet"]:
                st.caption(r["snippet"])
            if r["source_url"]:
                st.markdown(f"[Source]({r['source_url']})")


if __name__ == "__main__":
    main()
