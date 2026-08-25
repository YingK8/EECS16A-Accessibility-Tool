# Quickstart

Concrete commands, with the directory you run them from and what you should see.

Two paths matter throughout:

| | |
|---|---|
| **Tool repo** | `/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool` |
| **Your corpus** | `/Users/meli/Desktop/Kevin/UCB/EECS 16A/questionBank` |

The tool never writes to your corpus unless you explicitly pass `--write`.

---

## 1. Setup

There is none. Every command below is `uv run run.py ...`, from the **tool
repo**:

```bash
cd "/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool"
uv run run.py --version
```

The first run creates `.venv` and installs everything; later runs reuse it and
start immediately. Editing the code takes effect at once — there is no
reinstall step, because there was no install step.

If you would rather type `latexally` than `uv run run.py`, and from any
directory:

```bash
uv tool install --editable .
```

```
latexally, version 0.1.0
```

If you instead get `command not found`, your shell's `PATH` does not include
pip's script directory. Use this form everywhere instead, from the tool repo:

```bash
python3 -m latexally.cli --version
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
latexally doctor
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

## 4. Convert your assignments

This is the one command most people need. It asks what to convert, which
standards to apply, and where to put the results:

```bash
latexally run
```

It opens already scanning the first scope the profile declares, so the list is
populated and the arrows work before you touch anything:

```
latexally run   1/7   What do you want to convert?
Nothing is ticked yet. Next: documents, standards, colours, output.
 Scope  ← →    sp26   bank   exams   homeworks   discussions   notes   live
 Path   type  sp26
 9 directories skipped — no file containing \begin{document}
 62 directories — 46 discussion, 16 homework
 2 of 62 selected, 1 in another scope
 [x] sp26/dis/00B   (3 document(s), 4 .tex)
 [ ] sp26/dis/01A   (3 document(s), 4 .tex)
 Tick at least one directory — nothing is selected yet.
