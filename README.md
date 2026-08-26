# latexally (Alpha)

Convert LaTeX instructional materials into tagged PDFs that meet the structural
requirements of **ADA Title II / WCAG 2.1 AA / PDF-UA**. See [Conformance](#conformance) 
for which requirements are covered and not covered.

Written for the UC Berkeley EECS 16A question bank. Customise yaml in /profile for any
other courses!

Contributors / Tools:
Kevin Ying
Claude Opus 5
---

## Capabilities

| | |
|---|---|
| **A tagged template layer** | `latexally-assignment`, `-exam`, and `-worksheet` classes producing an `H1` to `H4` hierarchy that reaches PDF bookmark panes and screen readers |
| **Strict alt text** | `Described` wraps any region, including text, math, TikZ, and tables, so assistive technology reads the description and never the contents |
| **Deterministic descriptions** | pgfplots axes, circuitikz netlists, and state machines described from source, with no model in the loop |
| **A YAML worklog** | three keys per figure: `file`, `lines`, `alt_text`. Writing the text is what approves it |
| **A conformance checker** | source lint, build-log analysis, and PDF structure assertions, each mapped to a Matterhorn checkpoint or WCAG SC |
| **An agent harness** | every command speaks `--json`; agents propose descriptions, humans approve them |
| **A legacy shim** | existing `\qns`, `\qitem`, and `\sol` question files keep compiling |

---

## Setup

Tested on macOS.

### Download

```bash
brew install --cask mactex-no-gui   # or the .pkg from https://tug.org/mactex/
```

Add tagging packages (in new terminal):

```bash
sudo tlmgr install tagpdf pdfmanagement-testphase latex-lab latexmk
```

`uv run` builds `.venv` from `pyproject.toml` on first use.
`uv tool install --editable .` puts `latexally` on your PATH, for use from any
directory.

### Spoken math (Optional)

Adds speech text to LaTeX formulas. The engine is [MathCAT][mathcat], the
library NVDA and JAWS speak maths with; it is a Rust crate, vendored as a
submodule so the fork can carry course-specific rules.

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # once
git submodule update --init
cargo build --release --manifest-path mathspeech-driver/Cargo.toml
```

`doctor` reports this as T011. To take an upstream release:

```bash
git -C vendor/MathCAT fetch upstream
git -C vendor/MathCAT rebase upstream/main
```

[mathcat]: https://github.com/daisy/MathCAT

### Verification

```bash
uv run run.py doctor
```

Every check it runs, and what to do about each one:
[`src/latexally/README.md`](src/latexally/README.md).

veraPDF (optional) is the PDF/UA validator `check` defers to. Download from
https://docs.verapdf.org/install/ and put it on PATH.

### Updating TeX Live

```bash
sudo tlmgr update --self --all
```

Builds fail while this runs:

```
! LaTeX Error: File `l3backend-pdftex.def' not found.
```

Let it finish. Watch progress:

```bash
tail -f /usr/local/texlive/2025/texmf-var/web2c/tlmgr.log
```

---

## Quick start

```bash
uv run run.py
```

Asks what to convert, defaults the rest, and dry-runs unless told otherwise. It
opens on the folder you started it from — the first question is `local` or
`choose`, and `local` is already the answer. `Enter` moves on, `space` ticks.

Step by step, with what each screen shows:
[`docs/QUICKSTART.md`](docs/QUICKSTART.md).

### Command line

The menus and the flags call the same engine, so a run can be explored
interactively, saved, and replayed unchanged in CI:

```bash
latexally build sp26/hw/9 --write --question-tags
latexally build --config ally-out/run.yaml --write --json
```

Individual stages:

```bash
latexally files bank            # what is in scope
latexally scan bank             # figures -> YAML worklogs
latexally agent next-task -n 5  # what an agent should describe next
latexally apply bank --write    # approved descriptions -> .tex
latexally check bank --pdf out.pdf --log out.log
```

Exit codes: `0` clean, `1` findings, `2` could not run.

### One week's homework, in place

The default is a mirror: your `.tex` is read, never written, and the converted
copy goes to `ally-out/`. That is the right default and the wrong one for the
weekly job, where you want the folder itself to build.

**Where you run it is the scope.** There is no flag for it — standing in an
assignment folder means that assignment, standing at the top of the corpus
means all of it.

`--edit` writes the conversion back over the sources and drops the packages
they need beside them, so a bare `pdflatex` then produces the tagged PDF:

```bash
cd questionBank/sp26/hw/10

latexally scan                      # figures -> the alt-text worklogs
latexally build --write --edit
pdflatex prob10.tex                 # tagged; no TEXINPUTS, no output directory
```

Undo all of it:

```bash
latexally clean --write     # delete what the run produced; sources untouched
latexally revert --write    # that, and restore the .tex it rewrote
```

`clean` is the safe half: it deletes the worklogs, the `*-accessible.*` PDFs
and logs, the `latexally-*.sty` installed beside a driver, and the output tree.
It never touches a source file, needs no git, and is what you want when a run
has left residue behind.

`revert` adds the restore. It is `git checkout`, so it needs the corpus to be a
git repository and refuses outside one — and `--edit` already refuses to start
on a dirty worktree, so at the moment a revert runs the modifications in scope
are this tool's and nobody else's. **If you have edited those files since,
commit first**: the checkout cannot tell your work from the tool's.

Between them they delete what the tool wrote: the `*-accessible.*` PDFs and logs, the worklogs, and the
`latexally-*.sty` it installed. It leaves anything it does not recognise alone
— your own hand-built PDFs are ignored by git and would be gone forever if it
ran `git clean`. A worklog you have written descriptions into is reported and
kept, never deleted: git never had it, so nothing could give it back — pass
`--force` if you really mean it. Drop `--write` to see the list first; that is
the default for both.

`--edit` refuses on a dirty worktree, which is what makes the undo total. It
steps over its *own* output, so filling in a worklog and running again works.

**Why the corpus and not just the folder:** 16A drivers reach out of their own
directory — `\usepackage{../../../ee16}`,
`\input{../../../questionBank/hw/13/q_perpetual_motion}` — so a folder on its
own is not a corpus and does not build. Only the *uncommented* `\input` lines
are followed: `sp26/hw/10`'s body lists 33 questions with 27 commented out, and
the tool converts the 6 that are live.

#### Try it without touching anything

```bash
uv run python tests/revert_e2e.py sp26/hw/10 --keep
```

Copies that assignment's whole dependency closure into a throwaway git
repository, runs the stack, builds it with a bare `pdflatex`, and stops so you
can look. Without `--keep` it reverts and checks that every file came back
byte-identical.

### Output

```
questionBank/ally-out/          ← in the corpus, beside the material
  pdf/                          the converted PDFs
  logs/                         build logs
  tex/                          the converted sources
  baseline/                     untouched builds, only with --baseline
  descriptions/                 the alt-text worklogs staff fill in
    bank/hw_fig_alt_texts.yaml      the shared question bank
    bank/disc_fig_alt_texts.yaml
    sp26/hw_fig_alt_texts.yaml      one semester's own material
    fa15/exam_fig_alt_texts.yaml    the exam archive, by its own semester
    notes/notes_fig_alt_texts.yaml
  run.yaml                      this run's settings, replayable
```

Worklogs are filed by semester and material type. The semester is the first
path component shaped like one, wherever it sits, so `exams/fa15/final` files
under `fa15`. Material belonging to no semester — above all the shared question
bank, where roughly three quarters of this corpus's graphics live — goes to
`bank/`: a description is content-addressed and serves every assignment that
uses the figure, so filing it under whichever semester happened to `\input` it
would scatter one description across several files.

The output root sits in the corpus rather than in this repository, because the
descriptions are course content — written by course staff, outliving any
checkout of this tool. `-o` moves it anywhere you like.

Under `--edit` the converted sources are not in `tex/` but in the corpus, over
the originals. `latexally revert` removes both.

Every version of each assignment is converted, not just the solutions. `sol9.tex`
and `prob9.tex` share one body and differ only in whether `\sol` prints, and the
blank one is what students receive.

`--baseline` also builds the untouched original and reports a `pixel diff`
against it. It is a difference, not a score: 2.34% means 97.66% of the page is
pixel-identical, and the residue is tagging's own repagination. Off by default,
because it is a second full LaTeX run of every document — 70s against 50s on
sp26/hw/10 — and it answers a question you ask while adopting the tool, not on
every rebuild of a homework.

---

## Conformance

### ADA Title II

DOJ's 2024 rule added Subpart H to 28 CFR Part 35 and adopted **WCAG 2.1 Level
AA** as the technical standard for the web content a public entity provides.
That covers the PDFs a course posts.

| | |
|---|---|
| Rule | 28 CFR Part 35, Subpart H (§§ 35.200-35.205) |
| Technical standard | WCAG 2.1 Level AA |
| Compliance date, entities ≥50k population | **2027-04-26** |
| Compliance date, <50k and special districts | 2028-04-26 |

Title II's nondiscrimination and effective-communication duties apply now,
ahead of those dates.

UC Presidential Policy **IMT-1300** (2026-03-17) adopts WCAG 2.1 AA systemwide
and requires new digital content created after 2027-04-26 to conform, including
course material behind authentication.

**PDF/UA is declarable as of TeX Live 2026.** `tagging=on` and
`pdfstandard=ua-1` are both supported, so a build declares conformance in the
PDF's own metadata rather than merely being tagged. `doctor` reports this as
`Tagging mode: modern`. Earlier toolchains reached only the legacy testphase
mode, which tagged documents but could claim nothing.

**Section 508 (36 CFR Part 1194) is not the governing standard here.** It binds
federal agencies and their contractors; the operative obligation for this
corpus is Title II.

### Validators

WCAG is a content standard. PDF/UA-1 (ISO 14289-1) is the file-format
expression of accessibility, tested via the PDF Association's **Matterhorn
Protocol 1.1**. They are related, and neither contains the other:

```
ADA Title II   =  WCAG 2.1 Level AA         ⊅ and ⊄  PDF/UA-1
PDF/UA-1       =  ISO 14289-1
                    ├── Matterhorn 1.1: 31 checkpoints, 136 failure conditions
                    │     ├── 87 machine-checkable   ← what veraPDF implements
                    │     ├── 47 require human judgment
                    │     └──  2 have no defined test
                    └── contains NO colour-contrast requirement
```

A clean veraPDF run is a floor, not a compliance claim: it covers 87 of 136
conditions of one standard that itself omits requirements WCAG imposes. Colour
contrast is the clearest example. PDF/UA says nothing about it, and WCAG 1.4.3
is among the most commonly failed criteria in this corpus.

`check --pdf` runs veraPDF when it is on PATH and reports each failure as
`ALLY-VERA-<clause>-<test>`, joined back to the tagpdf label in the `.aux` so a
failure names a construct rather than an object number. Without it, `check`
falls back to its own structure assertions, which cover far less; `doctor` T010
says which of the two you are getting.

### Scope

| Area | Standard | Rule |
|---|---|---|
| Tagging (structure tree exists, nothing untagged) | WCAG 1.3.1, 1.3.2 | `ALLY-SRC-001`, `ALLY-PDF-001`, `ALLY-PDF-030/031`, `ALLY-LOG-*` |
| Heading hierarchy, H1-first, no level skips | WCAG 2.4.6; Matterhorn 14 | `ALLY-PDF-010/011/012` |
| Figure alt text, human-approved, no placeholders | WCAG 1.1.1; Matterhorn 13 | `ALLY-PDF-002/003/004` |
| Math alt text (speech in `/Alt`, MathML and TeX as `/AF`) | WCAG 1.1.1; Matterhorn 17 | `ALLY-PDF-040/041` |
| Text colour contrast, named colours only | WCAG 1.4.3 | `ALLY-SRC-010` |
| Document title and language | WCAG 2.4.2, 3.1.1; Matterhorn 06/11 | `ALLY-PDF-020/021` |
| Bookmarks that navigate | WCAG 2.4.5 (technique PDF2) | `ALLY-PDF-022` |

Partial: reading order (WCAG 1.3.2; Matterhorn 09) catches untagged text but
does not verify the order itself; table header cells (WCAG 1.3.1; Matterhorn 15)
are covered by a golden fixture with no corpus rule; typeface handling applies
`\pdfgentounicode=1` but checks neither font embedding nor ToUnicode coverage.

Not covered: non-text contrast (WCAG 1.4.11), information conveyed by colour
alone (1.4.1), images of text (1.4.5), language of parts (3.1.2), link purpose
(2.4.4), list structure (Matterhorn 16), `/ViewerPreferences /DisplayDocTitle`
(Matterhorn 06/07), extraction permissions for AT (Matterhorn 26), and
verification with real assistive technology (47 human Matterhorn conditions).

Math needs one warning. `latex-lab` fills `/Alt` from its own template the moment
`pdfstandard=ua-1` is declared, and that template is the **LaTeX source**, which
a screen reader announces as "backslash f-r-a-c open brace" while veraPDF reports
the file clean. `ALLY-PDF-041` rejects it and `latexally-math` substitutes speech,
keeping MathML and the TeX source attached for braille and TeX-literate readers.

The gaps above are real. Structural preconditions are what a program can
establish; the 47 human-judgment Matterhorn conditions stay with the staff
running the tool, and the worklog exists to make that judgment explicit.

---

## Alt-text mechanism

To wrap an arbitrary region so a reader announces only the description, PDF
offers three mechanisms, of which one works:

* **`/ActualText`** is spec-correct and implementation-broken. Acrobat corrupts
  long strings, Chrome truncates at 16383 characters, and Firefox, MuPDF before
  1.27, PDF-XChange, and SumatraPDF ignore it. It cannot nest. Rejected.
* **`/Alt` alone** describes without replacing. A reader still descends into
  tagged content inside the region.
* **Artifact** removes content from the structure tree, and readers skip it
  unconditionally. The only reliable suppression primitive in PDF.

`Described` combines the last two, the construction tagpdf itself uses for TikZ:
open a `Figure` carrying `/Alt`, open one marked-content leaf, then suspend
tagging for the body.

```latex
\begin{Described}{Two capacitors C1 and C2, each from its own top node to
ground, joined by switch S1.}
  \begin{circuitikz} ... \end{circuitikz}
\end{Described}

\begin{Decorative}\includegraphics{banner.jpg}\end{Decorative}  % speaks nothing
```

The `Figure` opens before the body is typeset. Box the body first and a TikZ
node opens its own `text` sequence, which is then announced despite a correct
`/Alt`. `tests/test_latex_golden.py::test_described_has_no_nested_readable_element`
pins it.

---

## Documentation

| File | What it covers |
|---|---|
| `src/latexally/README.md` | `doctor`: every check, the tagging modes, and exit codes |
| `docs/QUICKSTART.md` | The interactive runner, screen by screen |
| `docs/METHODOLOGY.md` | How this was researched and built, and what was verified by running rather than reading |
| `docs/ALT_TEXT_SPEC.md` | The authoring spec staff and agents follow, with genre templates |
| `docs/AGENT_HARNESS.md` | Driving the tool from an LLM agent |
| `docs/MIGRATION.md` | Converting an existing corpus, and what the shim cannot cover |

---

## References

**Regulation**

1. U.S. Department of Justice. *Nondiscrimination on the Basis of Disability: Accessibility of Web Information and Services of State and Local Government Entities*. 89 FR 31320 (2024-04-24), RIN 1190-AA79. Codified at 28 CFR pt. 35, subpt. H. <https://www.federalregister.gov/documents/2024/04/24/2024-07758/nondiscrimination-on-the-basis-of-disability-accessibility-of-web-information-and-services-of-state>
2. U.S. Department of Justice. *Extension of Compliance Dates*. 91 FR 20902 (2026-04-20). <https://www.federalregister.gov/documents/2026/04/20/2026-07663/extension-of-compliance-dates-for-nondiscrimination-on-the-basis-of-disability-accessibility-of-web>
3. U.S. Department of Justice. *Fact Sheet: New Rule on the Accessibility of Web Content and Mobile Apps*. <https://www.ada.gov/resources/2024-03-08-web-rule/>
4. U.S. Department of Justice. *ADA Title II Web Rule: First Steps Toward Compliance*. <https://www.ada.gov/resources/web-rule-first-steps/>
5. University of California. *Presidential Policy IMT-1300: Digital Accessibility* (2026-03-17). <https://policy.ucop.edu/doc/7000611/IMT-1300>
6. U.S. Access Board. *Section 508 Standards for ICT*. 36 CFR pt. 1194. Not the governing standard here. <https://www.access-board.gov/ict/>

**Technical standards**

7. W3C. *Web Content Accessibility Guidelines (WCAG) 2.1*. W3C Recommendation, 2018-06-05. The version incorporated by reference. <https://www.w3.org/TR/WCAG21/>
8. W3C. *Techniques for WCAG 2.1: PDF*. PDF1 (alt text), PDF2 (bookmarks). <https://www.w3.org/WAI/WCAG21/Techniques/#pdf>
9. ISO. *Document Management Applications: Electronic Document File Format Enhancement for Accessibility, Part 1 (PDF/UA-1)*. ISO 14289-1:2014. <https://pdfa.org/resource/iso-14289-pdfua/>
10. PDF Association. *Matterhorn Protocol 1.1* (2021). <https://pdfa.org/resource/matterhorn-protocol/>
11. University of Oulu. *Accessible Mathematical Documents*. Argues for TeX source in alt text over MathML or generated speech. <https://ict.oulu.fi/19002/?lang=en>

**Software**

12. `tagpdf` and `latex-lab`. LaTeX's tagging layer. <https://ctan.org/pkg/tagpdf>, <https://ctan.org/pkg/latex-lab>
13. `latexmk`. Build driver. <https://ctan.org/pkg/latexmk>
14. pikepdf. Structure-tree and content-stream reading. No high-level tagged-PDF API, so `check/structure.py` walks `/K` by hand. <https://pikepdf.readthedocs.io/>
15. latex2mathml. LaTeX to MathML. <https://pypi.org/project/latex2mathml/>
16. MathCAT. MathML to ClearSpeak speech; the engine NVDA and JAWS use. MIT, vendored at `vendor/MathCAT`. <https://github.com/daisy/MathCAT>
17. veraPDF. PDF/UA validator, run by `check --pdf`. Probed by `doctor` T010. <https://verapdf.org/>

**Evaluated and rejected**

18. MathJax. v4 dropped the MathML output jax. <https://www.mathjax.org/>
19. Speech Rule Engine. The first engine used here, replaced by MathCAT. Its ClearSpeak is good, but MathCAT is what the readers this corpus is read with actually run, and its rules are extensible. <https://speechruleengine.org/>
20. PyMuPDF. Good renderer, bad structure reader: `xref_get_key` returns `None` or raises on `/Alt` and `/ActualText`. <https://github.com/pymupdf/PyMuPDF/issues/4764>
21. `axessibility`. On pdfLaTeX it does not hook `$…$`, which is ~94% of this corpus's math. <https://ctan.org/pkg/axessibility>
22. Blackboard Ally. Institutional scorecard, not a validator: 0 to 1 scores with no published WCAG or PDF/UA mapping. <https://blackboard.github.io/rest-apis/ally/api>
