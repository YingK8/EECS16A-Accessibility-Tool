# Quickstart

Concrete commands, with the directory you run them from and what you should see.

Two paths matter throughout:

| | |
|---|---|
| **Tool repo** | `/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool` |
| **Your corpus** | `/Users/meli/Desktop/Kevin/UCB/EECS 16A/questionBank` |

The tool never writes to your corpus unless you explicitly pass `--write`.

---

## 1. One-time setup

Run this once, from the **tool repo**:

```bash
cd "/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool"
python3 -m pip install -e '.[all]'
```

`-e` means "editable": the command uses the source in this folder, so editing
the code takes effect immediately with no reinstall.

Check it worked — this can be run from **any directory**:

```bash
latexa11y --version
```

```
latexa11y, version 0.1.0
```

If you instead get `command not found`, your shell's `PATH` does not include
pip's script directory. Use this form everywhere instead, from the tool repo:

```bash
python3 -m latexa11y.cli --version
```

---

## 2. See the demonstration PDFs

Run from **anywhere** — the script finds its own paths:

```bash
"/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool/examples/build.sh"
```

Or, if you are already in the tool repo, just `./examples/build.sh`.

Expected output:

```
==> demo-homework-sol
    demo-homework-sol.pdf (249428 bytes), 0 LaTeX errors, 0 tagpdf warnings
==> demo-homework-prob
    demo-homework-prob.pdf (197524 bytes), 0 LaTeX errors, 0 tagpdf warnings
==> demo-legacy (legacy markup through the compatibility shim)
    demo-legacy.pdf (91501 bytes)

PDFs are in .../EECS16A-Accessibility-Tool/examples/build
```

Open them:

```bash
open "/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool/examples/build"
```

**What to look for.** In Preview, open the sidebar and choose
*Table of Contents* (in Acrobat: the bookmark ribbon on the left). You should
see a navigable tree:

```
Homework 9
    Question 1
        Part (a)
            Solution
        Part (b)
```

`demo-homework-sol.pdf` and `demo-homework-prob.pdf` are built from **one**
source file with different options — compare them to see solutions appear and
disappear. `demo-legacy.pdf` is built from legacy `\qns` / `\qitem` / `\sol`
markup through the compatibility shim.

To hear the alt text: in Preview, *Edit ▸ Speech ▸ Start Speaking*, or turn on
VoiceOver (⌘F5). The circuit figure announces its description and never reads
the labels drawn inside it.

---

## 3. Check your toolchain

From **anywhere**:

```bash
latexa11y doctor
```

This tells you whether a build here can actually produce a conforming PDF. On
this machine it currently ends with:

```
Tagging mode: legacy testphase — documents will be tagged, but this toolchain
cannot declare PDF/UA conformance in the PDF metadata.
```

That is expected on TeX Live 2025 and is explained in `MIGRATION.md`. Documents
still get tagged; they just cannot *claim* PDF/UA until TeX Live 2026.

---

## 4. Run against your real corpus

The `-p eecs16a` flag loads `profiles/eecs16a.yaml`, which already points at
your `questionBank`. These can be run from **any directory**.

### See what is in scope

```bash
latexa11y -p eecs16a files bank
```

```
420 files in scope bank
```

Scopes defined in the profile: `bank` (the shared question bank), `sp26`,
`exams`, and `live` (all three).

### Find every figure and write the worklogs

```bash
latexa11y -p eecs16a scan bank
```

```
266 call sites → 234 unique figures (1.14× deduplication)
described: 0   outstanding: 234
worklogs: .../questionBank/a11y/alt (42 files)
```

This creates `questionBank/a11y/alt/*.md` — one Markdown file per assignment
folder. **This is the only thing `scan` writes, and it writes nothing inside
your `.tex` files.** Safe to re-run: it regenerates the machine-written parts
and never overwrites text a person typed.

Add `--no-write` to see the counts without creating any files.

### Fill in descriptions

Open a worklog, e.g.:

```bash
open "/Users/meli/Desktop/Kevin/UCB/EECS 16A/questionBank/a11y/alt/questionBank-hw-10.md"
```

Each entry shows the machine-derived facts, the question the figure belongs to,
and an empty `### alt` section. Type the description there and change
`- status: todo` to `- status: approved`. The rules are in `ALT_TEXT_SPEC.md`.

