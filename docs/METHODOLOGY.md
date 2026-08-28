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
| Toolchain & validation | LaTeX3 tagging state, the suppression mechanism, validators, Python libraries | Whether `pdfstandard=ua-1` is accepted depends on the install, and `doctor` T006 is what answers it — see § 11 |

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

A regex wrapper inserts `\begin{Described}` on a commented line, where `%` eats it,
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

`\allyheading` parsed as `\a` + `11yheading`, and internal `\__ally_...` names as
`\__a`. Symptom: `Command \1 unavailable in encoding T1`. Fixed by renaming every
control sequence to a letters-only `access` prefix. File and message-module names
keep digits — those are strings, not macro names.

### 3.2 Paragraph tagging must be suspended, not just tagging

Content inside a suspended region still changes TeX's paragraph state, so the
next `\par` tried to close a structure that was never opened — `there is no open
structure on the stack` for the rest of the document. Fixed with
`\tagpdfparaOff` / `\tagpdfparaOn` around headings, `Described` and `Decorative`.

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

A `text` sequence nested inside the Figure. **Cause:** the unstarred `Described`
captured its body into a box *before* opening the Figure, so the body was
typeset while tagging was still live and a TikZ node opened its own sequence.
Fixed by opening the Figure and suspending *inside* the box, before any content.

**Lesson, and the reason `check/content.py` exists:** in this domain the
structure tree tells you what the tags *are*, not what they *cover*. Only the
content stream answers "would a screen reader speak this?". Pinned by
`test_described_has_no_nested_readable_element`.

### 3.5 tagpdf needs three runs

After one run every `/MCID` in the structure tree read `1` while the content
stream numbered them `0..31` — the tree resolves ids across runs via the `.aux`.
Diagnosed by comparing the two **[verified]**; hence `min_runs: 3` in the profile.

### 3.6 Bookmark strings are not typeset material

