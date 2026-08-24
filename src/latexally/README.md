# `latexally doctor`

Answers one question: can this toolchain produce a tagged, conforming PDF?

Run it before anything else. A LaTeX accessibility toolchain fails silently by
default. A missing testphase module or an unsupported `pdfstandard` value
produces an untagged PDF and no error, which is worse than a build failure when
the output carries a legal obligation.

There is no `doctor.py`. The command is `doctor` in `cli.py`; every check below
is implemented by `probe()` in `toolchain.py`.

## Invocation

```bash
uv run run.py doctor              # checks, then the tagging mode
uv run run.py doctor --strict     # WARN blocks too; use in CI
uv run run.py --json doctor       # machine-readable
uv run run.py -p eecs16a doctor   # name a profile when several are installed

uv run run.py doctor --tagging                 # will the SOURCE build at all?
uv run run.py doctor --tagging sp26            # one scope
uv run run.py doctor --tagging --fix           # show the rewrites
uv run run.py doctor --tagging --fix --write   # apply them to the corpus
```

`--json`, `--profile/-p`, `--corpus/-c` and `--quiet/-q` are global options, so
they precede the subcommand.

## Checks

| ID | Verifies | On failure |
|---|---|---|
| T001 | The engine the profile names is on PATH | Install TeX Live, put its bin directory on PATH |
| T002 | `latex.ltx` is readable and new enough for `tagging=on` | WARN when older than the engine's `min_format_date` |
| T003 | `tagpdf.sty` is installed | `tlmgr install tagpdf` |
| T004 | `pdfmanagement-testphase.sty` is installed | `tlmgr install pdfmanagement-testphase` |
| T005 | latex-lab testphase modules exist | `tlmgr install latex-lab` |
| T006 | `\DocumentMetadata{pdfstandard=...}` accepts the profile's value | WARN, and the detail lists the values this install does accept |
| T007 | The `tagging=on` switch is supported | WARN falls back to testphase modules; FAIL when none are usable |
| T008 | `latex-lab-testphase-tikz`, so `alt=` works on pictures | WARN routes figures through the `Described` wrapper instead |
| T009 | `latexmk` is on PATH | `tlmgr install latexmk` |
| T010 | `verapdf` is on PATH | WARN, and `check` falls back to its own structure assertions |
| T011 | MathCAT: the `vendor/MathCAT` submodule, and the driver built in `mathspeech-driver/` | WARN, and spoken math is skipped. `git submodule update --init`, then `cargo build --release` in `mathspeech-driver/` |
| T012 | `pikepdf`, `pymupdf` and `latex2mathml` import | WARN. `pip install 'latexally[pdf,tui,math]'` |

T001 to T009 decide whether a conforming build is possible. T010 to T012 only
turn features off.

## The tagging tier (`--tagging`)

A second question, deliberately kept apart from the first. T001-T012 ask
whether the *toolchain* can produce a conforming PDF; `--tagging` asks whether
the *source* compiles under tagging at all.

These are not accessibility rules. Every one is LaTeX that pdfLaTeX has always
accepted and that `\DocumentMetadata{testphase={tagpdf}}` rejects, so each
carries `standard="latex-lab limitation"` and none maps to a WCAG criterion or
a Matterhorn condition. They used to be reported by `check`, which made one
command answer two questions and buried both.

It is opt-in because it reads every file in scope; the T-checks are meant to
stay cheap enough to run before every command.

| Rule | Construct | Fixed by `--fix`? |
|---|---|---|
| `ALLY-SRC-040` | a counter in an enumitem `label=` option | yes — `\AllyEnumLabel` before the list |
| `ALLY-SRC-041` | `array` nested inside a matrix | yes — `\left[ … \right]` around the array |
| `ALLY-SRC-042` | a line break straight after display math | yes — `\mbox{}` before the break |
| `ALLY-SRC-043` | inline math opened with `\(` and closed with `$` | only when the span is really a formula |
| `ALLY-SRC-044` | a line break straight after a question macro | no — the fix deletes a break, which moves the page |

Counts depend on scope, so quote one with the other. Over the profile's default
scope (1,959 files: the live bank, sp26 and the exam archive) it is 107 / 14 /
36 / 4 / 166. Over every `.tex` in the tree including the frozen per-semester
snapshots (17,677 files) the first four are 667 / 357 / 306 / 30.

Two of the fixes contradict the hint the rule used to print, and the reasons
are in `latexally/rewrite.py`:

* **`ALLY-SRC-041` must not delete the array.** 257 of the 357 sites carry a
  `|` in the column spec. They are augmented matrices, and the array is where
  the divider lives.
* **`ALLY-SRC-043` is mostly not a formula.** 28 of 30 sites are a literal `(`
  written as `\(` — `\(1) put 4 resistors` — where closing with `\)` would
  set the paragraph in math mode. Those are reported, never rewritten.

A fix that repaginates is not a fix, so the rewrites are measured the same way
every other visual claim here is — untagged original against untagged
rewritten, at 110 dpi, so the number is the rewrite alone:

| File | Sites | Pixel diff |
|---|---|---|
| `hw/12/q_romeo_juliet_simplified` | `041` x10 | 0.0000% |
| `q_ct`, `q_ct_complex_exp_potpourri` | `040` x4 each | 0.0000% |
| `q_syllabus` | `040` x5 | 0.0048% |

The 0.0048% is enumitem: `[label=...]` sizes the label box from the label,
`\labelenumN` uses the standard `\labelwidth`, so one list indents by a hair.
Two orders of magnitude below the 0.42% that deleting a line break costs, and
below the 2.596% that enabling tagging costs on any route.

`--fix` alone prints diffs and changes nothing. `--fix --write` edits the
corpus and refuses on a dirty git worktree, which is the only thing that makes
588 rewritten files revertible. `build` applies the same rewrites to the output
mirror on every run, so a conversion never needs the corpus touched at all.

## Tagging modes

The report ends with one of three. This is the line the rest of the pipeline
reads.

| Mode | Means |
|---|---|
| `modern` | `tagging=on` works, and the PDF can declare PDF/UA |
| `legacy testphase` | Documents get tagged, but nothing may claim conformance in the metadata. TeX Live 2025 lands here |
| `unavailable` | A build would emit an untagged PDF, so the pipeline refuses to run |

## Exit codes

| Code | When |
|---|---|
| 0 | No check failed, and tagging is available |
| 1 | A check FAILed, or tagging is `unavailable`, or `--strict` and any WARN |
| 2 | The profile could not be loaded, so nothing ran |
