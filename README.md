# latexa11y

Convert LaTeX instructional materials into tagged, **ADA Title II / WCAG 2.1 AA /
PDF-UA** conforming PDFs — deterministically, without AI in the conversion path.

Built for the UC Berkeley EECS 16A question bank, but nothing in the Python
package is course-specific: a course is onboarded by writing a YAML profile.

---

## What it gives you

| | |
|---|---|
| **A tagged template layer** | `latexa11y-assignment`, `-exam`, `-worksheet` classes producing a real `H1 → H2 → H3 → H4` hierarchy that appears in PDF bookmark panes and screen readers |
| **Strict alt text** | `Described` wraps *any* region — text, math, TikZ, tables — so assistive technology reads only the description and never the contents |
| **Deterministic descriptions** | pgfplots axes, circuitikz netlists and state machines are described from source with no model in the loop |
| **A Markdown worklog** | staff or an AI fill in descriptions; machine sections regenerate, human text never gets overwritten |
| **A conformance checker** | source lint, build-log analysis and PDF structure assertions, each mapped to a Matterhorn checkpoint or WCAG SC |
| **An agent harness** | every command speaks `--json`; agents propose descriptions and humans approve them |
| **A legacy shim** | existing `\qns` / `\qitem` / `\sol` question files keep compiling |

---

## Quick start

**→ Full step-by-step instructions, including which directory to run each
command from and what you should see: [`docs/QUICKSTART.md`](docs/QUICKSTART.md).**

Install once, from this repository:

```bash
cd "/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool"
python3 -m pip install -e '.[all]'
```

Then, from **any** directory:

```bash
# 0. Will this toolchain actually produce a conforming PDF? Run this first.
latexa11y doctor

# 1. Convert. Pick a scope, choose which standards to apply, say where the
#    output goes. Dry run by default; your corpus is never touched.
latexa11y -p eecs16a run
```

That is the whole tool for most people. The runner writes everything it
produces under one directory you choose:

```
a11y-out/
  pdf/           the converted PDFs
  logs/          build logs
  tex/           the converted sources
  descriptions/  the worklogs staff fill in   ← the alt-text log
  baseline/      the untouched originals, for the before/after comparison
  run.yaml       this run's settings, replayable
```

and reports, per assignment, how far the page moved:

```
assignment    variant    pages  bookmarks  figures  errors  warnings  pixel diff
sp26/hw/9     solution      13         48        0       0         0       2.34%
sp26/hw/9     problem        8         48        0       0         0       2.38%
sp26/dis/09A  solution       5         21        0       0         0       1.44%
sp26/dis/09A  problem        3         21        0       0         0       1.93%
sp26/dis/09A  answer         4         21        0       0         0       1.70%
```

Every version of each assignment is converted, not just the solutions. `sol9.tex`
and `prob9.tex` share one body and differ only in whether `\sol` prints, and the
blank one is what students actually receive.

`pixel diff` is a *difference*, not a score: 2.34% means 97.66% of the page is
pixel-identical to the original, and the residue is tagging's own repagination.

Everything the menus do has a flag, and both routes call the same engine — so a
run can be explored interactively, saved, and replayed unchanged in CI:

```bash
latexa11y -p eecs16a build sp26/hw/9 --write --question-tags
latexa11y -p eecs16a build --config a11y-out/run.yaml --write --json
```

The individual stages remain available when you want them:

```bash
latexa11y -p eecs16a files bank            # what is in scope
latexa11y -p eecs16a scan bank             # figures -> Markdown worklogs
latexa11y -p eecs16a agent next-task -n 5  # what an agent should describe next
latexa11y -p eecs16a apply bank --write    # approved descriptions -> .tex
latexa11y -p eecs16a check bank --pdf out.pdf --log out.log
```

Exit codes: `0` clean, `1` findings, `2` could not run.

---

## Why `doctor` runs first

LaTeX accessibility fails **silently** by default. On TeX Live 2025:

* `\DocumentMetadata{pdfstandard=ua-1}` raises `unknown-standard` and the build
  continues, producing a PDF that claims nothing.
* `testphase=phase-IV` and `testphase=latest` do not exist; the loader warns and
  carries on, producing an **untagged** PDF that looks perfect.
* An unfilled alt placeholder ships as real `/Alt` text and *passes* both a
  naive "every Figure has /Alt" check and veraPDF.

Each of those yields a document that appears converted and is not. For material
under a legal obligation a false pass is worse than a hard error, so `doctor`
refuses to run the pipeline when the toolchain cannot deliver what the profile
asks for, and names the missing capability.

**TeX Live 2026 is required to declare PDF/UA conformance.** TL2025 tags
documents but cannot claim conformance in metadata; `doctor` says so plainly.

---

## The alt-text mechanism

The requirement is to wrap an arbitrary region so a reader announces *only* the
description. Three mechanisms could do that; only one works:

* **`/ActualText`** — spec-correct, implementation-broken. Acrobat corrupts long
  strings, Chrome truncates at 16383 characters, Firefox / MuPDF < 1.27 /
  PDF-XChange / SumatraPDF ignore it, and it **cannot nest**. Rejected.
* **`/Alt` alone** — a description, not a replacement. It does not stop a reader
  descending into tagged content inside the region.
* **Artifact** — content absent from the structure tree, skipped unconditionally.
  The only fully reliable suppression primitive in PDF.

`Described` combines the last two, which is the construction tagpdf itself uses for
TikZ: open a `Figure` carrying `/Alt`, open one marked-content leaf, then
**suspend tagging** for the body.

```latex
\begin{Described}{Two capacitors C1 and C2, each from its own top node to
ground, joined by switch S1.}
  \begin{circuitikz} ... \end{circuitikz}
\end{Described}

\begin{Decorative}\includegraphics{banner.jpg}\end{Decorative}  % speaks nothing
```

**Order is load-bearing.** The Figure must open *before* the body is typeset.
Boxing the body first looks equivalent and is not: the body is typeset while
tagging is still live, a TikZ node opens its own `text` sequence, and that
sequence is announced despite a perfect-looking `/Alt`. The test suite pins this;
see `tests/test_latex_golden.py::test_described_has_no_nested_readable_element`.

---

## Documentation

| File | What it covers |
|---|---|
| `docs/METHODOLOGY.md` | How this was researched and built, what was verified by running vs read, every dead end and bug — written so another agent can replicate the method |
| `docs/ALT_TEXT_SPEC.md` | The authoring spec staff and agents follow, with genre templates |
| `docs/AGENT_HARNESS.md` | Driving the tool from an LLM agent |
| `docs/MIGRATION.md` | Converting an existing corpus, and what the shim cannot cover |

---

## Status

Working and verified end to end: toolchain gate, template layer, legacy shim,
figure scanning, deterministic describers, worklogs, apply, checker, agent API.

Not yet implemented: the math-to-speech pipeline, the Textual TUI, the veraPDF
gate, and the parallel build harness. See `docs/METHODOLOGY.md` § Next phase.