The outline showed `allySolutionSolution` (a `\color` argument arriving as
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
| Contrast arithmetic | pure red 4.00:1 → `EE0000` 4.53:1; `3399E6` 3.07:1 → `187AC4` 4.55:1 |
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

1. ~~**Math-to-speech.**~~ **Built** — see § 10.
2. ~~**veraPDF gate.**~~ **Built** — `check/vera.py`, run by `check --pdf`. It
   shells out and parses `--format json`; there is no maintained Python
   binding. Failures map back to source via tagpdf `label=` keys in the `.aux`,
   not by guessing from object numbers. Two things about the report shape cost
   an afternoon and are worth writing down: the top level is `report`, not
   `jobs`, and `validationResult` is a **list**, not a dict. Both mistakes
   return an empty finding list, which reads as a clean PDF.
3. ~~**Parallel build harness.**~~ **Built** — `--jobs N`, a
   `ThreadPoolExecutor` over the compile phase only. The note this item used to
   carry was wrong: *one `-outdir` per document is not mandatory.*
   `compile_document` has always used one shared `-output-directory` and
   distinguished documents by `-jobname`, which is what already prevents the
   `\jobname.annotations` collision. What does have to stay serial is the
   *conversion* phase, because an assignment's variants share one mirror
   directory — `materialise`, `apply_descriptions` and the worklog shard all
   read-modify-write it.
4. **Textual TUI** over the same APIs the CLI uses.
5. **`migrate` command** for the preamble and `.sty` rewrites in `MIGRATION.md`.

---

## 7. Visual fidelity of the ee16 retrofit

A retrofit is only usable if the printed page does not change. Measured on the
real `sp26/hw/9` (13 pages), comparing rasterised pages at 150 dpi and counting
pixels differing by more than 96/255:

| Comparison | Difference |
|---|---|
| untagged original vs **tagging alone** (no latexally) | 2.596% |
| untagged original vs **full retrofit** | 2.594% |
| **tagging alone vs full retrofit** | **0.002%** |

So `latexally-ee16` is visually free: it changes 0.002% of pixels. The 2.6% is
the cost of enabling LaTeX's tagging at all — pagination shifts slightly on some
pages — and is unavoidable on any route to a tagged PDF.

That result depends on one workaround, found by bisection:

**Interword-space injection breaks the page.** Activating tagging also enables
tagpdf's `activate/spaces`, which injects real space glyphs. On this corpus that
reflows text in the running header and footer and inside `\emph` groups: glyphs
pile on top of each other and the header is unreadable. Measured: 484 of 3585
adjacent word pairs overlap with injection on, 0 of 3537 with it off.

It is **not** caused by anything in this package —
`\DocumentMetadata{testphase={tagpdf}}` alone reproduces it, and
`\DocumentMetadata` with no testphase does not. `latexally-ee16` therefore sets
`\tagpdfsetup{activate/spaces=false}`. The trade-off is that extracted word
boundaries rely on pdfTeX's positioning rather than explicit space glyphs;
extraction still resolves words correctly here, and a legible page matters more.

### Method notes for whoever repeats this

* Compare **pixels, not extracted text**. Enabling ToUnicode changes how text
  extracts — `office` becomes `ofﬁce`, math slots become real Unicode — while
  the page is identical. Text diffing reports those as changes; they are not.
* Compare **word boxes for overlap**, not word strings. The corrupted header
  still extracted as `['Last','Updated:','2026-08-08','21:28']`; only the
  positions revealed that the glyphs were stacked.
* Build both PDFs **in the same minute**. ee16's header prints `\timestamp`, so
  otherwise every page differs.
* Rebuild the baseline. The `sol9.pdf` committed in the repo is a stale artifact
  and does not match current source.
* Use **three pdflatex runs**. tagpdf resolves structure-tree MCIDs via the
  `.aux`; after one run the tree says `/MCID 1` everywhere.

### Correction to an earlier finding

The research pass reported `\font\dunhb=cmdunh10` as an outright PDF/UA blocker
requiring font replacement. That is wrong as stated. `\pdfgentounicode=1` plus
`\input glyphtounicode` makes pdfTeX emit a ToUnicode CMap for the font, and the
text then extracts as real words — verified. The course keeps its typography and
the document becomes conformant, so no font substitution is needed.

---

## 8. Refactoring the classes onto the house style

The standalone classes were originally my own design and shared nothing with an
EECS 16A document but the bookmark tree. They now reproduce ee16.sty's
specification: the 6.5in x 9in text block set by raw dimension assignment (not
`geometry`, which recomputes it from margins), Times/mathptmx body text, the
cmdunh10 masthead between two 6pt rules, `qunlist` supplying `1.` as a list
label, `(a)`/`i.`/`A.` part labels, the running header and copyright footer, and
an inline blue `Solution:`. Authors keep writing `\qns`, `\q`, `\qitem`, `\sol`
and `qunlist`.

### Why parts and solutions carry bookmarks but not heading tags

**PDF forbids a heading inside a paragraph.** In an EECS 16A document `(a)` is a
list label and `Solution:` opens the same paragraph as the solution text, so
both are inline. Tagging them produced `Parent-Child 'P' --> 'H4'`; giving them
their own paragraph would insert a `\parskip` and move the page, which is the
one thing this refactor exists to prevent.

So the tag tree carries H1 (masthead) and H2 (each question), and the bookmark
tree carries all four levels. Navigation is unaffected — the outline still reads
Homework > Question > Part > Solution — and the document stays valid.

The masthead as a whole is the H1, not just the title text: ee16 puts the title
on the same line as the semester, and the `\vskip` between masthead lines forces
an implicit `\par`, so a paragraph is already open by the time the title is set.
The bookmark is given the title alone, so the outline still reads "Homework 9".

### The paragraph-hook rule, stated properly

Three separate bugs in this work came from the same mistake, so it is worth
stating as a rule: **`\tagpdfparaOff`/`\tagpdfparaOn` must bracket whole
paragraph units.**

Switch it off part-way through a paragraph and the BEGIN hook has already fired
while the END hook still fires — or, at the start of a list item, the BEGIN hook
is skipped while the END hook is not. Either way tagpdf aborts with "the number
of automatic begin and end text para hooks differ". The fix in `\qns`/`\q` is to
switch off *before* `\item` and close with `\par` *before* switching back on, so
neither hook fires and they balance.

That `\par` is **not** free, and an earlier version of this document claimed it
was — "every question in the corpus is already followed by a blank line". It was
written from reading a handful of files and never measured. Measured: **74 of
362 `\qns` calls (20%)** are followed immediately by text, so the forced break
reflows one question in five. Question headings are therefore bookmark-only by
default, with `\accessquestiontags` to opt into real H2 tags. Both behaviours
are covered by `tests/test_latex_golden.py`.

### Interword spaces, again

`latexally-doc.sty` needed the same `\tagpdfsetup{activate/spaces=false}` as the
ee16 retrofit. Without it the running header, the footer and the `\Large` due-
date line rendered as stacked, overlapping glyphs.

---

## 9. Making conversion a program, and the naming pass

### 9.1 Why the shell script had to go

Conversion lived in `examples/build-corpus.sh`. That script was not a
convenience wrapper: it was the *definition* of what conversion means — which
lines get injected, in what order, with which options — expressed as a `sed`
expression that nothing tested and nothing else could call. Moving it into
`src/latexally/build/` exposed four defects that had been invisible:

| # | Defect | Why it was invisible |
|---|---|---|
| 1 | A directory is not a document | The scan looked in the assignment folder. **76.5% of sp26's graphics (277 of 362) are reached by `\input` from the shared bank.** `sp26/dis/01A` owns two `.tex` files with zero graphics and pulls in 36. A directory-scoped scan reported a clean sweep having examined a quarter of the material. |
| 2 | `TEXINPUTS` order was backwards | kpathsea searches `TEXINPUTS` entries *before* the default (where `.` lives). Listing the corpus ahead of the mirror made `\input{body}` find the ORIGINAL file, so every mirrored edit was silently discarded and the conversion appeared to work. |
| 3 | Relative paths resolve against the build directory | `sp26/dis/preambleFa23.tex` says `\usepackage{../../fa23}`, which means `sp26/fa23.sty` — two levels up from the *assignment*, not from the preamble. Resolving against the including file pointed at a nonexistent path. This is TeX's rule, not an approximation of it. |
| 4 | Error detection missed absolute paths | `-file-line-error` writes `<path>:12:`, and the scan matched only `./`. A mirrored build is handed an absolute driver, so a build that died with "Emergency stop" parsed as **zero errors**. |

Defect 4 is the instructive one. Fixing it immediately showed that `sp26/hw/5`
and `sp26/hw/13` *gain* 8 and 16 errors under conversion — numbers the old
detector had been reporting as 0. A control build with latexally removed
entirely produced the **identical** 8 and 16, so they come from LaTeX's own
tagging, not from this package. Two constructs are responsible, and both are now
detected in source in milliseconds rather than after a three-minute compile —
and, since `latexally/rewrite.py`, fixed:

* **`ALLY-SRC-040`** — enumitem options (`\begin{enumerate}[label=(\roman*)]`)
  on a list latex-lab is also tagging. One "Missing number, treated as zero" per
  `\item`, and no PDF. **107 occurrences in 58 files** of the default scope;
  667 across every `.tex` in the tree, snapshots included.
* **`ALLY-SRC-041`** — `array` or `tabular` nested inside a matrix environment.
  "Misplaced `\crcr`" and five more. **14 occurrences in 3 files** of the
  default scope; 357 across the whole tree.

Quote a count with the scope it was taken over. The two differ by a factor of
six because the profile's default scope is the live bank plus sp26 plus the
exam archive — 1,959 files — while the tree also holds every frozen
per-semester snapshot, 17,677 files in all.

Both fixes had to be argued with the corpus rather than reasoned out, and both
times the corpus won:

* **`ALLY-SRC-041` must not delete the array**, which is what the rule's own
  hint said to do. 257 of the 357 sites carry a `|` in the column spec: they
  are augmented matrices, and the array is where the divider lives. The nesting
  is inverted instead — `\left[ … \right]` around the array. This also fixes
  the MathML, since `latex2mathml` reads the nested form as three rows, not two.
* **`ALLY-SRC-040` must not use `\labelenumi`**, which is what its hint said.
  483 of 667 sites are two `enumerate`s deep or more inside their own file, and
  every question file is `\input` inside the driver's own
  `\begin{enumerate}[series=qn]`, so the depth is not knowable from the source
  at all. `latexally-core` provides `\AllyEnumLabel`, which asks LaTeX at the
  moment the list opens.

A third, `ALLY-SRC-043`, is mostly not mechanisable and is worth recording as
the case where reading the sites changed the answer. Its hint said "close it
with `\)`". 28 of the corpus's 30 sites are not mismatched formulas at all —
they are a literal `(` written as `\(`:

    \(1) put 4 resistors in series, and let it be $R$
    \(http://inst.eecs.berkeley.edu/~ee16a/sp19/hw-practice).

Following the hint on those pulls whole paragraphs into math mode. They are
reported and never rewritten.

### 9.2 The naming pass

The rule applied was: **a name must not imply a contrast the codebase does not
draw.** `AltOnly` presupposed an `AltAlso` that describes a region *and* still
reads its contents. No such mode exists or could exist — alt text always
replaces the region; that is what alt text is. The qualifier named the category
rather than this member of it, so it carried no information and invented a
sibling. `FigureBlock` likewise implied a `FigureInline`, and named layout
rather than meaning.

| Before | After |
|---|---|
| `AltOnly`, `\altonly` | `Described`, `\described` |
| `FigureBlock[alt=]` | `DescribedFigure[description=]` |
| `\FigureDescription` | `\LongDescription[label]` |
| `\accesssafepalette`, key `safepalette` | `\accessconformingcolors`, key `conforming-colors` |
| `Entry.alt` / `.long` | `.description` / `.long_description` |
| `ALT-*` rejections | `DESCRIPTION-*` |

Names that **kept** their qualifier, because it is earned: `\accessbookmarkonly`
genuinely contrasts with `\accessheading` (bookmark alone vs bookmark plus
structure heading), and `DataTable`/`LayoutTable` are both real. `Decorative`
and `Described` now read as the two halves of the PDF/UA binary they are — a
region is either an artifact or it is described — which the old pairing hid.

The rename was done **now because it was free**, and that was measured before
proposing it: `grep -rl AltOnly` across the corpus returned zero `.tex` files
and `questionBank/ally/` did not exist, so there were no worklogs to migrate.
Every occurrence was inside this repository. The same rename after the first
real conversion sweep would mean rewriting course sources. Deprecated aliases
live in `latexally-compat-ee16.sty` — not in the core package, so the core API
has exactly one spelling of each idea — and `tests/fixtures/golden_deprecated.tex`
builds against the old vocabulary to prove nobody mid-migration is stranded.

### 9.3 Bugs the new tests found

| Bug | Symptom |
|---|---|
| `.gitignore`'s bare `build/` | Matches at *any* depth, so `src/latexally/build/` — the whole conversion engine — was silently excluded from `git add -A` and never committed. The commit looked clean. Anchored to `/build/`. |
| `\answerbox` took an optional argument | `\answerbox{1in}` drew a default box and then typeset the literal text "1in" on the page. No error. Braces are what the legacy vocabulary uses (`\fillin{4cm}`), so it is the form the command will actually be given. Both spellings now work. |
| `srgb_to_luminance` accepted 0..255 | Returned a plausible wrong answer: (6,69,173) on white reported 16.79:1 instead of 8.53:1. Both "pass" 4.5:1, so the mistake could only surface as a conformance claim computed from nonsense. Now rejected. |
| The TUI hid each toggle's cost while it was off | Exactly when someone is deciding whether to pay it. |

### 9.4 Two claims measurement contradicted

Recorded because both were about to be written down as fact:

1. **Plain blue is not the failing colour.** `#0000FF` on white is 8.59:1 and
   conforms comfortably. The 2.6:1 failure is `solutionColor` =
   rgb(0.2, 0.6, 0.9) = `#3399E6`, defined by the discussion preambles. Only
   colours that actually fail are changed, and each is darkened just far enough
   to clear the floor: `#3399E6` becomes `#187AC4` at 4.55:1, not the `#0645AD`
   at 8.53:1 a fixed palette used to impose, which overshot so far that it read
   as harder than the colour it was fixing.
2. **The first style-fidelity test measured its own fixture.** It compared the
   class against a hand-written approximation of ee16's `\@maketitle` and
   reported a 50pt drift that came entirely from the fake masthead. The body is
   now compared against the house spec written out exactly, and the masthead
   against the real `ee16.sty`, skipped when the corpus is absent.

### 9.5 What a run produces

Everything under one directory the user chooses, so nothing is scattered and the
corpus stays read-only by default:

```
ally-out/
  pdf/           converted PDFs
  logs/          build logs
  tex/           converted sources, with each driver's transitive relative
                 dependencies copied at the same offsets so the tree builds
                 without the corpus beside it
  descriptions/  the YAML description files staff fill in
  baseline/      untouched originals, for the before/after pixel comparison
  run.yaml       the run's settings, replayable with `build --config`
```

`in-place` mode exists and refuses unless the corpus git worktree is clean, so
the tool's edits are always reviewable on their own and revertible with
`git checkout`.

### 9.6 Question H2 tags: a default reversed by measurement

They shipped **off**, on the reasoning in §8: a heading may not sit inside a
paragraph, so a real H2 question title forces a `\par`, and 74 of 362 `\qns`
calls are followed immediately by text rather than a blank line. A visual cost
was inferred from that count. It was never rendered.

Measured, off versus on, six assignments including all three in sp26 that
exhibit the pattern:

| Assignment | pages | H2 | pixel difference |
|---|---|---|---|
| sp26/hw/9 | 13 / 13 | 6 | 0.00% |
| sp26/hw/1 | 11 / 11 | 7 | 0.00% |
| sp26/hw/2 | 16 / 16 | 8 | 0.00% |
| sp26/dis/09A | 5 / 5 | 2 | 0.00% |
| sp26/hw/13 | 21 / 21 | 7 | 0.42% |
| sp26/dis/11A | 6 / 6 | 2 | 0.79% |

Page counts identical throughout, and a worst case of 0.79% against tagging's
own 2.6%. The `\par` collapses into the list item's existing `\parskip`
instead of adding to it, so the reflow the count predicted does not occur.

What it buys is not cosmetic, and is the reason the reversal matters rather than
being a wash. A screen reader's heading key (`H` in NVDA and JAWS, the rotor in
VoiceOver) walks the **structure tree**. The bookmark outline is a separate
object graph and does not answer it. With the toggle off a reader got an H1 and
then nothing until the body text, on a document whose visual design is entirely
"here is question 3" — which is also a WCAG 1.3.1 problem, since structure
conveyed visually was not conveyed programmatically.

**The general lesson, and the second time this exact mistake appears in this
document:** a count of source patterns is not a measurement of rendered output.
§8 recorded the same error in the opposite direction — claiming the `\par` was
free because "every question is followed by a blank line", which was also never
checked. Both were settled in minutes by building the documents and comparing
pixels. Infer nothing about a page that can be rendered.

### 9.7 A relative output directory went to the wrong place

Reported from a real run: `latexally build ... -o ally-out --write` crashed with
`AttributeError: 'NoneType' object has no attribute 'is_file'`.

The AttributeError was the symptom. The engine runs pdflatex with `cwd` set to
the directory being built and `-output-directory` taken from the run config; a
**relative** output path is then resolved against *that* directory, not against
the one the user typed it in. The PDF and log were written inside the mirrored
source tree, the log lookup that followed found nothing, and the reporting code
fell over on `None` -- replacing the build result with a traceback.

Every artifact path is now absolute by the time it leaves the model, while
`as_dict` still serialises the raw value so `run.yaml` stays portable. `run.yaml`
records `ally-out`; the engine receives `/Users/.../ally-out/pdf`.

**Why the tests missed it.** Every fixture passed an absolute `tmp_path`. The
defect lived exactly in the gap between what was tested and what a person types
-- `-o ally-out` is the obvious thing to type, and no test ever did. The
regression tests now `monkeypatch.chdir` and assert on relative input.

Two further defects fell out of the fix:

* Absolute paths appear in every path prompt, and `[/Users/...]` is Rich markup.
  An unescaped one raised `MarkupError` before the question could be asked. Every
  interpolated value in a prompt or table cell is escaped now.
* `_log_findings(None)` raised. A build that never wrote a log is a real case,
  and crashing there hides the build failure the user actually needs to see.

### 9.8 A third construct LaTeX's own tagging cannot handle

The same run surfaced `ALLY-SRC-042`: a line break immediately after display
math -- `\end{align*}` followed by `\newline` -- fails under tagging with
"There's no line here to end" and produces no PDF. Display math ends in vertical
mode, so there is no line for `\newline` to end; the untagged build tolerates
it, which is why the construct is in the corpus at all.

Attributed the same way as the others, and worth doing every time: two control
builds, one with tagging alone and latexally entirely absent, one with the full
retrofit and question H2 tags off. Both produced the **identical** two errors, so
neither this package nor the newly-flipped default is responsible. Measured
across the live corpus: 12 occurrences in 8 files.

### 9.9 An assignment is not one document

The runner built one file per assignment, chosen by preferring `sol<N>.tex`.
That is half the job at best. An EECS 16A assignment is several documents built
from **one body**, differing only in how `\sol` is defined:

| File | What it is |
|---|---|
| `sol9.tex` | `\newcommand{\sol}[1]{{\color{blue}\textbf{Solution: } #1}}` |
| `prob9.tex` | `\newcommand{\sol}[1]{}` -- the blank version students receive |
| `dis09A.tex` | the discussion handout, same idea |
| `ans09A.tex` | answers only |

Converting the solutions alone leaves the document students are actually given
untagged -- the exact opposite of the priority. Every variant is now built by
default, each with its own job name, baseline and fidelity number:

```
sp26/hw/9     solution   13pp  48 bookmarks  2.34%
sp26/hw/9     problem     8pp  48 bookmarks  2.38%
sp26/dis/09A  solution    5pp  21 bookmarks  1.44%
sp26/dis/09A  problem     3pp  21 bookmarks  1.93%
sp26/dis/09A  answer      4pp  21 bookmarks  1.70%
```

The page counts are the evidence that these are genuinely different documents
and that building one was never sufficient.

Which prefix means which document is declared in the profile (`corpus.variants`),
so a course spelling them `key`/`blank` changes one line of YAML rather than any
Python. A directory whose driver matches no prefix is still built, under the name
`document`, rather than being skipped for failing to follow a convention.

### 9.10 Bookmarks that listed the document without moving to it

Reported: clicking an entry in the table of contents did not jump to the
section. It jumped to page 1 -- from every entry, on every document.

`\bookmark[level=N, dest=NAME]` from the bookmark package **references** a
destination. It does not create one. Nothing else in the package created them,
so the missing anchors were invented at the top of the document.

What makes this worth recording is how thoroughly it passed inspection:

* 48 bookmarks, the expected count;
* correct titles, correctly purified;
* correct nesting, `H1 → H2 → H3 → H4`;
* a populated `/Names /Dests` tree containing every name referenced;
* every `/A` a well-formed `/GoTo` action pointing at a name that existed.

Every one of those was already asserted by the test suite. The defect is one
level below all of them: **resolve the destination to a page number.** All 48
resolved to page 1, as `/Fit` -- page-level, no coordinates -- while hyperref's
own destinations in the same file were `/XYZ` on the right pages. That contrast
inside a single PDF is what identified it.

The fix is to use hyperref's `\pdfbookmark`, which writes the outline entry
*and* drops the anchor at the current point in one call. Its `\hyper@anchorstart`
brackets the anchor with `\Hy@SaveLastskip` / `\Hy@RestoreLastskip` precisely so
the whatsit cannot alter spacing: measured before and after, the pixel difference
against the untouched original is **2.34% either way**, unchanged.

Destinations after the fix, on `sp26/hw/9`:

```
pages : {1: 5, 2: 4, 3: 1, 4: 6, 5: 3, 6: 7, 7: 5, 8: 2, 9: 6, 10: 4, 11: 3, 13: 2}
types : {'/XYZ': 48}
```

Three checks now exist so no route can reintroduce it, structurally rather than
by inspecting the LaTeX:

| Rule | What it catches |
|---|---|
| `ALLY-PDF-023` | a bookmark whose destination resolves nowhere |
| `ALLY-PDF-024` | an outline where nothing is positional, so nothing scrolls to its heading |
| `ALLY-PDF-025` | every destination on one page of a multi-page document |

`ALLY-PDF-025` reports the old artefact exactly: *"all 48 bookmarks point at page
1 of 13; the outline cannot navigate."* A single-page document with every
bookmark on page 1 is correct, and is not flagged.

**The lesson generalises past bookmarks.** Counting artefacts and checking their
shape is not the same as checking that they *work*. The same mistake in a
different costume appears in §3.4: a `Described` region with a perfect `/Alt`, a
clean log and a correct tag tree, still reading its contents aloud. Both were
found only by asking what a user would experience -- what does the reader
announce, where does this link go -- rather than what the file contains.

### 9.11 The enumitem blocker, bisected

`sp26/hw/3` fails on `questionBank/hw/4/q_orthonormal_basis_basics.tex:8`. The
rule already flagged it, but the rule's explanation was wrong in two ways, and
both mattered.

**It is not a build failure only.** Rendering the same list three ways:

| Build | Renders |
|---|---|
| untagged | `(i) first (ii) second` |
| tagged | `() first () second` |
| repaired + tagged | `(i) first (ii) second` |

The labels come out **empty**. A document that ignored the errors would ship
with its list numbering silently gone -- worse than the failure, because it
looks like a build that worked.

**It is not "enumitem options" and not phase-III.** Bisected:

| Configuration | Result |
|---|---|
| `phase-I` / `phase-II` / `tagpdf` alone | `(i) (ii)`, 0 errors |
| `phase-II` + `table` / `graphic` / `firstaid` | `(i) (ii)`, 0 errors |
| **`phase-II` + `math`** | `()`, 2 errors |
| **`phase-III`** (any combination) | `()`, 2 errors |
| `itemsep=0pt`, `noitemsep`, `itemize label=--` | 0 errors |
| `label=(\theenumi)` (unstarred) | 0 errors |
| `\setlist[enumerate,1]{label=(\roman*)}` | 0 errors |
| `\renewcommand{\labelenumi}{(\roman{enumi})}` | 0 errors |

So the trigger is narrow: **a starred counter (`\roman*`, `\alph*`, `\arabic*`)
in a per-instance optional argument**, once the `math` module or `phase-III` is
loaded. `leftmargin=*` fails the same way. The same starred key in a preamble
`\setlist` is fine, which is what shows the problem is the inline path rather
than the key.

Narrowing the rule to the starred forms took it from 238 occurrences in 86 files
to **103 in 57** -- the other 135 were `itemsep`, `noitemsep` and friends, which
compile perfectly and were being reported as blockers.

Both fixes are verified to render identically to the untagged original, so the
diagnostic now names them instead of saying "exclude this file". Dropping the
`math` module is not among the options: this is a linear-algebra course.

### 9.12 The second variant clobbered the first

Found while investigating the above, and invisible from the PDFs. Materialising
an assignment copies its sibling `.tex` files into the mirror; the copy skipped
only the driver being converted. Building `problem` therefore laid the ORIGINAL
`sol9.tex` over the converted one written moments before.

The PDFs were all correct -- each was compiled before being overwritten -- so
nothing in the build report showed it. Only the mirrored tree was wrong, and the
mirror is what makes the output archivable: rebuilding from it produced an
untagged solutions document. Every driver a run converts is now skipped by every
other pass's copy step.

### 9.13 Fixing one blocked assignment, and what it corrected

`sp26/hw/3` failed on three errors in one shared question file. Fixing it
minimally corrected three of this document's own claims.

**The enumitem fix is narrower than §9.11 said.** `\setlist` does NOT work. It
compiles with 0 errors, which is all §9.11 checked, and then silently ignores the
label and falls back to `1.`:

| Approach | Errors | Renders |
|---|---|---|
| inline `[label=(\roman*)]` | 2 | `()` |
| inline `[label=(\roman{enumi})]` | 0 | `()` |
| `\setlist[enumerate,1]{label=(\roman*)}` | 0 | `1.` |
| `\renewcommand{\labelenumi}{(\roman{enumi})}` | 0 | `(i) (ii)` |

Only the plain LaTeX2e redefinition works. Counting errors was not enough here
either -- the same mistake as §9.10, one level up.

**`ALLY-SRC-042` missed the construct that actually broke the build.** The rule
matched `\end{align}` and friends but not `$$ ... $$\\`, which is what
`q_orthonormal_basis_basics.tex:85` contained. The file was reported clean and
the build failed anyway. All three spellings of display math are matched now,
and the rule went from 12 occurrences in 8 files to **35 in 27**.

**Deleting the line break is the wrong fix, and the hint said to.** Measured
against the untouched original, untagged:

| Replacement for `$$...$$\\` | Difference from the original |
|---|---|
| `$$...$$\mbox{}\\` | **0.0019%** |
| `$$...$$\par\vspace{\baselineskip}` | 0.2380% |
| `$$...$$\medskip` | 0.2460% |
| `$$...$$` (break deleted) | 0.4157%, repaginates pages 6-7 |

The `\\` was producing a real blank line, so deleting it moves the page. Giving
it a line to end with `\mbox{}` keeps the layout and compiles. The hint now says
so, with the numbers.

Final state of `sp26/hw/3`: both variants build with **0 errors and 0 warnings**,
10 and 5 pages, 28 bookmarks each, 2.58% and 1.56% against their originals -- and
the list renders `(i) Give the dimension... (ii) Provide an orthonormal...` as it
always did. The source diff is six lines.


---

## 10. Spoken math, and four bugs that were only visible against a real build

The design in § 6 survived contact, with two substitutions.

**MathJax was replaced by `latex2mathml`.** MathJax 4 dropped the MathML output
jax, so `tex2mml` no longer exists as a public call; getting MathML out means
driving `SerializedMmlVisitor` through internal imports. `latex2mathml` is one
pip package and one function call, and it was **[verified]** to keep
`columnlines="none solid"` on `\begin{array}{cc|c}` — the augmented-matrix
attribute that was the whole reason MathJax was preferred over pandoc.

**MathCAT was replaced by the Speech Rule Engine, and then replaced it.** The
first decision was right on the evidence available: MathCAT has no published
PyPI wheel — `libmathcat_py` is unpublished and needs a maturin build — and no
npm package either, while SRE is one `npm install`.

What that reasoning missed is that the objection is to the *bindings*, not to
the engine. The crate is healthy (MIT, `mathcat` on crates.io, released the day
before this was written), it ships all 160 of its `Rules/*.yaml` inside the
`.crate`, and a driver speaking the same JSON-Lines protocol `speech.cjs` used
is about ninety lines of Rust. Building one costs a `rustup` in setup and
removes Node from the toolchain entirely, since SRE was its only consumer.

Two things made it worth doing:

* **It is what the readers actually run.** NVDA and JAWS both speak maths with
  MathCAT. SRE is what MathJax and ChromeVox use.
* **Its rules are extensible, and this corpus needs one.** See below.

Either way it is driven as a *library*, not through a CLI: one expression per
invocation, at 35,504 unique formulas, is 35,504 process spawns.

**MathCAT is vendored as a fork, and the fork earns its keep immediately.**
`vendor/MathCAT` is a submodule of `YingK8/MathCAT` with `upstream` pointing at
`daisy/MathCAT`, pinned to the tree crates.io published as 0.7.5. Having
`Rules/` on disk makes `set_rules_dir` one call rather than an unzip out of a
build directory — but the real reason is that **upstream MathCAT reads no
`mtable` line attribute at all.** `grep -r columnlines vendor/MathCAT` returns
nothing. `[A|b]` and `[A b]` are spoken identically, which on a linear-algebra
course is the difference between a system of equations and a 2 by 3 matrix.

The fork carries one rule, `augmented-matrix` in
`Rules/Languages/en/SharedRules/general.yaml`, matching
`contains(@columnlines, 'solid')` and placed ahead of the zero/identity/diagonal
special cases because an augmented matrix can also be square or all-zero.
**[verified]** on the real `write-dummy` fixture: "the 2 by 3 augmented matrix;
row 1; column 1; 1, …". `tests/test_mathspeech.py` asserts that prefix, so a
rebase that drops the rule fails the suite rather than silently losing the
divider.

### The four bugs

Each was invisible in isolation and each produced a plausible-looking result.

1. **The hash is over the wrong string.** latex-lab hashes
   `\l__math_content_AF_source_tl` — the *rendered source template* — and it does
   so in the `tagsupport/math/struct/begin` socket, one socket *later* than the
   content plug that sets it. Hashing `\g__math_grabbed_math_tl` instead is the
   obvious reading, and gives a different digest for every formula: every lookup
   misses and every `/Alt` comes out empty.

2. **`math/alt/use` is off unless PDF/UA-1 is declared.** latex-lab clears
   `\l__math_content_alt_tl` after the content socket when the flag is false. On
   an install that cannot declare `ua-1`, that silently discards correct
   speech. `latexally-math.sty` now sets the key explicitly.

3. **expl3 discards literal spaces.** The generated table is read under
   `\ExplSyntaxOn`, so `{ the fraction with … }` becomes
   `thefractionwith…`. The `/Alt` is present, well-formed, passes every
   structural check, and is unintelligible aloud. Speech is emitted with `~`.

4. **The output directory is not on TeX's input path.** `-output-directory`
   governs writing only; `\input` of a generated file sitting next to the `.aux`
   fails with "File not found", which nonstop mode swallows. **[verified]** with
   a two-line probe. The fix is one `TEXINPUTS` entry — and it matters because
   the alternative, writing the generated table into the corpus, breaks the rule
   that the corpus is never touched.

A fifth was caught by the fixture rather than the build: latex-lab writes its
MD5 in **uppercase** hex and inserts a space after every control sequence
(`\frac {x^2-1}{x+1}`). A lowercase-only pattern matches nothing at all, and a
hand-written fixture would have hidden it — `tests/fixtures/mathml_dummy.html`
is real `write-dummy` output for that reason.

### Correction to § 6

§ 6 proposed extracting a macro dictionary from `ee16.sty` and scanning the
corpus for formulas. Neither is needed. `write-dummy` reports every formula
latex-lab *actually tagged*, with its source and hash, which covers inline
`$…$` for free and cannot disagree with what reaches the PDF. The `$`-pairing
scanner that `texlex` would have needed was never written.

### What the engine swap actually measured

Validated against **861 unique formulas of real `write-dummy` output** from 12
converted documents — latex-lab's own record of every formula it tagged, so it
cannot disagree with what reaches the PDF.

| | at the swap | after |
|---|---|---|
| spoken | 832 (96.6%) | **852 (99.0%)** |
| refused by MathCAT | 29 | 9 |
| `/Alt` containing a macro name | **284** | 1 |

Throughput is ~690 formulas/second in one process, which is what makes 35,504
unique formulas a thirty-second job rather than an afternoon.

**The 284 were not MathCAT's fault, and would have been identical under SRE.**
Every leaked backslash was already in the MathML: `in_mathml == in_speech ==
284`. MathCAT adds none. What leaked were course macros `latex2mathml` has
never heard of and passes through as literal text — `\mat` alone accounted for
591 occurrences — so the reader heard "mat cap u is equal to". `expand_macros`
is the fix, and it is the same shape as the `alignat` rewrite that was already
there: a measured list, not a TeX expander.

Two converter bugs were worth the dig:

* **A superscripted matrix produced no `/Alt` at all.** `latex2mathml` emits an
  `<msup>` with the wrong number of children for
  `\begin{bmatrix}…\end{bmatrix}^{\top}`; MathCAT refuses it outright with
  "msup should have 2 children". Every transpose in a linear-algebra course.
  Wrapping the environment in a brace group is the whole fix.
* **`aligned` yields invalid MathML** where `align*` reads correctly, nested
  inside `equation*` or not. **[verified]** both ways.

That MathCAT *refuses* invalid MathML rather than guessing is worth keeping in
mind when reading these numbers: every refusal was a real defect upstream of it,
and the missing `/Alt` is reported by `ALLY-PDF-040` rather than shipped as
plausible nonsense.

The 9 that still do not speak are not a speech problem. Seven are latex-lab
write-dummy artifacts — `\begin{eqnarray}\if@eqnstar \else \ifx …
\hyper@makecurrent` — expanded `hyperref` internals captured as if they were a
formula, which they are not. The other two are a block matrix inside `\pqty`
inside an `align*` row: two levels of `\\` that the converter cannot
disambiguate. **Of the 854 entries that are mathematics and convert to valid
MathML, MathCAT speaks every one.**

### The probe that read the wrong file

`doctor` T006 reported "`\DocumentMetadata{pdfstandard=ua-1}` is NOT supported"
on an install that accepts it perfectly well. The detail line gave it away —
"this install accepts only: " with nothing after the colon, meaning the parser
had found *no* standards rather than a list without `ua-1` in it.

`document_metadata_capabilities` parses the installed sources rather than
trusting release notes, which is the right instinct and is why § 1 exists. It
still went wrong, because it parsed **one** file: the `pdfstandard` key has
moved out of `documentmetadata-support.ltx` and into `pdfmanagement.ltx`.
Reading both finds `ua-1`, `ua-2` and eighteen PDF/A and PDF/X values.

The consequence was not cosmetic. `preamble_for` asks T006 whether the standard
can be declared and omits `pdfstandard=ua-1` when it cannot, so **every PDF this
tool built was missing its PDF/UA identification schema.** Measured with the
veraPDF gate on the same document either way:

| `\DocumentMetadata` | veraPDF findings |
|---|---|
| `tagging=on` alone | 2 — clause 5-1 (no PDF/UA identification schema), 7.1-10 (no DisplayDocTitle) |
| `tagging=on,pdfstandard=ua-1` | **0** |

Two things are worth taking from this. The first is that a probe of the
*installed files* is only as good as its list of files, and a file that moves
looks exactly like a capability that vanished. The second is that this is the
mirror image of the failure mode § 1 is built around: a false **warning** rather
than a false pass. It is the milder direction, but it still told course staff
they could not claim conformance when they could, and it silently degraded
every artefact for as long as it went unnoticed. `tests/test_toolchain.py` now
pins both file locations.

### The ceiling

`/Alt` is a flat string: no math navigation, no reflow. Associated MathML is
attached for readers that can use it.

MathCAT can also emit Nemeth and UEB braille from the same MathML, and this
does not use it. PDF/UA has no braille channel — the only place to put it would
be another associated file, and no reader is known to consume one. Generating
braille nobody reads is not a feature, so it is left off. If a student needs a
Nemeth transcript, the engine is already here and it is one call.

The augmented-matrix divider *is* now announced; see above. That was the one
concrete gap this section used to record.

## 11. Closing the loop on the alternative formats

Sections 9.10 and 3.4 both ended on the same lesson: counting artefacts and
checking their shape is not the same as checking that they work. This section is
that lesson applied to the last unmeasured hop — the one between a correct PDF
and what a student actually receives from Canvas Ally.

### 11.1 One file, five readers, three answers

`fa26/dis/00B` built with `math_speech` on produces a PDF that is correct by
every measure the tool had: 153 structure elements, 38 `Formula` elements, each
carrying `/Alt` (MathCAT ClearSpeak) *and* `/AF` (MathML plus TeX source), with
`/ActualText` on the marked content. The content stream is spec-conformant —
one `BDC` spanning the whole formula, including the `q…Q` that draws the square
root rule.

Five extractors were then run against that one file, on `$a = 1 - i\sqrt{3}$`:

| Extractor | Stands for | What it returns |
|---|---|---|
| structure tree | JAWS, NVDA, VoiceOver | `a is equal to, 1 minus, i times the square root of 3` |
| **PDFBox** | **Canvas Ally** | `a is equal to, 1 minus, i times the square root of 3` |
| PyMuPDF | Preview, pdf.js | `a is \ne\nqual to, 1 minus, … of \n3` — span fragmented |
| poppler `pdftotext` | most Linux pipelines | `1−i 3` — `/ActualText` ignored |
| Ghostscript `txtwrite` | the floor | `Let a = 1−i √3` — `/ActualText` ignored |

The PDF is right in all five cases. Three of the five readers are wrong, and
nothing noticed, because nothing in the tool had ever looked at extracted text.
That is the whole of "text to speech is not working and it is hard to validate":
not a defect in the artefact, an unmeasured dependency on the reader.

Canvas Ally is a Java service built on PDFBox, so row two is the row a student
hears. `latexally formats` runs all five, weights PDFBox as
`ALLY_EXTRACTOR`, and reports a disagreement as `ALLY-FMT-002` — a warning, not
an error, because poppler and Ghostscript are behaving as designed and no change
to this PDF would alter it. It is reported so that a "works everywhere" claim is
never made on unmeasured ground.

### 11.2 The figures were the actual failure

The same build reported `figures: 0` and, separately, `descriptions: 3 done, 0
outstanding`. Both were true, and together they were a silent failure: the
worklog was full and the PDF had no `Figure` element in it at all. Structure
tags emitted were `float`, `Caption`, `Div`, `Formula` — nothing else.
`check --pdf` reported nothing, because a checker cannot see a figure that
produced no element.

What Ally's MP3 said where each plot should be:

```
−2 −1 1 2 3 4 −3 −2 −1 1 2 3 Re Im
Figure 1: Complex Plane for a
```

`ALLY-FMT-010` catches this from the artefact side, which is the only side that
can see it: a run of six or more bare numeric or axis tokens with no `Figure` or
`Artifact` around them. Announcing that as data is worse than announcing
nothing — silence prompts a question and "minus two minus one one two three
four" does not.

The root cause was a two-line gate in `apply_descriptions`, and it is worth
recording in full because both halves were individually defensible:

| mode | `scans` | `touches_sources` | what happened |
|---|---|---|---|
| `worklog` | yes | no | returned at the gate; nothing applied |
| `caption` | no | yes | reached the wrapper with an empty `entries` |
| `placeholders` | yes | yes | the only mode that ever wrapped a figure |
| `off` | no | no | correct, by accident |

`scans` governs whether a run *authors* alt-text work — a worklog, a `<<TODO>>`
marker, an undescribed-figure warning — which `caption` mode deliberately does
not do. It has nothing to say about whether a description a human already wrote
is allowed to reach the PDF. Applying one is not a mode; `off` is the only
answer to "do nothing here". `tests/test_build.py` pins all four.

With the three `00B` descriptions written, the same build produces 8 `Figure`
elements, 46 `/Alt`, and `formats` goes clean.

### 11.3 Evidence you can play

A transcript diff settles a disagreement between two engineers. It does not
settle "does this actually work for a student", so `formats` also writes an MP3
(`say` plus `ffmpeg`) and a BRF (`lou_translate`, UEB grade 2) rendered from the
PDFBox transcript — the same text Ally will use.

Neither is the shipped artefact. Ally synthesises its own audio on its own
servers from its own voices, and its braille from its own tables. These are
evidence: a file a person can play or emboss. Both renderers are rule-based and
offline, so `tests/test_no_ai_in_production.py` remains true.

**Still outstanding: one Ally round-trip.** Upload a converted PDF, download
Ally's MP3 and BRF, transcribe the audio by ear, and compare against all five
local transcripts. Whichever matches is the one `ALLY-FMT-001` should weight.
PDFBox is chosen on the strength of Ally being a PDFBox-based Java service, and
that is an inference, not a measurement. Until it is measured, treat
`ALLY-FMT-002` as the conservative gate: agreement across every engine is right
whichever one Ally turns out to use.

### 11.4 Two tooling hazards found on the way

Both cost more time than the feature did, and both are the kind that present as
a hang rather than an error.

**PyMuPDF segfaults after pikepdf.** `pymupdf._extra`'s `create_module` crashes
when it is imported into a process that has already loaded pikepdf — which
`check_formats` always has, since `read_structure` reads the tag tree with
pikepdf. It takes the interpreter with it, and under pytest that presents as the
suite stopping silently on whichever test happens to run sixth. `_extract_mupdf`
therefore runs out-of-process, like four of the five extractors already did.

**An extractor that writes beside its input can block on stdin.** PDFBox's
`export:text` and Ghostscript's `txtwrite` both need an output path. Writing it
next to the PDF meant a killed run left the file behind, and the next run found
its output occupied and asked — on stdin — whether to overwrite. Extractor
scratch now goes to a temp directory and every subprocess gets
`stdin=DEVNULL`. Writing into the corpus was also simply wrong: `check` is a
read-only command.

### 11.5 A numbered equation loses its number, and only for some readers

`ALLY-PDF-050` on `fa26/dis/00B`:

```
text inside a Formula described as <the square root of z z bar; ...>
is never announced: '(1)'
```

Reproduced in six lines — an `amsmath` `equation` environment under
`tagging=on` produces:

```
Formula   mcids [1, 3]
└── Lbl   mcids [2]        <- the equation number
```

The `Lbl` is a *child* of the `Formula`, and `/Alt` on an element replaces its
entire subtree, so the number goes with it. This is latex-lab's structure, not
anything this tool emits.

Who loses it is worth stating precisely, because the two answers differ:

| Reader | Announces |
|---|---|
| structure tree (JAWS, NVDA, VoiceOver) | `…the absolute value of z` then straight on to part (d) |
| PDFBox (Canvas Ally) | `…the absolute value of z (1)` |

So Ally's MP3 and braille are unaffected; a screen reader following the tag tree
is not. **Not fixed here.** The socket plugs in `latexally-math.sty` have a
documented history of failing subtly when reached into — a literal `#2` that
shipped, an undeclared socket that raised twice per formula — and none of the
available hooks knows whether the formula it is describing is numbered. The
finding now names the tag, says which readers are affected, and gives a remedy
the author can actually apply (use an unnumbered environment, or reference the
equation in prose) rather than "move the readable content out of it", which is
not advice anyone can follow against kernel-emitted structure.

Fixing it properly means either persuading latex-lab to make `Lbl` a sibling of
`Formula`, or folding `\theequation` into the speech string at the point the
alt is set. The first is upstream; the second needs a reliable way to know a
formula is numbered from inside the `math/content` socket.

## 12. One palette, for text and for drawings

### 12.1 What the corpus actually had

Three blues, for one purpose:

| where | value | contrast |
|---|---|---|
| `solutionColor`, discussion preambles | `rgb(0.2,0.6,0.9)` = #3399E6 | 3.07:1 — **fails AA** |
| `\sol` in every homework driver | plain `blue` = #0000FF | 8.59:1 |
| `blueish` in `ee66.sty` | `rgb(0.7,0.1,0.7)` | 5.65:1 — and it is magenta |

`answerColor` was two different colours depending on document type:
`rgb(0.1,0.6,0.9)` from `fa26/fa26.sty:6`, silently overridden to
`rgb(0.2,0.2,0.9)` by `fa26/dis/preambleFa24.tex:30` for every discussion. The
undocumented override was the only accessible one of the pair.

And the figures used none of these names. Counted across `fa26`: 114 uses of
`solutionColor` in prose and **zero** in any drawing — every `\addplot`,
`\draw` and `\fill` in the corpus spells its colour as a bare xcolor word. So
`fa26/dis/00B` drew its answer text in `answerColor` and its answer vectors in
`blue`, side by side on one page, with nothing able to reconcile them.

### 12.2 Why the old remap could not fix it

`\accessconformingcolors` derived a value per colour *name*, darkening each just
enough to clear 4.5:1. That is the right operation for the question it was
asked — "does this colour pass?" — and it cannot answer the other one: "is this
the same colour as the one beside it?". Deriving per name is in fact how the
three blues stayed three blues, and it never touched a figure at all.

### 12.3 The palette

Five tokens, hue held exactly at the primaries and secondaries, lightness moved
until each is as dark as it can be and still recognisably its own hue:

| token | hex | on white | hue |
|---|---|---|---|
| `allyBlue` | #0000FF | 8.59:1 | 240°, pure |
| `allyRed` | #CC0000 | 5.89:1 | 0° |
| `allyGreen` | #006600 | 7.24:1 | 120° |
| `allyPurple` | #6A0DAD | 9.24:1 | 275° |
| `allyOrange` | #B35A00 | 4.80:1 | 30° |

Pure primaries were the brief and only one survives WCAG: #FF0000 is 4.00:1 and
fails AA for body text, #00FF00 is 1.37:1 and fails comprehensively. Only pure
blue passes untouched, and it is kept exactly.

`\accesspalette` binds the course's five names *and* xcolor's base names —
`blue`, `red`, `green`, `purple`, `orange` — to those tokens at `begindocument`.
Binding the base names is what reaches the drawings, and it costs no document
edits at all across roughly 2,000 figure-bearing files. Measured on
`fa26/dis/00B`: before, #187AC4 ×18 (text) and #0000FF ×8 (vectors); after,
#0000FF ×26.

`conforming` survives as the narrower mode, for a document whose figures must
keep the exact hues they were drawn in.

### 12.4 What this does not fix

A figure that encodes meaning in hue alone — `fill=red` against `fill=green`
with no marker, pattern or label to tell them apart — is still a WCAG 1.4.1
failure after both are darkened. Darker is not distinguishable. Counted in
`fa26`: 32 `fill=red`, 20 `fill=green`, 20 `fill=yellow`. Fixing it means
editing the drawings, and that is the next piece of work, not this one.

`ALLY-SRC-010` also still reports the failing source colours, and should: a
document that only conforms with a tool in the loop still fails under a bare
`pdflatex`. The hint now says so, so the finding reads as a statement about the
source rather than a contradiction of the build.
