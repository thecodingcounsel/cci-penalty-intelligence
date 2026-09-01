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
import plotly.graph_objects as go
import streamlit as st

from src.analytics import compute_penalty_stats, flag_statistical_outliers, format_inr
from src.comparables import build_case_profile, find_comparables
from src.data import DataValidationError, add_quality_flags, get_filter_options, load_full_dataset
from src.search import search as search_corpus

DATA_PATH = "data/orders.csv"
METADATA_PATH = "data/corpus_metadata.json"

# Amazon/Future's penalty (~₹202 Cr) is roughly two orders of magnitude larger than every
# other case in the corpus. Left in, it flattens every other bar/line on the Overview tab's
# charts to invisibility. This constant is used ONLY by render_overview() below (via
# _overview_corpus()) - Amazon stays fully present in the underlying dataset, the Penalty
# Benchmark table, Case Explorer, Find Comparables, and Corpus Search. This is a presentation
# choice on one tab, not a data exclusion - see README "Trust model" / "Amazon handling".
AMAZON_ORDER_ID = "CCI-1138"

# Restrained palette shared by the CSS below and the Plotly charts in render_overview().
# Kept in sync by hand with .streamlit/config.toml's [theme] block - one muted accent,
# no gradients, no additional bright colors.
ACCENT_COLOR = "#5B8DEF"
CHART_TEXT_COLOR = "#9aa4b2"
CHART_GRID_COLOR = "rgba(255, 255, 255, 0.07)"

st.set_page_config(page_title="CCI Penalty Intelligence", layout="wide")

CSS = """
<style>
/* Clean system-font stack across the whole app, applied broadly so it reaches Streamlit's
   form controls (buttons/inputs/selects don't inherit body font-family by default) and
   BaseWeb-rendered widgets (multiselects, tabs) without needing per-widget overrides. */
.stApp, .stApp * {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
}

/* Hide default Streamlit chrome (hamburger menu, "Made with Streamlit" footer, the
   decorative top gradient bar) that makes the page read as a prototype rather than a
   product. The toolbar/running-indicator area is left alone so functionality is untouched. */
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }

.block-container { padding-top: 1.75rem; padding-bottom: 3rem; max-width: 1180px; }

/* Title: smaller and less dominant than Streamlit's default st.title size. Subtitle and
   corpus-info line (both st.caption) are visually secondary via reduced opacity/size. */
h1 { font-size: 1.65rem !important; font-weight: 600 !important; margin-bottom: 0.1rem !important; }
[data-testid="stCaptionContainer"] { opacity: 0.62; font-size: 0.85rem; }

/* Tighten the default gap Streamlit puts between stacked elements so the page doesn't feel
   like it has dead vertical space, while still leaving deliberate room via st.divider(). */
[data-testid="stVerticalBlock"] { gap: 0.6rem; }

/* Metric labels: title case as written in the Python code (no forced uppercase), medium
   weight rather than bold, muted via opacity so it adapts to both light and dark themes. */
[data-testid="stMetricValue"] { font-size: 1.7rem; font-weight: 600; letter-spacing: -0.01em; }
[data-testid="stMetricLabel"] { font-size: 0.82rem; font-weight: 500; opacity: 0.65; letter-spacing: 0.01em; }

hr { margin: 1.1rem 0; border-color: rgba(128, 128, 128, 0.2); }
h3 { margin-top: 0.25rem; font-weight: 600; }

.cci-hero { padding: 0.25rem 0 0.75rem 0; }
.cci-hero .cci-label { font-size: 0.82rem; font-weight: 500; opacity: 0.65; margin-bottom: 0.15rem; }
.cci-hero .cci-value { font-size: 2.8rem; font-weight: 600; letter-spacing: -0.02em; line-height: 1.1; }

/* Overview metric cards: subtle border, gently rounded corners, modest padding, no shadow. */
.cci-metric-card {
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.02);
  padding: 0.85rem 1rem 0.75rem 1rem;
}
.cci-metric-card .cci-metric-label { font-size: 0.76rem; font-weight: 500; opacity: 0.6; margin-bottom: 0.35rem; letter-spacing: 0.01em; }
.cci-metric-card .cci-metric-value { font-size: 1.55rem; font-weight: 600; letter-spacing: -0.01em; line-height: 1.15; }

.cci-tag { display: inline-block; font-size: 0.78rem; padding: 0.18rem 0.6rem; border-radius: 999px;
           background: rgba(128, 128, 128, 0.16); margin: 0 0.3rem 0.3rem 0; white-space: nowrap; }
.cci-score { font-weight: 600; font-size: 1.15rem; text-align: right; }
.cci-muted { opacity: 0.65; }
</style>
"""


