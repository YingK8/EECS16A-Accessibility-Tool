# Methodology and decision record

Written so another agent — or a TA a year from now — can replicate the method,
understand why each decision went the way it did, and avoid repeating the dead
ends. Every claim below is marked **[verified]** (executed on the machine) or
**[read]** (taken from documentation or source without running it).

---

## 1. How the research was done

Three research agents ran in parallel, each with a narrow brief and an explicit
instruction to separate what they ran from what they read:

| Agent | Brief | Most valuable finding |
|---|---|---|
| Figure alt-text | WCAG/NCAM/DIAGRAM guidance, genre templates, deterministic extraction, critique of the prior tool | 31% of `\includegraphics` call sites are commented out, and the prior regex tool would uncomment and break them |
| Math-to-speech | SRE, MathCAT, MathReader, axessibility, LaTeXML, MathJax | `latex-lab-math` ingests external MathML keyed by MD5 — so pdfLaTeX needs no engine change |
| Toolchain & validation | LaTeX3 tagging state, the suppression mechanism, validators, Python libraries | TeX Live 2025 cannot declare PDF/UA at all; `pdfstandard` accepts only PDF/A values |

**What worked:** giving each agent a corpus to measure against rather than a
question to answer in the abstract. Every one came back with counts from this
repository, which is what made the plan concrete.

**What to repeat:** requiring the verified/read distinction. Two agents reported
capabilities that exist upstream but are absent from the installed TeX Live, and
the distinction is what caught it.

**What to watch:** an agent reported the linked StackExchange thread as
authoritative on `/ActualText`. Reading it showed it concludes only "use
`tagging=on` + `alt=`". The real evidence was a measured viewer-support
benchmark elsewhere. Agent summaries were treated as leads, then checked.

---

## 2. Findings that changed the design

### 2.1 The toolchain cannot claim conformance **[verified]**

```
$ grep -A2 '_pdfstandard .choices:nn' $(kpsewhich documentmetadata-support.ltx)
  {A-1B,A-2A,A-2B,A-2U,A-3A,A-3B,A-3U,A-4}
```

No `ua-1`, no `ua-2`. And no top-level `tagging` key — only the deprecated
`activate/tagging`. LaTeX format is `2024-11-01` patch level 2 **[verified]** by
reading `\edef\fmtversion` in `latex.ltx`.

TL2025 is frozen; `tlmgr update --all` cannot cross releases. **TeX Live 2026 is
a prerequisite for declaring conformance**, so `doctor` was built first and
reports the exact missing capability rather than letting a build pass silently.

### 2.2 Comment-blindness was the prior tool's fatal bug **[verified]**

Measured across the approved scope:

| Scope | `\includegraphics` commented out | `tabular` commented out |
|---|---|---|
| question bank | 29% | 14% |
| sp26 | 33% | — |
| exams | 25% (192 sites) | 31% (276 sites) |

A regex wrapper inserts `\begin{AltOnly}` on a commented line, where `%` eats it,
leaving a live `\includegraphics` and an unmatched `\end` — a guaranteed compile
failure. This is almost certainly why the prior work was reverted (`git log`
shows `reverted old code`, and the corpus contains zero `AccessibleFigure`).

**Decision:** a comment-aware, brace-balanced scanner (`texlex/scanner.py`)
rather than regexes or an off-the-shelf parser. tree-sitter and pylatexenc were
both considered; a purpose-built scanner won because it needs no build
dependency, tolerates the corpus's broken macros, and classifies every character
as code / comment / verbatim in one pass. 2,632 files scan in 3.1 s **[verified]**.

### 2.3 `/ActualText` is the wrong primitive **[read]**

