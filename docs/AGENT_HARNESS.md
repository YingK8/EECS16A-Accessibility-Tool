# Driving latexally from an LLM agent

Every command speaks `--json`; nothing is TUI-only. An agent uses exactly the
same code paths a human does.

---

## The contract

**What an agent submits is what ships.** There is no review stage. The worklog
carries `file`, `lines` and `alt_text`, so a description has nowhere to sit as a
draft: `agent submit` writes it and the next build puts it in the PDF.

This replaced a gate that recorded every submission as `needs-review` and wrote
only `approved` text. Removing it also removed the only thing between an unread
machine-written sentence and a student, and the failure that gate prevented,
plausible-sounding but wrong alt text in a document under a legal obligation, is
silent and expensive. `validate_description` catches malformed text, not wrong
text. Read what agents submit.

---

## The loop

```bash
# 1. Learn the rules (also embedded in every task payload).
latexally --json agent rules

# 2. Take the highest-value outstanding work.
latexally --json agent next-task --limit 5
```

A task is self-contained:

```json
{
  "id": "fig-b345050cb9d2",
  "genre": "circuit",
  "call_sites": 8,
  "question": "Capacitor Charge Sharing",
  "caption": "Charge sharing between two capacitors",
  "inside_solution": false,
  "image_absolute": null,
  "machine_facts": [
    "Circuit with 2 capacitors (C1, C2), 1 switch (S1), and 2 ground connections",
    "Capacitor C1 connects node at (0, 3.15) to node at (0, 0), with voltage V1"
  ],
  "still_needed": ["name the topology only if that is not what the question asks"],
  "rules": ["Write plain words. No LaTeX…", "…"]
}
```

`machine_facts` are deterministic extractions, not a draft description — do not
paste them verbatim. `caption` is given so you can avoid repeating it.
`inside_solution: false` means the disclosure rule applies: do not give away the
answer.

```bash
# 3. Submit. Validation runs BEFORE the write.
latexally --json agent submit --id fig-b345050cb9d2 \
  --description 'Two capacitors C1 and C2, each from its own top node to ground, joined by switch S1.'
```

Rejections are actionable and arrive in one turn:

```json
{"accepted": false, "rejections": [
  {"rule": "DESCRIPTION-OPENER", "message": "do not open with 'image of'; a reader already says 'graphic'"},
  {"rule": "DESCRIPTION-MARKUP", "message": "contains LaTeX markup; /Alt is a plain string…"}
]}
```

Correct and resubmit. Then verify:

```bash
latexally --json check live
```

---

## Self-verification

The full loop an agent should run before reporting done:

```bash
latexally --json doctor                 # can this toolchain even conform?
latexally --json scan live              # refresh; outstanding count
latexally --json agent next-task        # work
latexally --json agent submit ...       # propose, validated
latexally --json check live             # source lint
# after a human approves and a build runs:
latexally --json check live --pdf out.pdf --log out.log
```

`check --json` returns findings with a stable `rule`, a `severity`, the
`standard` each enforces, a `file`/`line` where one exists, and a `hint` naming
the fix. Treat `severity: "error"` as blocking.

Exit codes: `0` clean · `1` findings · `2` could not run.

---

## Making edits directly

If an agent edits `.tex` itself rather than going through the worklog, use the
same layer the tool uses — never a regex:

```python
from latexally.texlex import TexSource, EditBuffer

source = TexSource.from_path(path)          # comment- and verbatim-aware
buffer = EditBuffer(path)
for span in source.environments("circuitikz"):   # live code only
    buffer.wrap(span.start, span.end, "\\begin{Described}{…}\n", "\n\\end{Described}")
print(buffer.diff(source.text))             # review before writing
path.write_bytes(source.encode(buffer.apply(source.text)))
```

Why this matters: in this corpus **25–33% of `\includegraphics` call sites sit on
commented-out lines**. A regex wrapper inserts `\begin{Described}` on a commented
line, `%` swallows it, and the file no longer compiles. `TexSource.finditer`
only ever matches live code; `EditBuffer` refuses overlapping edits instead of
silently clobbering, and records wraps as two insertions so the wrapped content
never passes through Python.

---

## What an agent must not do

* Paste `machine_facts` as a description.
* Describe a figure it has not seen when the task says `image_absolute` — open
  the image first; rasters carry no recoverable content.
* Reveal an answer in a figure whose task says `inside_solution: false`.
* Edit `file` or `lines`; both regenerate on the next `scan`.