# ------------------------------------------------------------------------------------------
# Data loading + small display helpers
# ------------------------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _load() -> pd.DataFrame:
    df = load_full_dataset(DATA_PATH)
    df = add_quality_flags(df)
    # Informational only - shown as a read-only column in the benchmark table so a lawyer
    # can see which cases are statistical outliers. Never auto-excludes anything; the
    # Include checkbox is the only thing that controls the benchmark set.
    df = flag_statistical_outliers(df, "penalty_43a")
    return df


def _overview_corpus(df: pd.DataFrame) -> pd.DataFrame:
    """Presentation-only: drops Amazon/Future for Overview-tab visuals. See AMAZON_ORDER_ID."""
    return df[df["order_id"] != AMAZON_ORDER_ID]


def _corpus_last_updated() -> str:
    path = Path(METADATA_PATH)
    if not path.exists():
        return "unknown"
    try:
        meta = json.loads(path.read_text())
        return meta.get("last_updated_utc", "unknown")[:10]
    except (json.JSONDecodeError, OSError):
        return "unknown"


def _date_str(value) -> str:
    return str(value.date()) if pd.notna(value) else "—"


def render_hero_metric(label: str, value: str) -> None:
    st.markdown(
        f'<div class="cci-hero"><div class="cci-label">{label}</div><div class="cci-value">{value}</div></div>',
        unsafe_allow_html=True,
    )


def render_metric_card(label: str, value) -> None:
    st.markdown(
        f'<div class="cci-metric-card"><div class="cci-metric-label">{label}</div>'
        f'<div class="cci-metric-value">{value}</div></div>',
        unsafe_allow_html=True,
    )


def _plotly_layout(**overrides) -> dict:
    """Shared restrained styling for both Overview charts: transparent background (so the
    app's own dark background shows through), muted axis text, subtle gridlines, no legend,
    no Plotly toolbar - avoids the 'default dashboard' look."""
    layout = dict(
        showlegend=False,
        margin=dict(l=10, r=10, t=10, b=10),
        height=260,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=CHART_TEXT_COLOR, size=12),
        hoverlabel=dict(bgcolor="#1c2129", font_color=CHART_TEXT_COLOR),
    )
    layout.update(overrides)
    return layout


def render_tags(items: list[str]) -> None:
    if not items:
        return
    st.markdown(" ".join(f'<span class="cci-tag">{i}</span>' for i in items), unsafe_allow_html=True)


def render_quality_badges(row: pd.Series) -> None:
    badges = ["Source text available" if row.get("source_text_available") else "OCR required"]
    if row.get("legal_intelligence_available"):
        badges.append("Legal intelligence available")
    badges.append("Human verified" if row.get("human_verified") else "Auto-extracted, unverified")
    st.caption(" · ".join(badges))


# ------------------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------------------

