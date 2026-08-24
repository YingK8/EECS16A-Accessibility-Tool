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
| T011 | Node, plus `speech-rule-engine` in `node_modules` | WARN, and spoken math is skipped. `npm install` in the repo root, or install Node 20+ |
| T012 | `pikepdf`, `pymupdf` and `latex2mathml` import | WARN. `pip install 'latexally[pdf,tui,math]'` |

T001 to T009 decide whether a conforming build is possible. T010 to T012 only
turn features off.

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
