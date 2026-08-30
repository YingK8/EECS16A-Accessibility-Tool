# EE 66 question bank

## Accessibility: running latexally

[latexally](../EECS16A-Accessibility-Tool) is a custom tool to help convert course
PDFs to meet ADA Title II / WCAG 2.1 AA / PDF-UA. The tool can be cloned in as a
separate repository, on the same level as questionBank/.

Once installed, `latexally` runs from anywhere inside questionBank/.

### Setup

#### 1. Install Tool

[EECS16A-Accessibility-Tool Github Repository](https://github.com/YingK8/EECS16A-Accessibility-Tool.git)

Clone it onto the same level of this repository, so the profile's default path
(`../../questionBank`) resolves:

```
EECS 16A/
├── questionBank/                 <- you are here
└── EECS16A-Accessibility-Tool/
```

#### 2. Install TeX Live and the tagging packages:

```bash
brew install --cask mactex-no-gui
sudo tlmgr install tagpdf pdfmanagement-testphase latex-lab latexmk
```

`latexally formats` reads the PDF back the way each downstream reader does, and
renders an MP3 and a braille file you can check by hand. Those need a few more
tools. None is required — each one missing narrows the evidence and says so:

```bash
brew install poppler liblouis ghostscript ffmpeg
```

It also needs Java and a PDFBox jar, because PDFBox is the text layer Canvas
Ally itself uses — the one extractor whose answer is a student's answer:

```bash
brew install openjdk
./vendor/pdfbox/fetch.sh
```

The jar is fetched rather than committed — it is 13.5 MB of binary, and Apache
ships it as a Maven Central release rather than as anything a checkout gives
you. The script pins the version and its SHA-256, and is a no-op once the jar
is in place. `LATEXALLY_PDFBOX_JAR` overrides the path if you keep one
elsewhere.

#### 3. Setup `latexally` PATH

Put `latexally` on your PATH, so it can be run from anywhere in this corpus:

```bash
cd ../EECS16A-Accessibility-Tool
uv tool install --editable .
```

#### 4. Verify

Verify the toolchain can actually produce a tagged PDF:

```bash
cd ../questionBank
latexally -p ee66 doctor
```

Expect `Tagging mode: modern`. A `warn` on veraPDF is fine — it is the optional
PDF/UA validator; without it `check --pdf` falls back to weaker structure
assertions. Everything else should read `ok`.

`PDFBox (Ally's text layer)` and `format evidence tools` cover the extras above.
A `warn` on either only means `latexally formats` will have less to say.

### Run

#### 1. Where to run it

Scope of accessibility tool is defined by the assignment directory 
you run latexally from:

```bash
cd fa26/hw/1                # example: convert everything in hw1
latexally run               # run interactive TUI
latexally -help             # show all args and options

latexally files             # what is in scope: body.tex, prob1.tex, sol1.tex
latexally scan              # figures -> alt-text worklogs
latexally build --write     # converted copies under ally-out/
latexally formats <pdf>     # what Ally will read: transcripts, MP3, braille
```

The tool does not apply accessibility changes to commented-out texts.

`-p ee66` names the course. Two profiles ship with the tool — `ee66` for
current material and `eecs16a` for the archive under its old number — so the
commands will not guess which one you mean. Alias it if the flag gets old:

```bash
alias latexally='latexally -p ee66'
```

The only command that needs no `-p`: it asks which course on its first screen,
then walks scope, standards, colours and output, showing what each option costs
before anything is written. `Enter` moves on, `\` goes back, `space` ticks.

It saves the answers to `ally-out/run.yaml`, which replays unchanged:

```bash
latexally -p ee66 build --config ally-out/run.yaml --write
```

#### 2. What it does to figures

By default a run adds `\caption{<<TODO:figure-id>>}` to every figure and table
that has none, so what still needs writing is printed on the page rather than
hidden in `/Alt` where only a screen reader would find it. Captions go only
into floats — a bare `\includegraphics` is reported and skipped, because
`\caption` outside a `figure` or `table` does not compile — and a figure that
already has a caption is left alone, so re-running only touches what is
outstanding.

**Your `.tex` is not edited unless you ask.** By default a run writes an
assignment's output into that assignment's own `accessible/` folder — the
converted `.tex`, the PDF and the logs together — and leaves your sources
alone:

```
fa26/hw/1/
├── prob1.tex                  your source, untouched
└── accessible/
    ├── pdf/                   the tagged PDFs
    ├── tex/                   what they were built from
    └── logs/
```

The captions and markers go into the copies under `accessible/tex/`. `--edit`
is what puts them over the originals instead, and it refuses to start on a
dirty git worktree so the undo below stays total. `-o` with `mirror` mode keeps
everything in one tree away from the corpus.

Alt-text worklogs stay in `ally-out/descriptions/` rather than moving into each
assignment: one description serves every assignment that inputs the figure.

An unfilled marker does not fail the build. It is reported by
`latexally -p ee66 check` and, under captions, visible in the PDF — so check
before handing anything out.

#### 3. Filling alt texts

`scan` writes YAML worklogs with one entry per figure. Course staff fill in
`alt_text`; writing the text is what approves it. Descriptions are filed by
what they describe, not by who used them — shared bank figures go to
`bank/`, so one description serves every assignment that inputs the figure:

```
ally-out/
├── descriptions/
│   ├── bank/hw_fig_alt_texts.yaml       <- the shared question bank
│   ├── fa26/hw_fig_alt_texts.yaml       <- this semester's own material
│   └── fa15/exam_fig_alt_texts.yaml
├── pdf/  logs/  tex/
└── run.yaml
```

Then `latexally -p ee66 apply --write` writes them into the sources.

**`ally-out/descriptions/` is course content and should be committed.** The
worklogs outlive any checkout of the tool, and a description that is lost has
to be written again by hand. The rest of `ally-out/` is build output.

#### 4. Checking what a student will actually hear

`check` reads the PDF. It does not read what Canvas Ally makes *from* the PDF,
and those are different questions — five text extractors disagree about the same
correct file, because `/Alt` and `/ActualText` are instructions a reader may or
may not follow.

```bash
latexally -p ee66 formats accessible/pdf/fa26-dis-00B-solution-accessible.pdf
```

It writes, beside the PDF:

```
formats/<name>/
├── transcript-pdfbox.txt      <- what Canvas Ally reads. This is the one.
├── transcript-structure.txt      JAWS, NVDA, VoiceOver
├── transcript-mupdf.txt          Preview, pdf.js
├── transcript-poppler.txt        ignores /ActualText by design
├── transcript-ghostscript.txt    ignores it too
├── speech.mp3                 play it
├── braille.brf                emboss it
└── report.json
```

**Play the MP3.** It is the fastest way to find a figure nobody described: where
a plot should be, an undescribed one reads out as "minus two minus one one two
three four". `ALLY-FMT-010` reports the same thing without the listening.

The MP3 and BRF are evidence, not the artefact — Ally makes its own, from its
own voices and braille tables. They exist so a claim can be checked by a person
rather than argued from a log.

Errors worth knowing:

| code | means |
|---|---|
| `ALLY-FMT-001` | a description never reaches Ally's text layer, so the MP3 and braille will not contain it |
| `ALLY-FMT-003` | raw LaTeX survived into a transcript — a reader announces it as backslashes |
| `ALLY-FMT-010` | a drawing's axis labels reach the text layer as bare numbers, with no description to replace them |

`ALLY-FMT-002` is a warning, not an error: poppler and Ghostscript drop
substitute text by design and no change to your document alters that.

#### 5. Undoing a run

```bash
latexally -p ee66 clean --write     # delete what the run produced; sources untouched
latexally -p ee66 revert --write    # that, plus restore any .tex it rewrote
```

`revert` is `git checkout` underneath, so **commit your own work first** — it
cannot tell your edits from the tool's. Drop `--write` from either to see the
list before anything happens.

Full documentation, including the conformance scope and what is deliberately
not covered, is in the [tool's README](../EECS16A-Accessibility-Tool/README.md).