def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("CCI Penalty Intelligence")
    st.caption("Section 43A / 44 precedent intelligence")

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

    st.caption(
        f"{len(df)} orders · corpus last updated {_corpus_last_updated()} · "
        "an analytical aid — the CCI order remains the primary source"
    )

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
    # All Overview penalty statistics (median/mean/largest) and the trend chart use the
    # Amazon-excluded view consistently - see AMAZON_ORDER_ID / _overview_corpus() above.
    # Total Orders is the only card that reflects the full 68-order corpus. Amazon remains
    # fully present in the underlying dataset and every other tab.
    overview_df = _overview_corpus(df)
    overview_penalized = overview_df[overview_df["penalty_43a"].notna()]
    overview_stats = compute_penalty_stats(overview_df, "penalty_43a")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_metric_card("Total Orders", len(df))
    with c2:
        render_metric_card("Median Penalty (excl. Amazon)", format_inr(overview_stats["median"]))
    with c3:
        render_metric_card("Mean / Average (excl. Amazon)", format_inr(overview_stats["mean"]))
    with c4:
        render_metric_card("Largest Penalty (excl. Amazon)", format_inr(overview_stats["max"]))
    st.caption("Amazon/Future is excluded from overview penalty statistics.")

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("**Penalty trend by year**")
        if not overview_penalized.empty:
            render_trend_chart(overview_penalized)
        else:
            st.caption("Not enough data to chart yet.")

    with col_b:
        st.markdown("**Most common conduct types**")
        conduct_counts = df[df["conduct_type"] != ""]["conduct_type"].value_counts()
        if not conduct_counts.empty:
            render_conduct_chart(conduct_counts)
        else:
            st.caption("No conduct-type intelligence extracted yet.")


def render_trend_chart(overview_penalized: pd.DataFrame) -> None:
    yearly = (
        overview_penalized.groupby(overview_penalized["decision_date"].dt.year)["penalty_43a"]
        .median() / 1_00_000
    )
    # Years as category-typed strings, not a numeric axis - the only way to guarantee
    # Plotly never applies thousands-grouping (e.g. "2,013") to a tick label.
    years = [str(int(y)) for y in yearly.index]

    fig = go.Figure(go.Scatter(
        x=years, y=yearly.values, mode="lines+markers",
        line=dict(color=ACCENT_COLOR, width=2),
        marker=dict(size=5, color=ACCENT_COLOR),
        hovertemplate="%{x}: ₹%{y:.1f} Lakh<extra></extra>",
    ))
    fig.update_layout(**_plotly_layout(
        xaxis=dict(type="category", showgrid=False, tickfont=dict(size=11)),
        yaxis=dict(
            showgrid=True, gridcolor=CHART_GRID_COLOR, zeroline=False, tickformat=".0f",
            title=dict(text="₹ Lakh", font=dict(size=11)),
        ),
    ))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def render_conduct_chart(conduct_counts: pd.Series) -> None:
    labels = conduct_counts.index.tolist()  # value_counts() is already sorted largest-first
    values = conduct_counts.values.tolist()

    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker=dict(color=ACCENT_COLOR),
        hovertemplate="%{y}: %{x}<extra></extra>",
    ))
    fig.update_layout(**_plotly_layout(
        margin=dict(l=10, r=20, t=10, b=30),
        # automargin lets Plotly reserve exactly as much left space as the longest label
        # needs (e.g. "Pre-closing consummation") - no truncation, no manual guessing.
        yaxis=dict(autorange="reversed", showgrid=False, automargin=True, tickfont=dict(size=11)),
        xaxis=dict(
            showgrid=True, gridcolor=CHART_GRID_COLOR, tickformat="d",
            title=dict(text="Orders", font=dict(size=11)),
        ),
    ))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


# ------------------------------------------------------------------------------------------
# Tab 2 - Penalty Benchmark
# ------------------------------------------------------------------------------------------

BENCHMARK_TABLE_COLUMNS = [
    "case_name", "decision_date", "provision",
    "penalty_43a", "penalty_44", "penalty_45", "total_penalty",
    "conduct_type", "verified", "source_url", "pdf_url",
]