← Scope  esc Back  n Next  a All  c Clear  q Quit  s Save run.yaml
```

**A scope is where to look** — a glob the profile declares under
`corpus.scopes`, or a path you type. `←` `→` walk them; the one you are on is
highlighted, and so is the row your cursor is on. The line above the list says
what the scope turned out to hold.

**The runner is keyboard only.** Clicks, hover and the scroll wheel are off, so
your terminal's own text selection and scrollback work as they always did.
Every control has a key, and the key that moves a row is printed in that row's
own gutter — the footer carries the rest and greys out whatever does not apply
where you are standing.

| key | does |
|---|---|
| `←` `→` | move between named scopes (in the path field, the text caret) |
| `↑` `↓` `PgUp` `PgDn` `Home` `End` | move between rows — and on a two-way choice like mirror / in-place, they *are* the choice |
| `Enter` | tick a row — `[x]` is ticked, `[ ]` is not |
| `a` / `c` | tick all / clear |
| `n` | Next. On Review it says `Build`, and that is what writes |
| `Esc` or `b` | Back, keeping everything you already changed. Step 1 has no back |
| `s` | write the settings to `<output>/run.yaml` without building — replay with `latexally run --config` or `latexally build --config` |
| `q` | quit; nothing is built |

**Nothing starts ticked**, and `n` stays greyed — with the reason under the
list — until you tick something. A scope of forty directories used to build all
forty because Enter was the obvious key.

**Ticks survive a change of scope.** A scope is a place to look, not a new
question, so you can tick two discussions under `sp26`, walk to `exams`, tick
one more, and run all three. Anything ticked outside the scope you are looking
at is named under the list — `also ticked: sp26/dis/00B` — because a tick you
cannot see is a tick you cannot take back, and it is still going to be built.

There is no second filter for *kind*: kind is what a directory turned out to be
(`hw` → homework, `dis` → discussion), which narrows the same axis a scope
already does — the profile declares both a `homeworks` scope and a `homework`
kind. What the current scope holds is stated above the list instead.

Then seven steps, each already carrying a sensible default:

| step | what it asks |
|---|---|
| Scope | which directories |
| Documents | solutions, the blank handout, answers-only |
| Standards | which standards to apply |
| Colours | which course colours fail WCAG, and what to do about each |
| Alt text | write a description template into undescribed figures, or skip |
| Output | where everything goes, and whether your corpus is edited |
| Review | every directory selected, the exact preamble, every file touched |

Every list scrolls, so a scope larger than your terminal is still reachable and
the footer never gets pushed off the bottom. Nothing is drawn on a painted
background: the app uses your terminal's own colours, and the cursor is reverse
video rather than a coloured bar — on the colour table it is confined to the
colour's name, so the swatches stay visible.

### Historical material and the question bank

Most of this corpus does not build as it stands. 3,175 `\input` targets across
1,109 files do not exist: an assignment from fa17 asks the *live* shared bank
for a question that has since been retired from it and now survives only in a
frozen snapshot (`fa19_questionBank/`, `fa23_questionBank/`, …). The assignment
compiled the year it was set and has been dead ever since, because nobody
rebuilds old homeworks until a conversion tool does.

The build finds the file and stands it in, so historical material converts. It
searches the assignment's **own semester** first, then backwards in time, then
forwards, and only then today's live bank — the question as it most likely read
when the assignment was set. **The corpus is never modified**: the stand-in is
copied into the output mirror at the path the source asked for, along with any
figures it carries, so the original is untouched and the substitution is a file
you can diff.

Every substitution is listed under `SUBSTITUTED INCLUDES` in `build-log.txt`
with where it came from and the one-line fix to make it permanent. Two states
matter:

| in the report | means |
|---|---|
| `ok (repaired)` | the file came from the assignment's own semester, or every bank had an identical copy. No choice was made. |
| `SUBSTITUTED` / `≈` | the banks that had it **disagree**. The stand-in may not be the question the assignment asked — check that one before shipping the PDF. |

`latexally check <scope>` reports the same thing without building, naming the
stand-in it would use and the fix. Repair is on by default; nothing about it is
silent.

A build has three outcomes, and the report says which: `✓` built clean, `!`
built but with errors in the log — the PDF is on disk and worth looking at —
and `✗` nothing came out. They used to be drawn as two, which sent people
hunting for output that was already there.

**Standards do not predict a cost.** An average of somebody else's documents is
not information about yours, so the screen states what each standard *is* and
the build reports what it actually cost — pages, bookmarks, figures and pixel
diff against the untouched original, per document, as each one finishes.

**Review** lists every directory you selected, the exact lines it will inject
and every file it will touch. Nothing is written until you press `n` there.

`latexally run` needs a terminal. In CI, or with input piped in, replay a saved
configuration instead — same engine, no screens:

```bash
latexally build --config ally-out/run.yaml --write
```

**Everything it produces goes under one directory** (`ally-out/` by default,
changeable from the Output screen):

```
ally-out/
  pdf/           the converted PDFs
  logs/run.log   every LaTeX log for the run, converted and untouched alike,
                 in one file — the only file in there
  tex/           the converted sources
  descriptions/  the worklogs staff fill in   ← the alt-text log
  baseline/      the untouched originals, for the before/after comparison
  run.yaml       this run's settings
  build-log.txt  how the run went: what built, what did not, and why
