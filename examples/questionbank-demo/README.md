# Question-bank demonstration

Real EECS 16A material, described and tagged. Where `demo-homework.tex` is
written for the demo, everything under `corpus/` was copied verbatim out of
`questionBank/` and left alone except for the `Described` wrappers that
`latexally apply` inserted.

```
corpus/questionBank/sec/13/q_polynomial_fir_convolution.tex   1 figure
corpus/questionBank/sec/13/q_freq_resp_dag_diagram.tex        2 figures
corpus/sp26/dis/13A/questions/q_pca.tex                       4 figures
corpus/sp26/dis/13A/questions/q_diode_ls.tex                  1 figure
corpus/ally/descriptions/                                     the worklogs
```

Eight unique figures across nine call sites: two block diagrams, a complex-plane
sketch, two scatter plots, two number lines and a diode circuit. `./build.sh`
rebuilds both variants into `build/`.

|                    | pages | figures with `/Alt` |
| ------------------ | ----: | ------------------: |
| `-prob.pdf`        |     8 |                   6 |
| `-sol.pdf`         |    10 |                   9 |

The student build is three short because those descriptions are marked
`solution-only: yes` and would give the answer away.

## What the PDFs demonstrate

Every figure is one `/Figure` structure element carrying `/Alt`, whose single
child is one marked-content leaf. Verified on `-sol.pdf`: nine figures, nine
single-`/MCR` kids, **zero** nested tagged constructs inside any of them — and
between 3 and 43 glyph runs sealed inside each leaf. A screen reader following
the structure tree announces "graphic", speaks the description, and never
reaches those glyphs. See `docs/ALT_TEXT_SPEC.md`, "What a reader actually does
with `/Alt`".

Nothing here shows on hover. `/Alt` is not a tooltip.

## Two things worth knowing

**A corpus bug is patched in the copy.** `q_polynomial_fir_convolution.tex`
opens an inline formula with `\(` and closes it with `$`:

```latex
... products of \(a_i\) and \(b_j$ where \(i + j = n\):
```

Untagged pdflatex tolerates it. The tagging toolchain's math grabber does not,
and the solution build dies with "Argument of \__math_grab_inline:w has an extra
}". The copy under `corpus/` closes it with `\)`; **the corpus itself is still
broken** and `latexally build sp26/dis/13A` fails on the solution and answer
variants because of it.

**The driver supplies four course macros.** `\bmqty`, `\e`, `\wt` and the `hint`
environment are reproduced in `demo-questionbank.tex` from `questionBank/ee16.sty`
and `questionBank/sp26/sp26.sty`, so the demo does not drag in the whole course
preamble chain. Same meanings, nothing more.

## Re-verifying

`build.sh` reports LaTeX errors only. To check the structure tree itself:

```bash
cd ../..   # the tool repo, where pyproject.toml lives
uv run python -c "
import pikepdf
p = pikepdf.open('examples/questionbank-demo/build/demo-questionbank-sol.pdf')
figs = [o for o in p.objects
        if isinstance(o, pikepdf.Dictionary) and str(o.get('/S','')) == '/Figure']
print(len(figs), 'figures')
for o in figs:
    print(' ', str(o.get('/K').get('/Type')), str(o.get('/Alt'))[:60])"
```

Expect nine figures, each with an `/MCR` kid and a plain-words `/Alt`.