def render_benchmark(df: pd.DataFrame) -> None:
    options = get_filter_options(df)
    f1, f2, f3 = st.columns(3)
    f_year = f1.multiselect("Year", options["year"])
    f_provision = f2.multiselect("Provision", options["provision"])
    f_conduct = f3.multiselect("Conduct type", options["conduct_type"])

    f4, f5 = st.columns(2)
    f_transaction = f4.multiselect("Transaction type", options["transaction_type"])
    f_cooperation = f5.multiselect("Cooperation", options["cooperation"])

    f_sector = []
    if options["sector"]:
        f_sector = st.multiselect("Sector", options["sector"])

    filtered = df.copy()
    for col, selected in [
        ("year", f_year), ("provision", f_provision), ("conduct_type", f_conduct),
        ("transaction_type", f_transaction), ("cooperation", f_cooperation), ("sector", f_sector),
    ]:
        if selected:
            filtered = filtered[filtered[col].isin(selected)]

    st.caption("Tick Include to build your benchmark set. No case is excluded automatically.")
    if filtered.empty:
        st.warning("No cases match the current filters.")
        edited = filtered.assign(Include=pd.Series(dtype=bool))
    else:
        table = filtered.copy()
        table.insert(0, "Include", table["include_default"])
        display_cols = ["Include"] + BENCHMARK_TABLE_COLUMNS
        edited = st.data_editor(
            table[display_cols],
            hide_index=True,
            width="stretch",
            disabled=[c for c in display_cols if c != "Include"],
            column_config={
                "Include": st.column_config.CheckboxColumn("Include", width="small"),
                "case_name": st.column_config.TextColumn("Case", width="large"),
                "decision_date": st.column_config.DateColumn("Date"),
                "penalty_43a": st.column_config.NumberColumn("43A", format="%.0f"),
                "penalty_44": st.column_config.NumberColumn("44", format="%.0f"),
                "penalty_45": st.column_config.NumberColumn("45", format="%.0f"),
                "total_penalty": st.column_config.NumberColumn("Total", format="%.0f"),
                "verified": st.column_config.CheckboxColumn("Verified"),
                "source_url": st.column_config.LinkColumn("Source", display_text="Open"),
                "pdf_url": st.column_config.LinkColumn("PDF", display_text="Open"),
            },
            key="benchmark_editor",
        )

    included = edited[edited["Include"] == True] if not edited.empty else edited  # noqa: E712

    st.divider()
    stats = compute_penalty_stats(included, "penalty_43a")
    render_hero_metric("Median Penalty", format_inr(stats["median"]))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Selected Cases", stats["count"])
    c2.metric("Mean / Average", format_inr(stats["mean"]))
    c3.metric("Minimum", format_inr(stats["min"]))
    c4.metric("Maximum", format_inr(stats["max"]))
    st.caption("Median is often more representative where exceptional penalties distort the average.")

    if not included.empty:
        st.divider()
        st.markdown("**Penalty by case (₹ Lakh)**")
        chart = included.set_index("case_name")[["penalty_43a", "penalty_44", "penalty_45"]].fillna(0) / 1_00_000
        st.bar_chart(chart)


# ------------------------------------------------------------------------------------------
# Tab 3 - Case Explorer
# ------------------------------------------------------------------------------------------

def render_case_explorer(df: pd.DataFrame) -> None:
    query = st.text_input(
        "Find a case", key="explorer_filter", placeholder="Search by case name or registration number",
        label_visibility="collapsed",
    )
    options_df = df
    if query:
        mask = (
            df["case_name"].str.contains(query, case=False, na=False)
            | df["combination_registration_no"].str.contains(query, case=False, na=False)
        )
        options_df = df[mask]

    if options_df.empty:
        st.warning("No cases match that search.")
        return

    labels = options_df.apply(lambda r: f"{r['case_name'][:70]} — {_date_str(r['decision_date'])}", axis=1)
    choice = st.selectbox("Select a case", options=options_df.index, format_func=lambda i: labels.loc[i], label_visibility="collapsed")
    row = df.loc[choice]

    st.divider()
    render_case_profile(row)


