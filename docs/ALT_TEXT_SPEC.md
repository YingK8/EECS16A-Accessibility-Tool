# Alt-text authoring spec

The rules staff and agents follow when filling in a worklog. Grounded in WCAG
2.1 SC 1.1.1, the W3C WAI complex-images tutorial, NCAM's *Effective Practices
for Description of Science Content*, and the DIAGRAM Center guidelines.

The test that matters: **cover the figure, read only your description, and try
to answer the question.** If you cannot, the description is wrong. If you can
answer it *more easily* than a sighted student, it is also wrong — see rule 9.

---

## The rules

1. **Plain words only.** No `$`, no backslashes, no braces. `/Alt` is a plain PDF
   string; a reader announces `$\frac{1}{2}$` as "dollar backslash f-r-a-c one
   two dollar". Write "one half", "V sub C1" or "VC1".

2. **Never open with "image of".** Or "figure showing", "a diagram of",
   "screenshot of". The reader already said "graphic".

3. **Never repeat the caption.** It is a sibling element, announced immediately
   after your description. Repeating it makes the reader say the same sentence
   twice — and this corpus has 4,099 captions.

4. **Convey the data, not the drawing.** Not "a line goes up and to the right
   through a grid" but "a straight fit line b = 0.95a + 1". Not "the box on the
   left" but "the non-inverting input".

5. **No colour-only identification.** Not "the blue curve" — name the series by
   its equation or its data.

6. **No spatial narration** — *unless position is the data.* Image stitching,
   mechanical drawings and map questions are genuine exceptions.

7. **Never the filename.** `latex-lab` uses the filename as its *default* alt, so
   "vehicle setup dot pdf" is what an undescribed figure sounds like.

8. **Length.** Aim for one sentence, roughly 200 characters. There is no WCAG
   character limit — this is a usability rule, because `/Alt` is atomic: a reader
   cannot pause inside it, re-read a sentence, or navigate a list. If you need
   more, use the `long` field, which becomes visible body text everyone benefits
   from.

9. **Disclosure.** Problem and solution builds share one source, so a figure a
   student sees must convey exactly what a sighted student sees — no more. Do not
   name the topology if identifying it is the question. Figures that appear only
   inside a solution may disclose freely; the worklog marks these
   `solution-only: yes`.

10. **Write once per unique figure.** Descriptions are keyed by content hash, so
    one description serves every call site. Never write "as shown above" or
    reference a specific assignment.

---

## Decide the disposition first

Apply in order, stop at the first hit:

1. Does the image contain text, numbers or symbols a reader needs? → **figure**
2. Is it referenced by `\ref`, a caption, or prose ("the circuit shown")? → **figure**
3. **Deletion test:** delete it and read the question. Can every part still be
   answered? If no → **figure**.
4. Is it a logo, banner, seal, rule, spacer or ornament? → **artifact**
5. Anything else → **figure**.

When in doubt, choose figure. A wrong artifact call silently deletes
information; a wrong figure call costs a second of speech.

Never infer decorative from a filename. In this corpus `lefthalfpic.jpg` and
`righthalfpic.jpg` look decorative and are the panorama halves an
image-stitching question is entirely about.

---

## Genre templates

### Circuits (circuitikz)

Traverse **electrically** — source, then branches, then ground. Name every
labelled component and every ground. State reference polarity for any labelled
voltage or current.

> Two capacitors, C1 and C2, each connected from its own top node to ground,
> with switch S1 joining the two top nodes.

Note what is absent: the fact that closing S1 puts them in parallel. That is the
question.

### Plots (pgfplots)

Type, axis names, ranges, then the actual points or the equation. For twelve
points or fewer, list them — shorter and more useful than any prose about shape.

> Scatter plot of b versus a on axes 0 to 9, with four data points: (2, 2),
> (4, 6), (6, 7), (8, 8), and a straight fit line b = 0.95a + 1.

Beyond twelve points, or three or more series, put the data in a table in the
body instead. The tool generates the table for you.

### Vectors and geometry

Ambient space, then each object by its definition, then the relation the
question turns on.

> Circle of radius R centred at origin O; vector r runs from O to a point on the
> circle at angle theta from the positive x-axis, and vector t starts at that
> point and runs tangent to the circle.

### State machines

Nodes, then edges as `from → to, label`, then self-loops.

> State-transition diagram with two states, A and B: A to B with weight 1, B to
> A with weight three quarters, and a self-loop on B with weight one quarter.

### Photographs and screenshots

Only the features the question uses, in the order it uses them. Transcribe any
visible text verbatim — it is unrecoverable otherwise.

> LM741 op-amp 8-pin DIP pinout. Pin 1 offset null, pin 2 inverting input,
> pin 3 non-inverting input, pin 4 V minus, pin 5 offset null, pin 6 output,
> pin 7 V plus, pin 8 not connected.

If the raster is a screenshot of a plot or table, the right fix is to rebuild it
as `pgfplots` or `tabular`. Flag it rather than describing it.

---

## Tables

Everything is a layout table until you say otherwise, which is fail-safe: a
layout grid announced as "table, 21 columns" traps a reader in a structure that
carries no meaning.

```latex
\begin{DataTable}[table/header-rows={1}]     % column headers
\begin{DataTable}[table/header-columns={1}]  % row headers
\begin{LayoutTable}                          % positioning only
```

Leave `|`, `\hline` and column specs alone — they are rules, not content, and
they do not affect tagging.

---

## Definition of done

A description is done when: a disposition is recorded; the text passes rules
1–10; a `long` description exists if rule 8 triggered; the status is `approved`;
and `latexa11y check --pdf` reports no `A11Y-PDF-002/003/004` for it.
