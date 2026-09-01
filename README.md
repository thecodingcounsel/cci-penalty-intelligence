# CCI Penalty Intelligence (V1.1)

A working tool for analysing penalties imposed by the Competition Commission
of India under **Sections 43A and 44** of the Competition Act (failure to
notify combinations / gun-jumping), built for a competition lawyer, not a
software engineer, to understand, run, and extend.

**V1** built the application and data schema against fake sample data.
**V1.1** adds a real, repeatable ingestion pipeline, tested end to end on
the 10 most recent real orders. See "Current limitations" below - this is
still a small, manually-triggered test batch, not the full corpus.

## What it does

- Loads a table of CCI 43A/44 orders. `data/orders.csv` currently holds
  **10 real orders** ingested from the official CCI orders page - see
  "How the real data was ingested" below. The original fake sample rows
  used to build/test the app are preserved at
  `data/samples/orders_fake_sample.csv`.
- Lets you filter by provision, year, conduct type, transaction type, and
  sector.
- Lets you tick/untick **any** case in or out of your working set - nothing
  is hardcoded to be excluded.
- Recalculates Section 43A penalty statistics (count, mean, median, min,
  max, total, 25th/75th percentile) live as you change your selection.
- Lets you toggle outlier exclusion, and shows a side-by-side comparison of
  the average penalty with and without flagged outliers.
- Shows a bar chart of penalties by case, and a detail table with every
  field, including a short extract of the source text and page number
  behind each penalty figure, plus links to the source order and PDF.
- Formats rupee figures in lakh/crore form, as used in Indian legal practice.
- Displays a standing disclaimer: **this tool is an analytical aid, the CCI
  order is the primary source.**

## How to run it

From the project folder:

```bash
# one-time setup
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# every time you want to use it
source .venv/bin/activate
streamlit run app.py
```

This opens the app in your browser, usually at `http://localhost:8501`.
Stop it with `Ctrl+C` in the terminal.

## How the real data was ingested

`data/orders.csv` was produced by running:

```bash
source .venv/bin/activate
python ingest.py            # ingests the 10 most recent real orders
```

This is a separate, manually-run script - it is **not** triggered by the
app itself, and it refuses to run for more than 10 orders (a deliberate
guard for this test phase; see "V2 roadmap"). Re-running it is safe: PDFs
already on disk are not re-downloaded, and it always leaves `verified=False`
on every row it writes, because nothing it extracts has been checked by a
human against the source order yet.

## Architecture

```
                                    ┌─ src/crawler.py  ──┐
                                    ├─ src/pdf_extract.py│
ingest.py  (pipeline runner)  ──────┤                    ├──►  data/orders.csv
                                    └─ src/extract_fields.py

data/orders.csv  ──►  src/data.py  ──►  src/analytics.py  ──►  app.py
  (real data)         (load & clean)      (pure statistics)      (UI only)
```

Ingestion and the app are two separate paths that only meet at
`data/orders.csv`. `app.py` never imports the crawler, PDF, or
field-extraction modules - it only ever reads the CSV.

- **`data/orders.csv`** - the dataset. One row per penalty order. Penalties
  under Sections 43A, 44 and 45 are kept in **separate columns**
  (`penalty_43a`, `penalty_44`, `penalty_45`) plus a `total_penalty` column,
  because a single case can attract penalties under more than one
  provision and collapsing them into one number would misrepresent that.
  `penalty_source_page` and `penalty_source_text` hold a page number and a
  short quoted excerpt for every penalty figure, so a lawyer can check it
  against the order without re-reading the whole document.

- **`src/crawler.py`** - the only module that talks to cci.gov.in. Calls
  the JSON endpoint the official orders page's own table uses to discover
  orders (`discover_orders()`), and downloads each order's PDF
  (`download_pdfs()`), skipping any file already on disk.

- **`src/pdf_extract.py`** - turns a downloaded PDF into plain text using
  PyMuPDF (`extract_text()`), flags a PDF as `likely_scanned` if it has
  almost no extractable text (no OCR is implemented), and saves the text
  to `data/text/<order_id>.txt` (`save_text()`).