def render_case_profile(row: pd.Series) -> None:
    st.markdown(f"### {row['case_name']}")
    st.caption(f"{row['combination_registration_no'] or '—'} · {_date_str(row['decision_date'])} · Section {row['provision'] or '—'}")
    render_quality_badges(row)

    render_hero_metric("Total Penalty", format_inr(row["total_penalty"]))

    st.divider()
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Legal issue**")
        st.caption(row["legal_issue_summary"] or "Not extracted")
    with c2:
        st.markdown("**Conduct**")
        st.caption(row["conduct_type"] or "Not extracted")
    with c3:
        st.markdown("**Outcome**")
        st.caption(row["outcome_summary"] or "Not extracted")

    with st.expander("Penalty rationale"):
        st.write(row["penalty_rationale"] or "Not extracted")

    c4, c5 = st.columns(2)
    with c4:
        st.markdown("**Mitigating factors**")
        st.caption(row["mitigating_factors"] or "None identified")
    with c5:
        st.markdown("**Aggravating factors**")
        st.caption(row["aggravating_factors"] or "None identified")

    c6, c7 = st.columns(2)
    with c6:
        st.markdown("**Voluntary disclosure**")
        st.caption(row["voluntary_disclosure"] or "Not stated")
    with c7:
        st.markdown("**Cooperation**")
        st.caption(row["cooperation"] or "Not stated")

    st.divider()
    src_col, pdf_col, prov_col = st.columns([2, 2, 3])
    src_col.markdown(f"[CCI order page]({row['source_url']})" if row["source_url"] else "_Source not available_")
    pdf_col.markdown(f"[Original PDF]({row['pdf_url']})" if row["pdf_url"] else "")

    with st.expander("Source excerpts (provenance)"):
        if row["penalty_source_text"]:
            st.markdown(f"**Penalty finding** — page {row['penalty_source_page'] or '?'}")
            st.caption(row["penalty_source_text"])
        if row["intelligence_source_text"]:
            st.markdown(f"**Legal-factor finding** — page {row['intelligence_source_page'] or '?'}")
            st.caption(row["intelligence_source_text"])
        if not row["penalty_source_text"] and not row["intelligence_source_text"]:
            st.caption("No source excerpt captured for this case.")


# ------------------------------------------------------------------------------------------
# Tab 4 - Find Comparables
# ------------------------------------------------------------------------------------------