```

Every directory holds deliverables only. LaTeX scatters `.aux`, `.out`,
`.annotations` and a `-mathml-dummy.html` around each build; they are rewritten
from scratch every run and read by nothing, so they are deleted rather than
filed somewhere tidier. What survives in `math/` is not debris: `-mathml.html`
and `-mathspeech.ltx` are read by the *next* LaTeX pass — deleting them costs
the run its spoken math, silently, because the build still succeeds — and
`-mathspeech.json` is the cache that stops an unchanged formula being converted
twice.

`run.yaml` says how the run was *configured* and nothing about how it went, so
every run also writes `build-log.txt` beside it — the same report the runner
showed you, in plain text you can grep or paste. The LaTeX logs are one file
per run rather than one per document; each is preceded by a banner, so
`grep -A40 '=== sp26-dis-00B-solution' ally-out/logs/run.log` gets you the one
you want.

The result table is the honest one:

```
assignment    variant    pages  bookmarks  figures  errors  warnings  pixel diff
sp26/hw/9     solution      13         48        0       0         0       2.34%
sp26/hw/9     problem        8         48        0       0         0       2.38%
sp26/dis/09A  solution       5         21        0       0         0       1.44%
sp26/dis/09A  problem        3         21        0       0         0       1.93%
sp26/dis/09A  answer         4         21        0       0         0       1.70%
```

**Every version of each assignment is built**, because an assignment is not one
document: `sol9.tex` and `prob9.tex` pull in the same body and differ only in
whether `\sol` prints, and discussions add a student handout and an answers-only
build. The blank one is what students receive. The **Documents** screen
restricts this if you want only one.

**`pixel diff` is a difference, not a score, and low is the goal.** 2.34% means
97.66% of the page is pixel-identical to the original; the residue is tagging's
own repagination. If it were large, conversion would have moved your document.

### Doing the same thing without the menus

Every screen has a flag, and both routes call the same engine:

```bash
latexally build sp26/hw/9 sp26/dis/09A -o ally-out          # dry run
latexally build sp26/hw/9 sp26/dis/09A -o ally-out --write
latexally build --config ally-out/run.yaml --write          # replay
```

Useful flags: `--question-tags` (real H2 tags for question titles, at the cost
of reflowing about one question in five), `--house-colors` (keep the course
palette even where it fails contrast), `--in-place` (edit the corpus directly;
refuses unless its git worktree is clean), `--json` (for agents and CI).

---

## 5. Working with the corpus directly

`profiles/eecs16a.yaml` is loaded automatically — it is the only profile
installed, and it already points at your `questionBank`. Pass `-p <name>` only
once there is more than one course to choose between.

### See what is in scope

```bash
latexally files bank
```

```
420 files in scope bank
```

Scopes defined in the profile: `bank` (the shared question bank), `sp26`,
`exams`, and `live` (all three).

### Find every figure and write the worklogs

```bash
latexally scan bank
```

```
266 call sites → 234 unique figures (1.14× deduplication)
described: 0   outstanding: 234
worklogs: .../questionBank/ally/descriptions (42 files)
```

This creates `questionBank/ally/descriptions/*.yaml` — one file per assignment
folder. **This is the only thing `scan` writes, and it writes nothing inside
your `.tex` files.** Safe to re-run: it rebuilds the list of figures from the
source and never overwrites a description a person typed.

Add `--no-write` to see the counts without creating any files.

### Fill in descriptions

Open a worklog, e.g.:

```bash
open "/Users/meli/Desktop/Kevin/UCB/EECS 16A/questionBank/ally/descriptions/questionBank-hw-10.yaml"
```

Each entry is three lines: the figure's id, where it is first used, and an
empty `description:`. Type the description there — that is the whole step.

```yaml
  - figure: fig-3870069f66da
    file: sp26/dis/13A/questions/q_pca.tex:10
    description: >-
      Scatter plot on x and y axes running about minus 4 to 4, with four
      transactions marked.
```

The `>-` is YAML for "this paragraph continues on the following indented
lines"; it exists so a one-word edit is a one-line diff. A single-line
description can go straight after `description:` with no `>-`. The rules for
what to write are in `ALT_TEXT_SPEC.md`.

**Anything you write here is written into the PDF.** There is no separate
approval step, so an unfinished sentence ships as alt text. Leave
`description:` empty until it is ready. To see the machine-derived facts for a
figure — extracted circuit topology, plot data, labels — use
`latexally agent next-task`, which reports them without putting them in the
file.

### Write descriptions into the .tex files

**Dry run first** — this changes nothing and prints the diff:

```bash
latexally apply bank --show-diff
```

When the diff looks right:

```bash
latexally apply bank --write
```

Only entries marked `approved` are written. Rollback is `git checkout` inside
`questionBank`; the tool creates no `.bak` files.

### Validate

```bash
latexally check bank
```

Add a built PDF and its log for the full check:

```bash
latexally check bank --pdf out.pdf --log out.log
```

Exit codes: `0` clean, `1` findings, `2` could not run.

---

## 6. What conversion actually does to a file

Add **two lines** to a driver file such as `sp26/hw/9/sol9.tex`. Nothing else
changes — not the body, not the macros, not the layout.

```latex
\DocumentMetadata{lang=en-US,pdfversion=1.7,
                  testphase={phase-III,math,table,graphic,firstaid}}   % line 1
\documentclass[11pt]{article}
\usepackage{../../../timestamp}
\usepackage{../../../ee16}
\usepackage{../../../markup}
\usepackage{../../sp26}
...
\newcommand{\sol}[1]{{\color{blue} \textbf{Solution: } #1}}
\usepackage{latexally-ee16}                                            % line 2
\input{body}
```

`\DocumentMetadata` **must be the very first line**, before `\documentclass`.
`\usepackage{latexally-ee16}` goes **after** `ee16` and `markup`.

Then build from that assignment's own folder, so its relative paths resolve:

```bash
cd "/Users/meli/Desktop/Kevin/UCB/EECS 16A/questionBank/sp26/hw/9"
export TEXINPUTS="/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool/tex:"
pdflatex sol9.tex && pdflatex sol9.tex && pdflatex sol9.tex
```

**Three runs are required, not superstition.** tagpdf resolves the structure
tree across runs via the `.aux` file; after one run the reading order is wrong.

`TEXINPUTS` tells LaTeX where to find `latexally-ee16.sty`. The trailing colon
is required — it means "then also look in the normal places".

### Skip TEXINPUTS entirely (already done on this machine)

Symlink the style directory into your personal TeX tree once:

```bash
ln -s "/Users/meli/Desktop/Kevin/UCB/EECS 16A/EECS16A-Accessibility-Tool/tex" \
      "$(kpsewhich -var-value TEXMFHOME)/tex/latex/latexally"
```

After that `\usepackage{latexally-ee16}` works from anywhere with no
`TEXINPUTS` at all, and the build above becomes:

```bash
cd "/Users/meli/Desktop/Kevin/UCB/EECS 16A/questionBank/sp26/hw/9"
pdflatex sol9.tex && pdflatex sol9.tex && pdflatex sol9.tex
```

A **symlink**, not a copy: the style files are then always the ones in the
repository, so editing them takes effect immediately — the same reason `uv run`
uses the source tree rather than an installed copy. A copy silently goes stale
and you end up debugging a version you are no longer editing.

Verify at any time with:

```bash
kpsewhich latexally-ee16.sty
```

To undo: `rm "$(kpsewhich -var-value TEXMFHOME)/tex/latex/latexally"`.

---

## 7. Run the tests

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
| `command not found: latexally` | pip's script dir is not on `PATH`. Use `python3 -m latexally.cli ...` from the tool repo. |
| `LaTeX Error: File 'latexally-ee16.sty' not found` | `TEXINPUTS` not set, or missing its trailing colon. See §5. |
| `File '../../../timestamp.sty' not found` | You are in the wrong directory. Build from the assignment's own folder. |
| Bookmarks pane is empty | Fewer than three `pdflatex` runs, or `\DocumentMetadata` is not the first line. |
| `error: no worklogs found; run scan first` | `apply` before `scan`. Run `latexally scan bank`. |
| `unknown scope 'foo'` | Valid scopes are `bank`, `sp26`, `exams`, `live`, or a path relative to the corpus root. |