- **`src/extract_fields.py`** - pulls the penalty amount out of the order
  text with a regular expression, with no LLM and no invented data. It
  only fills in a figure when it is unambiguous - see "How a penalty gets
  into the DataFrame" below. Anything less clear-cut is left blank with the
  reason recorded, so a blank cell always has an explanation.

- **`ingest.py`** - the pipeline runner. Chains the three modules above,
  builds a full-schema row per order (interpretive fields like
  `conduct_type` left blank - see "Current limitations"), and writes
  `data/orders.csv`. This is the one file that knows the *order* the
  pipeline steps run in; it contains no scraping or extraction logic
  itself.

- **`src/data.py`** - the only module that knows the *app's* data lives in
  a CSV file. It validates that all required columns are present, parses
  dates and numbers, converts True/False-style text to real booleans, and
  fills in sensible defaults for blank cells so a messy row never crashes
  the app. It exposes `load_orders()` and `get_filter_options()`.

- **`src/analytics.py`** - pure functions that take a DataFrame and return
  numbers: `compute_penalty_stats()`, `exclude_outliers()`,
  `compare_outlier_impact()`, and `format_inr()` (lakh/crore formatting).
  Nothing here imports Streamlit, so these functions can be reused later by
  a script, an API, or a RAG answer without any UI dependency.

- **`app.py`** - the Streamlit UI. It calls `load_orders()` once, renders
  the sidebar filters and the editable case table, then calls the
  analytics functions on whatever subset of cases is currently selected.
  It contains no data-loading, scraping, or statistical logic of its own.

### 1. How crawler.py finds an order

The official orders page (`cci.gov.in/combination/orders-section43a_44`)
renders its table with a JavaScript library (DataTables) that fetches its
rows from a JSON endpoint on the same URL. `discover_orders()` calls that
same endpoint directly (`requests.get` with the page size and sort
DataTables itself would send) and gets back structured JSON - the
combination number, party names, decision date, section, and a reference
to the order's PDF - for the most recent orders, newest first. No HTML
scraping or page-layout guessing is involved.

### 2. How it finds the PDF

Each JSON row includes an `order_file_content` field: an HTML-entity-encoded
JSON string naming the PDF's path on the server, e.g.
`images/caseorders/en/order1779368902.pdf`. `_extract_pdf_url()` decodes
that and prefixes it with `https://www.cci.gov.in/` to get the direct,
downloadable PDF URL - the exact file the "View Documents" link on the
website points to.

### 3. Where the PDF is saved

`download_pdfs()` saves each PDF to `data/pdfs/<order_id>.pdf`, where
`order_id` is `CCI-<CCI's internal order id>` (e.g. `CCI-1760`). If that
file already exists, it is not re-downloaded - re-running `ingest.py` is
cheap and safe.

### 4. How pdf_extract.py turns the PDF into text

`extract_text()` opens the PDF with PyMuPDF and calls `page.get_text()` on
every page, which reads the PDF's embedded text layer (the characters the
PDF was built from - not an image). If the whole document comes back with
under ~200 characters of text, it's flagged `likely_scanned=True`, because
that usually means the PDF is a scanned image with no embedded text layer,
which PyMuPDF cannot read and which V1.1 does not attempt to OCR. All 10
orders ingested so far had normal embedded text - none were scanned.

### 5. How a penalty gets from the CCI PDF into the DataFrame

`extract_penalty_info()` in `src/extract_fields.py` does this in one
narrow, verifiable way:

1. It only proceeds if the order's officially listed provision (from the
   CCI JSON, not guessed) is a *single* recognised section - `43A`, `44`,
   or `45`. A compound listing like `43A+44` is left blank, because there
   would be no safe way to know which figure belongs to which section from
   the text alone.