### Write approved descriptions into the .tex files

**Dry run first** — this changes nothing and prints the diff:

```bash
latexa11y -p eecs16a apply bank --show-diff
```

When the diff looks right:

```bash
latexa11y -p eecs16a apply bank --write
```

Only entries marked `approved` are written. Rollback is `git checkout` inside
`questionBank`; the tool creates no `.bak` files.

### Validate

```bash
latexa11y -p eecs16a check bank
```

Add a built PDF and its log for the full check:

```bash
latexa11y -p eecs16a check bank --pdf out.pdf --log out.log
```

Exit codes: `0` clean, `1` findings, `2` could not run.

---

## 5. Make one of your own assignments accessible

Add **two lines** to a driver file such as `sp26/hw/9/sol9.tex`. Nothing else
changes — not the body, not the macros, not the layout.

```latex
\DocumentMetadata{lang=en-US,pdfversion=2.0,
                  testphase={phase-III,math,table,graphic,firstaid}}   % line 1
\documentclass[11pt]{article}
\usepackage{../../../timestamp}
\usepackage{../../../ee16}
\usepackage{../../../markup}
\usepackage{../../sp26}
...
\newcommand{\sol}[1]{{\color{blue} \textbf{Solution: } #1}}
\usepackage{latexa11y-ee16}                                            % line 2
\input{body}
```

`\DocumentMetadata` **must be the very first line**, before `\documentclass`.
`\usepackage{latexa11y-ee16}` goes **after** `ee16` and `markup`.

Then build from that assignment's own folder, so its relative paths resolve:

```bash
cd "/Users/meli/Desktop/Kevin/UCB/EECS 16A/questionBank/sp26/hw/9"
export TEXINPUTS="/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool/tex:"
pdflatex sol9.tex && pdflatex sol9.tex && pdflatex sol9.tex
```

**Three runs are required, not superstition.** tagpdf resolves the structure
tree across runs via the `.aux` file; after one run the reading order is wrong.

`TEXINPUTS` tells LaTeX where to find `latexa11y-ee16.sty`. The trailing colon
is required — it means "then also look in the normal places".

### Skip TEXINPUTS entirely (already done on this machine)

Symlink the style directory into your personal TeX tree once:

```bash
ln -s "/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool/tex" \
      "$(kpsewhich -var-value TEXMFHOME)/tex/latex/latexa11y"
```

After that `\usepackage{latexa11y-ee16}` works from anywhere with no
`TEXINPUTS` at all, and the build above becomes:

```bash
cd "/Users/meli/Desktop/Kevin/UCB/EECS 16A/questionBank/sp26/hw/9"
pdflatex sol9.tex && pdflatex sol9.tex && pdflatex sol9.tex
```

A **symlink**, not a copy: the style files are then always the ones in the
repository, so editing them takes effect immediately — the same reason the
Python package is installed with `pip install -e`. A copy silently goes stale
and you end up debugging a version you are no longer editing.

Verify at any time with:

```bash
kpsewhich latexa11y-ee16.sty
```

To undo: `rm "$(kpsewhich -var-value TEXMFHOME)/tex/latex/latexa11y"`.

---

## 6. Run the tests

From the **tool repo**:

```bash
cd "/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool"
python3 -m pytest tests/ -q
```

```
75 passed
```

These compile real LaTeX and assert on the resulting PDF structure, so they take
about 15 seconds and need `pdflatex` on your `PATH`.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `command not found: latexa11y` | pip's script dir is not on `PATH`. Use `python3 -m latexa11y.cli ...` from the tool repo. |
| `LaTeX Error: File 'latexa11y-ee16.sty' not found` | `TEXINPUTS` not set, or missing its trailing colon. See §5. |
| `File '../../../timestamp.sty' not found` | You are in the wrong directory. Build from the assignment's own folder. |
| Bookmarks pane is empty | Fewer than three `pdflatex` runs, or `\DocumentMetadata` is not the first line. |
| `error: no worklogs found; run scan first` | `apply` before `scan`. Run `latexa11y -p eecs16a scan bank`. |
| `unknown scope 'foo'` | Valid scopes are `bank`, `sp26`, `exams`, `live`, or a path relative to the corpus root. |
