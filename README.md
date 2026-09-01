# CCI Penalty Intelligence

*A living intelligence system for CCI gun-jumping and combination penalty precedent.*

## Problem

Competition lawyers currently review individual CCI penalty orders manually
to benchmark potential gun-jumping exposure - reading PDF after PDF to
answer questions like "what has CCI actually imposed for facts like mine?"
There is no structured, searchable, comparable record of the Section 43A/44
penalty corpus.

## Solution

CCI Penalty Intelligence turns the complete official Section 43A/44 order
corpus into structured, searchable and comparable precedent intelligence:
every order is discovered, downloaded, read, and conservatively parsed into
a dataset a lawyer can filter, benchmark, search, and interrogate - with a
citation back to the source PDF for every figure and every legal-factor
conclusion it draws.

## Key features

- **Complete official corpus** - all 68 orders currently published on
  CCI's Section 43A/44 listing (`data/orders.csv`), with a repeatable,
  incremental updater (`update_corpus.py`) for new orders as CCI publishes them.
- **Penalty analytics** - mean, median, min/max, quartiles, recalculated
  live as a lawyer includes/excludes any case.
- **Outlier-aware benchmarking** - a transparent, explainable statistical
  flag (IQR rule) - never a manual "this case is famous" label - with a
  side-by-side median-vs-mean comparison.
- **Comparable-case engine** - score every case against a precedent or a
  hypothetical fact pattern using a transparent, weighted, explainable
  scoring model (never an opaque AI similarity number).
- **Legal-factor intelligence** - conduct type, transaction type,
  voluntary disclosure, cooperation, mitigating/aggravating factors,
  penalty rationale, extracted conservatively from the order text itself.
- **Source provenance** - every extracted penalty figure and legal-factor
  conclusion carries a page number and a quoted excerpt from the order.
- **Corpus search** - fast, local, TF-IDF-style keyword search across case
  names, issues, rationale, and legal factors.
- **Incremental updater** - `python update_corpus.py` finds and ingests
  only new orders; nothing already on disk is re-downloaded or re-parsed.

## How to run it

```bash
# one-time setup
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# every time you want to use it
source .venv/bin/activate
streamlit run app.py
```

Opens at `http://localhost:8501`. Stop with `Ctrl+C`.

## Architecture

```
CCI website
   │  (src/crawler.py - the official listing's own JSON endpoint)
   ▼
PDF                          data/pdfs/
   │  (src/pdf_extract.py - PyMuPDF, no OCR yet)
   ▼
Extracted text                data/text/
   │  (src/extract_fields.py - objective penalty figures, regex + a
   │   conservative Indian-number-word parser, never an LLM)
   ▼
Structured data               data/orders.csv
   │  (src/intelligence.py - conduct/transaction type, mitigating/
   │   aggravating factors, rationale - pattern-based, provenance-backed)
   ▼
Legal intelligence            data/legal_intelligence.csv
   │  (src/data.py merges the two by order_id)
   ▼
Analytics / comparables / search
   (src/analytics.py, src/comparables.py, src/search.py - pure functions,
    no Streamlit, no network calls)
   ▼
Streamlit interface           app.py
   (Overview · Penalty Benchmark · Case Explorer · Find Comparables · Corpus Search)
```

Each arrow is a separate, independently re-runnable script or module:

| Step | Script/module | What it does |
|---|---|---|
| Discover + download | `src/crawler.py` | Calls CCI's own order-listing JSON endpoint; downloads each PDF once |
| Extract text | `src/pdf_extract.py` | PyMuPDF text layer extraction; flags scanned PDFs rather than guessing |
| Extract penalty figures | `src/extract_fields.py` | Regex + word-number parsing, single-provision and compound-provision aware |
| Extract legal factors | `src/intelligence.py` | Conservative pattern matching against controlled vocabularies, with provenance |
| Orchestrate a full run | `ingest.py` | Runs the above for the whole corpus, writes `orders.csv` |
| Orchestrate an update | `update_corpus.py` | Runs the above for only new orders since the last run |
| Build intelligence | `build_intelligence.py` | Runs `src/intelligence.py` over already-extracted text |
| Validate | `quality_check.py` | Duplicate IDs, impossible dates, penalty-sum mismatches, missing provenance |
| Serve the UI | `app.py` | Streamlit only - no scraping/extraction logic of its own |