2. It searches the extracted text for the phrase `penalty of INR <amount>`
   (a phrasing every one of the 10 real orders used verbatim in its
   concluding paragraph). If **exactly one** such phrase is found, that
   amount is parsed and written to the matching column (`penalty_43a`,
   `penalty_44`, or `penalty_45`), and `total_penalty` is set to the same
   value.
3. If the phrase is found **zero times**, it checks for the equally
   explicit sentence "the Commission decides not to impose any penalty" -
   if present, the penalty is recorded as `0`, not left blank, because the
   order did explicitly decide the question.
4. If the phrase is found **more than once**, or neither pattern matches
   at all, the penalty is left blank and the reason is recorded (visible in
   the `notes` column) - the pipeline never guesses which figure is the
   real one.
5. Whichever figure is used, a ~300-character excerpt around it is saved to
   `penalty_source_text`, and the page it appears on is located and saved
   to `penalty_source_page` - so every non-blank penalty figure carries its
   own citation back into the order.

For the first 10 real orders, this matched cleanly for all 10 (nine
positive penalties, one explicit zero) - see "What worked / what didn't"
below for the batch's actual field-by-field outcome.

### Data flow in plain English (app side)

1. `app.py` asks `src/data.py` to load and clean `data/orders.csv`.
2. The sidebar filters (provision/year/conduct/transaction/sector) narrow
   that down to a "filtered" set.
3. You tick/untick the `Include` checkbox per case in the table (defaulted
   from `include_default`, but always changeable) to build a "selected" set.
4. Optionally, the outlier toggle removes rows flagged `is_outlier = True`
   from that selected set.
5. `app.py` hands the final selected set to `src/analytics.py`, which
   computes the Section 43A statistics, the chart data, and the
   outlier-impact comparison shown on screen.

## What worked / what didn't (10-order test batch)

All 10 orders were from `cci.gov.in`'s live listing (ids 1312, 1313, 1462,
1518, 1520, 1565, 1603, 1664, 1688, 1760), decision dates 22 Aug 2023 to 20
May 2026.

**Extracted successfully for all 10 orders:** `order_id`,
`combination_registration_no`, `case_name`, `decision_date`, `provision`,
`source_url`, `pdf_url`, `penalty_43a`/`total_penalty` (nine orders got a
positive figure; one, CCI-1520 / Torrent Power, got an explicit `0` because
the Commission decided not to impose a penalty), `penalty_source_page`,
`penalty_source_text`.

**Left blank for all 10 orders (by design, not by failure):**
`conduct_type`, `transaction_type`, `sector`, `notification_issue`,
`gun_jumping_type`, `voluntary_disclosure`, `cooperation`,
`mitigating_factors`, `aggravating_factors` - these require legal
interpretation and Phase 4 explicitly excluded them from automated,
non-LLM extraction. `penalty_44` and `penalty_45` are also blank for all
10, because all 10 orders' officially listed provision was `43A` only.

**PDF formatting differences observed:** page counts ranged from 10 to 22
pages; all 10 PDFs had a normal embedded text layer (none were flagged
`likely_scanned`); the operative penalty sentence used two slightly
different phrasings across the batch - "the Commission decides to impose a
penalty of INR X" and "the Commission decides to take a lenient view and
impose a nominal penalty of INR X" - both matched by the same regex because
it only anchors on `penalty of INR <amount>`, not the full sentence.

**Scanned PDFs:** none in this batch.

**App test:** `app.py` was run unmodified against the resulting
`data/orders.csv` (via Streamlit's `AppTest` harness and a live local
server) - it loaded all 10 rows, filters and the outlier toggle worked,
and the Section 43A statistics matched a manual calculation from the CSV.

## Current limitations

- **Only 10 real orders are loaded**, out of the full CCI 43A/44 corpus.
  This was a deliberate test batch for the ingestion pipeline, not a start
  on the full dataset - `ingest.py` refuses to run for more than 10 orders
  as a guard against scaling up by accident.
- **No row has been verified yet.** Every real row has `verified=False`.
  The `penalty_source_page`/`penalty_source_text` columns exist precisely
  so a human can check each figure quickly, but that review hasn't
  happened - do not cite any figure from this file before doing so.