def render_comparables_tab(df: pd.DataFrame) -> None:
    mode = st.radio("Mode", ["Select an existing case", "Build a fact pattern"], horizontal=True, label_visibility="collapsed")

    base = None
    exclude_ids: set = set()

    if mode == "Select an existing case":
        labels = df.apply(lambda r: f"{r['case_name'][:70]} — {_date_str(r['decision_date'])}", axis=1)
        choice = st.selectbox(
            "Precedent case", options=df.index, format_func=lambda i: labels.loc[i],
            key="comparables_case_select", label_visibility="collapsed",
        )
        base_row = df.loc[choice]
        base = build_case_profile(base_row)
        exclude_ids = {base_row["order_id"]}
        st.caption(f"Base case: **{base['case_name']}** — {base['conduct_type'] or 'conduct unknown'}, {base['transaction_type'] or 'transaction type unknown'}")

    else:
        opts = get_filter_options(df)
        c1, c2, c3 = st.columns(3)
        conduct = c1.selectbox("Conduct", [""] + opts["conduct_type"])
        transaction = c2.selectbox("Transaction type", [""] + opts["transaction_type"])
        cooperation = c3.selectbox("Cooperation", ["", "Full", "Partial"])
        c4, c5, c6 = st.columns(3)
        closed_before = c4.selectbox("Closed before approval?", ["", "Yes", "No"])
        voluntary = c5.selectbox("Voluntary disclosure", ["", "Yes", "No"])
        delay_days = c6.number_input("Approx. delay (days)", min_value=0, value=0, step=1)

        base = {
            "order_id": None, "case_name": "Hypothetical fact pattern", "decision_date": None,
            "conduct_type": conduct or None, "transaction_type": transaction or None,
            "closed_before_approval": closed_before or None, "voluntary_disclosure": voluntary or None,
            "cooperation": cooperation or None, "delay_duration_days": float(delay_days) if delay_days else None,
            "penalty_43a": None, "mitigating_factors": None, "aggravating_factors": None,
        }

    st.divider()
    results = find_comparables(base, df, exclude_order_ids=exclude_ids, top_n=10)

    if results.empty:
        st.warning("No comparable cases could be scored — not enough overlapping data for this case or fact pattern.")
        return

    with st.expander("Adjust the comparable set"):
        results_table = results.copy()
        results_table.insert(0, "Include", True)
        edited = st.data_editor(
            results_table[["Include", "order_id", "case_name", "decision_date", "penalty_43a", "comparable_score"]],
            hide_index=True, width="stretch",
            disabled=["order_id", "case_name", "decision_date", "penalty_43a", "comparable_score"],
            column_config={
                "Include": st.column_config.CheckboxColumn("Include"),
                "comparable_score": st.column_config.NumberColumn("Score %", format="%.0f"),
                "penalty_43a": st.column_config.NumberColumn("Penalty 43A", format="%.0f"),
            },
            key="comparables_editor",
        )
    included_ids = set(edited.loc[edited["Include"] == True, "order_id"])  # noqa: E712
    shown = results[results["order_id"].isin(included_ids)]

    for _, r in shown.iterrows():
        with st.container(border=True):
            title_col, score_col = st.columns([5, 1])
            title_col.markdown(f"**{r['case_name'][:70]}**")
            score_col.markdown(f'<div class="cci-score">{r["comparable_score"]:.0f}%</div>', unsafe_allow_html=True)
            st.caption(f"{format_inr(r['penalty_43a'])} penalty · {_date_str(r['decision_date'])}")
            render_tags(r["reasons"])
            if r["source_url"]:
                st.markdown(f"[Open case / source]({r['source_url']})")

    st.divider()
    st.markdown("**Comparable-set benchmark**")
    stats = compute_penalty_stats(shown, "penalty_43a")
    st.caption(f"{stats['count']} comparable case(s)")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Median", format_inr(stats["median"]))
    c2.metric("Mean / Average", format_inr(stats["mean"]))
    c3.metric("Minimum", format_inr(stats["min"]))
    c4.metric("Maximum", format_inr(stats["max"]))


# ------------------------------------------------------------------------------------------
# Tab 5 - Corpus Search
# ------------------------------------------------------------------------------------------

def render_search_tab(df: pd.DataFrame) -> None:
    query = st.text_input(
        "Search", key="corpus_search_query", label_visibility="collapsed",
        placeholder="Search CCI penalty precedent… e.g. pre-closing integration, voluntary disclosure, delay in notification",
    )
    if not query:
        st.caption("Try: pre-closing integration · voluntary disclosure · delay in notification · share purchase agreement · exercise of control")
        return

    results = search_corpus(df, query, top_n=15)
    if results.empty:
        st.warning("No matches found.")
        return

    results = results.merge(df[["order_id", "conduct_type"]], on="order_id", how="left")

    st.caption(f"{len(results)} result(s)")
    for _, r in results.iterrows():
        with st.container(border=True):
            st.markdown(f"**{r['case_name'][:80]}**")
            st.caption(f"{_date_str(r['decision_date'])} · {format_inr(r['total_penalty'])}")
            if r["snippet"]:
                st.write(r["snippet"])
            if r.get("conduct_type"):
                render_tags([r["conduct_type"]])
            if r["source_url"]:
                st.markdown(f"[Open case / source]({r['source_url']})")


if __name__ == "__main__":
    main()