## Trust model

**The CCI order remains the primary source.** Every screen in the app
repeats this. Every penalty figure and every legal-factor field carries a
page number and a quoted excerpt (`penalty_source_page`/`penalty_source_text`,
`intelligence_source_page`/`intelligence_source_text`) so it can be checked
against the PDF in one click. No case is ever silently excluded from a
benchmark - the lawyer always has an Include checkbox. No case is ever
labelled an outlier because it's well-known - only a transparent IQR rule
does that, and it's always overridable. No automatically extracted row is
ever shown as "human verified" - that flag is only ever set by a person
(`verified=True` is never written by the pipeline itself).

## Limitations

- **Automatic extraction, not legal judgment.** `src/extract_fields.py` and
  `src/intelligence.py` use pattern matching, not an LLM - they extract
  only what the order states in a form the patterns recognise, and leave a
  field blank (with a logged reason) rather than infer or guess. A blank
  cell means "not confidently extractable," not "doesn't apply."
- **No row has been manually verified yet.** All 68 real rows currently
  have `verified=False`. Treat every figure as a starting point for review,
  not a citation-ready fact.
- **Legal-factor coverage is uneven by design.** Conduct type is populated
  for ~94% of orders, but fields like `voluntary_disclosure` (~25%) or
  `transaction_value` (~4%) are populated only when the order states them
  in a recognisable way - low coverage there reflects the conservative
  extraction discipline, not a bug.
- **2 of 68 PDFs are scanned images** with no extractable text layer
  (`CCI-1104`, `CCI-1091`). OCR is not implemented; these rows are flagged
  `ocr_required` and excluded from any figure-based analysis until OCR is added.
- **Compound-provision orders** (e.g. listed under "43A and 44") only get a
  penalty broken out per section when the order text itself makes the
  split explicit - otherwise the figure is left blank rather than guessed.
- **Comparable scoring is a heuristic, not a legal opinion.** It surfaces
  and explains similarity across the specific factors it can measure - a
  lawyer's judgment about which case actually controls a fact pattern
  always governs.
- **No database.** `data/orders.csv` and `data/legal_intelligence.csv` are
  the only stores - no edit history, no concurrent-user support yet.
- **Updates are manual/scheduled, not real-time.** `update_corpus.py` must
  be run for the corpus to reflect newly published orders; the "Corpus
  last updated" indicator in the UI reflects the last run, not the present
  moment.

## Project structure

```
app.py                     Streamlit UI (5 tabs) - no data/network logic
ingest.py                  full-corpus ingestion pipeline runner
update_corpus.py           incremental updater - only new orders
build_intelligence.py      (re-)builds the legal-intelligence layer
quality_check.py           data-quality validation -> data/quality_report.csv

src/
    data.py                 loading, merging, validation, quality flags
    analytics.py             penalty statistics, IQR outlier flagging
    crawler.py                CCI listing discovery + PDF download
    pdf_extract.py            PDF -> text (PyMuPDF)
    extract_fields.py         objective penalty figure extraction
    intelligence.py           legal-factor extraction (conduct, rationale, etc.)
    comparables.py             explainable comparable-case scoring
    search.py                  local TF-IDF-style corpus search

data/
    orders.csv                the structured corpus (68 real orders)
    legal_intelligence.csv     the enrichment layer, linked by order_id
    pdfs/, text/                source PDFs and extracted text (not overwritten by updates)
    ingestion_errors.csv        every logged extraction issue, with reasons
    quality_report.csv          output of quality_check.py
    corpus_metadata.json        last-updated timestamp for the UI
    sample_orders.csv           the original fake sample rows, preserved for reference
```
