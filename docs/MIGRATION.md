# Migrating an existing corpus

What the compatibility shim covers, what it cannot, and the order to do things.

---

## Prerequisite

Install **TeX Live 2026**. On TL2025 `\DocumentMetadata{pdfstandard=ua-1}` is
rejected and `tagging=on` does not exist, so documents can be tagged but
conformance cannot be declared. `latexally doctor` reports exactly which
capability is missing and refuses to run the pipeline when a build would
silently produce untagged output.

---

## Order

```bash
latexally doctor                    # gate
latexally scan sp26                 # build worklogs for one semester
#   … staff and agents fill in ally/descriptions/*.md, a human approves …
latexally apply sp26 --show-diff    # review
latexally apply sp26 --write
latexally check sp26
```

Do one live semester first. It exercises the whole loop on material that is
actually published, and the shared question bank benefits every later semester
because descriptions are keyed by content hash.

Recommended sequence: `sp26` → `questionBank/` → `exams/`. Exams last: 1,927
files, several with self-contained duplicated preambles, and the worst legacy
patterns (`\framebox(470,42){}`, `\uline{\hfill}`).

---

## What the shim covers

`latexally-compat-ee16.sty` re-expresses the legacy vocabulary in tagged terms,
so existing question files keep compiling:

| Legacy | Becomes |
|---|---|
| `\qns{title}` | question heading, H2 |
| `\q{pts}{title}` | question heading with points, H2 |
| `\qitem` | part heading, H3 (works inside a legacy `enumerate` or standalone) |
| `\sol{…}` `\ans{…}` `\solans{…}` | solution / answer blocks, H4 |
| `qunlist` | transparent container |
| `\fig` `\twofig` | `\includegraphics` wrapped for description |
| `\fillin` | labelled answer blank |
| `\def\title{…}` | recovered at `\begin{document}` |

Verified on `tests/fixtures/golden_legacy.tex`, which uses all of the above:
0 errors, 0 tagpdf warnings, and a full `H1 → H2 → H3 → H4` bookmark tree.

---

## What still needs an edit

These are mechanical and detectable; the `migrate` command that automates them
is next-phase work, so today they are hand edits.

1. **`\newcommand{\qitem}{\qpart\item}` in every `body.tex` — delete it.**
   `\newcommand` errors on an already-defined command, and `\qitem` is defined
   by the shim.

2. **`\renewcommand{\labelenumi}{(\alph{enumi})}` blocks — delete them.** Dead
   once parts are headings rather than list labels.

3. **Preamble.** Replace the driver preamble with the class:

   ```latex
   \DocumentMetadata{lang=en-US, pdfstandard=ua-1, pdfversion=1.7, tagging=on}
   \documentclass[sol]{latexally-assignment}
   \usepackage{latexally-compat-ee16}
   \coursenumber{EECS 16A}
   \semester{Spring 2026}
   ```

   `\DocumentMetadata` **must be the first line**, before `\documentclass`.
   Without it the whole accessibility layer is inert and the PDF is untagged
   while still compiling cleanly.

4. **Driver-file pairs collapse.** `prob9.tex` and `sol9.tex` differ only in
   whether `\sol` expands; that is now `[prob]` versus `[sol]` on one source.
   Same for `dis09A` / `ans09A` / `sol09A` → `[blank]` / `[ans]` / `[sol]`.

---

## Blockers in `ee16.sty`

`latexally check` reports these directly. Ranked by impact:

| Issue | Why it blocks | Rule |
|---|---|---|
| `\font\dunhb=cmdunh10` ×5 | bitmap font with no ToUnicode map — every heading and the whole title block is unextractable and unspeakable, *regardless of tagging* | `ALLY-SRC-024` |
| `\def\section` via `\@startsection` | bypasses the kernel hooks, so no H1/H2, no `Sect`, no bookmark, for 2,042 + 1,193 call sites | — |
| `\epsffile` (866 files) | predates `graphicx`; invisible to the tagging code, so no Figure element and no `/Alt` at all | `ALLY-SRC-022` |
| `\def\maketitle` with bare `\hrule` | untagged content | — |
| hand-built `qunlist` `list` | a raw `list` gets no `L`/`LI`/`Lbl`/`LBody` structure | — |
| `subfigure` | obsolete, not in the tagging status database | — |

And in `markup.sty`: `\renewcommand\document` / `\enddocument` run at the wrong
time relative to tagging initialisation. Convert to
`\AddToHook{begindocument/end}` and `\AddToHook{enddocument}`.

Colour replacements are already in `profiles/eecs16a.yaml`: `solutionColor`
measures 3.07:1 and `answerColor` 3.12:1 against a 4.5:1 requirement. Note that
the previous tooling "fixed" contrast by setting `redish` to pure red, which is
4.00:1 — still failing.

---

## Idempotency

`scan` and `apply` are safe to re-run. `scan` regenerates machine sections and
preserves every human field. `apply` skips anything already wrapped, and writes
only `approved` descriptions — a draft or empty description is never written,
which is what stops an unfilled placeholder reaching a PDF as real `/Alt` text.

Rollback is `git checkout`; the tool writes no `.bak` files.