The measured viewer benchmark (ho-tex/accsupp#2) shows Acrobat returning random
substrings of long values, Chrome truncating at 16383 characters, and Firefox,
MuPDF < 1.27, PDF-XChange and SumatraPDF ignoring it entirely. tagpdf's author
notes marked-content `ActualText` **cannot nest**.

**Decision:** suppression is built from `Figure` + `/Alt` + **suspended tagging**,
which is what tagpdf itself does for TikZ.

### 2.4 Content-addressed identity **[verified]**

The prior scheme, `sha1(relpath + "#" + offset)`, broke on any earlier edit and
on the `sp25 → sp26` rollover that renames every path. Hashing image bytes or the
normalised environment body instead gives ids that are re-derivable, need no
placeholder in the source, and deduplicate automatically: 2,564 live call sites
collapse to 1,769 unique figures **[verified]**.

This also removes the placeholder failure mode entirely — there is no token that
can leak into a PDF as real `/Alt`.

---

## 3. Bugs found while building, and how

These were all invisible in the LaTeX log. Recording them because each represents
a class of failure this domain produces.

### 3.1 Control sequences cannot contain digits

`\a11yheading` parsed as `\a` + `11yheading`, and internal `\__a11y_...` names as
`\__a`. Symptom: `Command \1 unavailable in encoding T1`. Fixed by renaming every
control sequence to a letters-only `access` prefix. File and message-module names
keep digits — those are strings, not macro names.

### 3.2 Paragraph tagging must be suspended, not just tagging

Content inside a suspended region still changes TeX's paragraph state, so the
next `\par` tried to close a structure that was never opened — `there is no open
structure on the stack` for the rest of the document. Fixed with
`\tagpdfparaOff` / `\tagpdfparaOn` around headings, `AltOnly` and `Decorative`.

### 3.3 Marked content cannot nest

Opening a `Figure` inside running text produced `nested marked content found`.
Fixed with tagpdf's own `\tag_mc_end_push:` / `\tag_mc_begin_pop:n` pair.

### 3.4 The suppression silently failed — the important one

**Symptom: none.** Clean log, zero tagpdf warnings, correct-looking tag tree,
correct `/Alt`. And the figure's text was still fully readable.

Found only by parsing the content stream and inspecting marked-content nesting:

```
enclosing path at the forbidden glyphs: [('Figure', 6), ('text', 7)]
```

A `text` sequence nested inside the Figure. **Cause:** the unstarred `AltOnly`
captured its body into a box *before* opening the Figure, so the body was
typeset while tagging was still live and a TikZ node opened its own sequence.
Fixed by opening the Figure and suspending *inside* the box, before any content.

**Lesson, and the reason `check/content.py` exists:** in this domain the
structure tree tells you what the tags *are*, not what they *cover*. Only the
content stream answers "would a screen reader speak this?". Pinned by
`test_altonly_has_no_nested_readable_element`.

### 3.5 tagpdf needs three runs

After one run every `/MCID` in the structure tree read `1` while the content
stream numbered them `0..31` — the tree resolves ids across runs via the `.aux`.
Diagnosed by comparing the two **[verified]**; hence `min_runs: 3` in the profile.

### 3.6 Bookmark strings are not typeset material

The outline showed `a11ySolutionSolution` (a `\color` argument arriving as
literal text) and `Question ` (a counter that never expanded). Fixed with
l3text's `\text_purify:n` plus explicit plain-text bookmark forms.

### 3.7 `rgb` and `RGB` differ by case

Lower-casing the xcolor model made the 0–255 branch dead code, so every pgfplots
colour clamped to white and reported 1.00:1. Also: only colours actually applied
to text are judged against a text threshold — pgfplots defines dozens per
document for plot lines, and judging those was pure noise.

---

## 4. Verified outcomes

| Claim | Evidence |
|---|---|
| Suppression works | forbidden glyphs sit in a `Figure` leaf with no nested readable element; `readable_text()` excludes them |
| Hierarchy works | bookmark outline `Homework 9 → Question 1 → Part (a) → Solution`, tag levels `[1,2,3,4,…]` |
| Legacy files compile | `golden_legacy.tex` uses `\def\title`, `qunlist`, `\qns`, `\qitem` in `enumerate`, `\sol` — 0 errors, 0 warnings |
| Checker matches research | `ee16.sty` → 5× `cmdunh10`, 3× `epsf`; `sp26.sty` → 3.07:1 and 3.12:1 |
| Contrast arithmetic | pure red 4.00:1, `0645AD` 8.53:1, `C00000` 6.48:1 |
| Describers | pgfplots reproduces the research report's worked example exactly |

51 tests pass, including a full compile-and-assert cycle on two golden fixtures.

---

## 5. Deliberate non-goals

* **No AI in the conversion path.** Every transformation is rule-based and
  reproducible. AI may *propose* descriptions through the agent API; a human
  approves, and only approved text is written.
* **No engine migration.** pdfLaTeX throughout. LuaLaTeX would automate MathML
  generation, but the same MathML can be supplied externally, so the migration
  risk across circuitikz, `epsf` and legacy fonts buys nothing.
* **No frozen snapshots.** 17k of 17.6k `.tex` files are archived per-semester
  copies. Converting them multiplies authoring cost by the number of semesters.

---

## 6. Next phase

Not yet built, in recommended order:

1. **Math-to-speech.** Design settled by research: extract a macro dictionary
   from `ee16.sty` and the semester styles → pass 1 with
   `\tagpdfsetup{math/mathml/write-dummy}` to emit source + LaTeX's own MD5 per
   formula → MathJax 4 `tex2mml` (it emits the `columnlines` attribute MathCAT
   needs for augmented matrices; pandoc was **[verified]** to drop it) → MathCAT
   SimpleSpeak → `/Alt`. 523,828 occurrences collapse to 35,504 unique strings,
   so a hash-keyed cache makes this minutes, not days. Reject `axessibility`: on
   pdfLaTeX it does not hook `$…$` at all, which is ~94% of this corpus's math.
2. **veraPDF gate.** Shell out, parse `--format json`; there is no maintained
   Python binding. Map failures back to source via tagpdf `label=` keys in the
   `.aux`, not by guessing from object numbers.
3. **Parallel build harness.** One `-outdir` per document is mandatory —
   `markup.sty` writes `\jobname.annotations` and will collide.
4. **Textual TUI** over the same APIs the CLI uses.
5. **`migrate` command** for the preamble and `.sty` rewrites in `MIGRATION.md`.
