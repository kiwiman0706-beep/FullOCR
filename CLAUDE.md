# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FullOCR converts self-scanned (自炊) Japanese technical-book PDFs into structured Markdown
(with figures and a table of contents) for use in Obsidian. It combines:

- **NDLOCR-Lite** — Japanese/vertical-text OCR (CPU-only, no GPU required) — the source of truth
  for *text*.
- A **structure engine** (`docling` default/recommended, or `mineru`, or `marker`) — layout
  analysis only (headings, figure regions, tables, reading order). Its own OCR text is not used.

These two outputs are merged by bounding-box overlap into a common intermediate representation,
then rendered to Markdown.

Status: Phase 1 (core pipeline scaffolding), Phase 1.5 (real NDLOCR-Lite/Docling wiring, verified
end-to-end against two full real books, 448 pages) and most of Phase 2 (Obsidian-output polish)
are done. A first-cut local GUI (drag-and-drop batch conversion, `fullocr gui`) exists but hasn't
been packaged into a Windows exe yet (that's Phase 4). See "Known-abandoned approaches" below
before trying to re-solve chapter splitting the same ways already ruled out.

## Commands

```bash
uv venv
uv pip install -e ".[dev]"           # core + pytest/ruff, enough to run the test suite
uv pip install -e ".[docling,pdf]"   # + recommended engine (docling) and figure-cropping (PyMuPDF)
# uv pip install -e ".[mineru]" / ".[marker]" instead of docling to try an alternate engine
# docling / mineru / marker are declared mutually exclusive uv extras (pyproject.toml [tool.uv.conflicts])
#   — install only one at a time.

uv run pytest -q                     # full suite (synthetic fixtures, no real OCR binaries needed)
uv run pytest tests/test_engines.py  # docling/mineru adapter parsing tests only
uv run pytest -k test_ndl_parser_reads_lines   # single test by name
uv run ruff check .

fullocr init-config fullocr.config.json   # write a config template (tune engine + NDL/engine command lines here)
fullocr run book.pdf -c fullocr.config.json
fullocr run ./books --folder -c fullocr.config.json   # batch a folder; skips already-processed, retries failures
fullocr run book.pdf --no-engines          # re-merge/re-render from existing work/ JSON without re-running OCR (fast — reuses cached engine JSON, use this after any pipeline/merge/render code change instead of re-running the real OCR)
fullocr render work/book/ir.json -c fullocr.config.json   # regenerate Markdown from an (e.g. GUI-corrected) IR JSON
fullocr gui                                # launch the local drag-and-drop batch-conversion GUI (pywebview + FastAPI)
```

NDLOCR-Lite itself is not pip-installable; install per the official
[ndl-lab/ndlocr-lite](https://github.com/ndl-lab/ndlocr-lite) distribution (`uv tool install .` from
a clone gives a global `ndlocr-lite` command — this is what `NDLConfig.command` defaults to).
Docling installs normally via `uv pip install -e ".[docling,pdf]"`. Both command defaults in
`config.py` are confirmed working against real installs, not placeholders.

MinerU/Marker cannot share a venv with Docling or each other (pyproject.toml declares them mutually
exclusive `[tool.uv] conflicts`, and their real pip dependency trees genuinely conflict — e.g.
marker needs `pillow<11`, mineru needs `pillow>=11`). To try either, build a separate venv:
`uv venv .venv-mineru && uv pip install --python .venv-mineru/bin/python "mineru[pipeline]"` (the
bare `mineru` extra pulls in neither `torch` nor `six`, both required — install `mineru[pipeline]`
then `six` explicitly). Marker: `uv pip install --python .venv-marker/bin/python "marker-pdf>=1.0"`.
Both engines were A/B/C-tested against Docling on a real chapter and scored identically (Gemini
judge, same page) — Docling remains the default for its lighter install and faster CPU runtime, not
because it's more accurate.

## Architecture

### The IR is the spine

Every stage communicates through `fullocr.ir.Document` — a JSON-serializable tree of
`Page`/`Block` with pixel-space bounding boxes (top-left origin). This is deliberate: a planned
GUI (Phase 3) needs to let a user fix reading order/block type/typos at the *block* level, not by
hand-editing final Markdown, then re-render. Because the structure engine is treated as "a
classifier that emits JSON," not "a thing that emits Markdown," Docling/MinerU/Marker are
interchangeable underneath the same IR — `PipelineConfig.engine` selects which one runs.

Pipeline shape (`batch.build_ir_for_pdf`):

```
NDLOCR-Lite (text, per-line bbox)  ─┐
                                     ├─▶ pipeline/merge.py ─▶ ir.Document ─▶ render/markdown.py
structure engine (layout only)  ────┘      (build_document)
```

- `pipeline/ndl.py` — parses NDLOCR-Lite JSON into `NDLPage`/`NDLLine`. Confirmed against real
  `--sourcepdf`/`--sourceimg` output: the top-level shape is `{"contents": [[line,...], ...],
  "pages": [{"img_width","img_height",...}, ...]}` for multi-page PDFs (or `"imginfo"` singular for
  a single image) — *not* the `{"pages": [{width,height,contents}]}` shape the alias tables were
  originally written to expect. `parse_ndl_json` special-cases the real shape first and falls back
  to the alias-table logic for other inputs. Lines carry `confidence`; `merge.py` drops lines below
  `NDLConfig.min_confidence` (default 0.5) before assignment — this catches some but not all OCR
  garbage (a line can be high-confidence and still be a repeated-character misread; that's not
  caught by anything today).
- `pipeline/docling.py` / `pipeline/mineru.py` / `pipeline/marker.py` — each parses its engine's
  native JSON into the shared `pipeline/layout.py` types (`LayoutPage`/`LayoutBlock`). This is the
  common contract `merge.py` depends on, so adding a 4th engine only means writing one more adapter
  that emits `LayoutPage`. All three were confirmed against real output; Docling and MinerU both
  return a **flat heading level** for every heading (Docling: always `1`; MinerU: always `2`) rather
  than a real hierarchy — this is why chapter splitting is off by default (see below).
  `docling.py` also reclassifies `section_header`-labeled blocks whose text looks like a figure/table
  caption (`^[図表]\s*\d`) as `CAPTION`, because Docling itself is inconsistent about which label it
  picks for captions in the same document. Table blocks carry a `table_cells` grid (row/col
  offsets + spans, from Docling's `data.table_cells`) that `merge.py` uses to rebuild an actual
  Markdown table instead of flattened text.
- `pipeline/merge.py` (`build_document`) — for each page, assigns NDL text lines to layout blocks
  by normalized bbox overlap (`PipelineConfig.assign_overlap_threshold`); text reading order within
  a block accounts for vertical (縦書き, right-to-left column) vs horizontal Japanese layout.
  Unassigned NDL lines are rescued rather than dropped, classified as `PAGE_HEADER`/`PAGE_FOOTER`
  if they sit in the top/bottom 8% of the page (`_rescued_line_type`) and `BODY` otherwise. Table
  blocks get their cell-level NDL text re-assigned by the same overlap logic, one cell at a time
  (`_render_table_markdown`), and short "label" fragments like a boxed callout's "極論1" badge get
  paired with their nearby text block by *vertical bbox proximity*, not reading-order adjacency —
  real documents put the label column and the text column in different reading-order clusters
  (`_merge_label_pairs`). If the structure engine produced nothing, falls back to NDL-only (one
  block per line, position-ordered).
- `pipeline/textclean.py` — **rule-based (free, deterministic) body cleanup**, runs after merge
  when `PipelineConfig.text_cleanup` (default on). Joins consecutive `BODY` blocks whose previous
  block doesn't end on sentence-final punctuation — *across page boundaries* (Markdown has no page
  concept, but OCR splits paragraphs at page breaks), drops nonbre/柱 noise (digit/symbol-only short
  fragments), normalizes whitespace. Headings/figures/captions break the join so structure is kept.
- `pipeline/structure_llm.py` + `llm/` — **optional LLM structure pass** (runs in
  `batch.build_ir_for_pdf` after merge, before render; off unless `StructureLLMConfig.enabled`
  AND an API key is present, else a silent no-op). This is the *chosen* fix for the flat-heading
  problem (see Known-abandoned below): Docling/MinerU return a single heading level, so the Obsidian
  outline is flat. The pass sends the heading list + each heading's following body snippet + the
  document's head text (which usually contains the printed 目次) to an LLM, which returns a real
  H1–H6 hierarchy and marks layout false-positive headings for demotion to `BODY`. It also writes a
  `_structure_review.md` QA report (outline-vs-printed-TOC agreement, structure critique). `llm/`
  is provider-agnostic: `llm.base.LLMClient` (a `generate(prompt)` Protocol) + `make_client()`
  factory; `llm.gemini.GeminiClient` (google-genai, `.[llm]` extra) is the first provider, swappable
  via `StructureLLMConfig.provider` / GUI. The real LLM call runs on the user's machine; all the
  parse/apply/report logic is unit-tested with a fake client (`tests/test_structure_llm.py`). The
  review string is persisted on `DocMeta.structure_review` so `_write_structure_review` can emit it
  after `render_document` (which clears `*.md` first). Beyond hierarchy+review, the same pass has
  three more opt-in (token-costing) features, each its own `StructureLLMConfig` flag: `anomaly_check`
  (report weird line breaks / page skips — cheap "page-boundary contexts" by default, `anomaly_full_text`
  for the whole book), `rewrite_text` (LLM tidies each `BODY` paragraph's OCR line breaks/typos without
  touching headings/figures/tables, so structure+images are preserved — output tokens make this the
  pricey one), and `figure_alt_text` (multimodal: crops each `FIGURE` from the page image, asks the
  model for an alt/description stored on the block and rendered as a searchable caption under the image).
  The vision path uses an optional `describe_image(bytes, prompt)` method on the client (checked via
  `hasattr`), implemented by `GeminiClient`.
- `render/markdown.py` + `render/toc.py` — walks the IR to produce `00_index.md` (front-matter +
  TOC) plus, if `RenderConfig.split_by_heading_level` is set, one Markdown file per chapter; crops
  figure regions out of rendered page images (needs `Pillow` + a `Page.image_path`, scaled by
  `page_image.size / page.width,height` since the two are rendered at different DPIs — degrades to
  a placeholder blockquote if no page image exists), and uses Obsidian wikilinks for images/TOC by
  default. `render_document` clears stale `*.md` files and `images_dirname/*` from `out_dir` before
  writing — needed because re-rendering the same book with different render options (e.g. via
  `--no-engines`) used to leave old split files behind.
- `batch.py` orchestrates a full run per PDF: run NDL → run structure engine on NDL's transparent
  text-PDF (falls back to the original PDF if absent) → render page PNGs via PyMuPDF (optional,
  degrades to no figures) → parse both → merge → save `work/<book>/ir.json` → render to
  `out/<book>/`, then copies the NDL transparent-text PDF into `out/<book>/` too. `process_folder`
  adds skip-existing and per-file retry with backoff.
- `gui/` — a first-cut local GUI: `gui/server.py` (FastAPI: `/api/convert` accepts multipart PDF
  uploads + option checkboxes as form fields, runs `batch.process_folder` in a background thread,
  `/api/jobs` is polled for progress), `gui/static/index.html` (drag-and-drop + checkboxes, vanilla
  JS, polls every 1.5s), `gui/app.py` (launches uvicorn in a thread, opens a `pywebview` window
  pointing at it). Launch with `fullocr gui`. Not yet packaged as an exe (Phase 4). The Linux dev
  box here can't render the pywebview window itself (QtWebEngine needs `libasound.so.2`, not
  installed, no sudo) — verify GUI changes by hitting the FastAPI endpoints directly
  (`fastapi.testclient.TestClient`, or run `uvicorn fullocr.gui.server:app` and `curl`/browser
  against `http://127.0.0.1:8756`) rather than trying to screenshot the desktop window.