- **Interpretive fields are all blank** for the real rows (see above) -
  populating them (by hand, or later by an LLM step) is separate work.
- Penalty extraction only handles the exact phrasing CCI happened to use in
  this batch (`penalty of INR <amount>`, and the explicit "decides not to
  impose any penalty" sentence). A future order phrased differently would
  correctly come back blank with a reason - not wrong - but the regex will
  likely need new patterns added as more orders are ingested.
- Only `penalty_43a`/`penalty_44`/`penalty_45` under a *single*, cleanly
  listed provision are extracted. An order officially listed under a
  compound provision (e.g. `43A+44`) is not attempted yet.
- No OCR - a scanned PDF (none appeared in this batch) would currently
  come through with `likely_scanned=True` in the pipeline's console output
  and no extracted penalty, not a fabricated one.
- Statistics are calculated only on `penalty_43a` in the main analytics
  panel, per the V1 spec. `penalty_44` and `penalty_45` are captured in the
  schema and shown in the tables/chart but do not yet have their own
  summary panel.
- There is no database - `data/orders.csv` is the only store, so there is
  no history of edits and no concurrent-user support.
- cci.gov.in's TLS certificate is missing an intermediate certificate, so
  `src/crawler.py` uses the `truststore` package to validate via the
  operating system's trust store (the same thing a browser does) instead
  of Python's bundled certificate list. This is a workaround for a
  misconfiguration on CCI's server, not a relaxation of certificate
  checking.

## V2 roadmap

- **Scale past 10 orders** - raise (or remove) `ingest.py`'s 10-order guard
  and paginate `discover_orders()` through the full listing.
- **Claude API extraction** - turn extracted text into the interpretive
  fields (`conduct_type`, `mitigating_factors`, etc.) that Phase 4
  deliberately left blank, and to handle penalty phrasings the regex in
  `src/extract_fields.py` doesn't recognise.
- **OCR** - handle any order that comes back `likely_scanned=True`.
- **SQLite** - replace `data/orders.csv` as the store; only
  `src/data.py::load_orders()` needs to change, since it is the sole
  point of contact with the storage layer.
- **Automated updates** - a scheduled job running `ingest.py`-style logic,
  instead of a manual `python ingest.py` invocation.
- **Precedent status** - track whether an order has been appealed,
  stayed, or set aside (a new column plus a filter, additive to the
  existing schema).
- **RAG / question answering** - natural-language queries over the order
  text in `data/text/`, built on top of the same `src/analytics.py`
  functions, without touching how the table or filters work.

## Assumptions and technical debt

- `provision` is a free-text label (e.g. `"43A"`, `"43A+44"`) rather than a
  normalised multi-value field. This is simple for V1 but will need a
  proper multi-value structure once real data volume grows.
- Outlier flagging (`is_outlier`) is manual, not statistically derived
  (e.g. not an IQR/z-score rule). That is a deliberate simplification - a
  lawyer's judgment about what counts as an outlier is more defensible
  than an automatic rule until there is enough real data to calibrate one.
  All 10 real rows currently have `is_outlier=False` by default.
- `include_default` lets the dataset ship with a suggested starting
  selection, but every row is always user-togglable in the UI - nothing is
  permanently excluded in code. All 10 real rows default to `True`
  (included) regardless of whether extraction found every field, since
  penalty data (the figure the app analyses) was successfully extracted
  for all 10.
- The penalty-amount regex in `src/extract_fields.py` is intentionally
  narrow rather than clever, per the "do not guess" requirement - it will
  under-extract (leave things blank) on unfamiliar phrasing rather than
  risk over-extracting (attaching the wrong figure). Expect blanks to
  appear as the corpus grows, each with a stated reason in `notes`.
- No automated tests are included yet. Given the small, pure-function
  surface of `src/analytics.py` and `src/extract_fields.py`, adding
  `pytest` tests there would be a low-cost, high-value next step before
  scaling past 10 orders.