### Engine adapters are execution + parsing, not just parsing

Each `pipeline/<engine>.py` module pairs a `run_<engine>()` (shells out via `subprocess.run`,
formatting `cfg.command` with `{input}`/`{outdir}`, both `shlex.quote`d — self-scanned filenames
routinely contain spaces and `!`/`,`, which broke unquoted `shell=True` commands) with a
`load_<engine>_dir()`/`parse_<engine>_json()`. `tests/` validate the parse/merge/render logic
against synthetic JSON fixtures (see `tests/test_pipeline.py`, `tests/test_engines.py`); real-engine
behavior has also been verified end-to-end against two full books (448 pages) — see the phase
1.5/2 verification artifact linked from project memory/chat history if you need the detailed
findings, they aren't duplicated here.

### Known-abandoned approaches (don't redo these without new information)

- **Deriving heading levels from block bbox height, to fix Docling/MinerU's flat levels.** Tried
  twice: linear min-max scaling collapses almost everything into the lowest level because a couple
  of huge decorative cover-page text blocks stretch the range; percentile/rank-based bucketing
  avoids that but then mechanically promotes ~1/3 of *all* headings (not just the ~11 real chapter
  titles out of ~300) to the top level, producing worse over-splitting than doing nothing. Reverted
  both times — `assign_heading_levels` only fills in levels the engine left `None`, it doesn't
  override. Chapter-level splitting (`RenderConfig.split_by_heading_level`) is off by default as a
  result. A future fix likely needs a different signal than bbox height (page position, distance
  from other headings, a small classifier) — or just wait for the Phase 3 GUI so a human marks
  chapter starts once per book. **Update:** the "different signal" turned out to be an LLM —
  `pipeline/structure_llm.py` now assigns the hierarchy (and demotes false headings) when enabled.
  The bbox-height heuristic in `merge.assign_heading_levels` is still there but only fills `None`s;
  the LLM pass overrides levels wholesale when it runs.

### Config drives everything environment-specific

`fullocr.config.PipelineConfig` holds per-engine `command` templates, glob patterns for locating
output JSON, and render options. The intent (see README roadmap) is that a future PyInstaller-based
Windows exe only needs to swap these `command`/path values to point at bundled binaries — the
adapters themselves shouldn't need to change.
