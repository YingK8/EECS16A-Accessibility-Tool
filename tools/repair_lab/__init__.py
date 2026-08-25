"""The iteration harness. Not part of the package, and not importable by it.

`src/latexally/` is the deterministic repair pipeline: given a corpus it makes
the same choices every time, with no model in the loop and no network. This
directory is how that pipeline gets *improved* -- run the corpus, look at what
still fails, propose a rule, keep it only if the whole corpus agrees.

The wall between the two is deliberate and enforced by
``tests/test_no_ai_in_production.py``:

* nothing under ``src/`` may import anything from here;
* this package is excluded from the wheel;
* no model-provider dependency appears in ``[project.dependencies]``.

What a proposal may be
----------------------
A **rule**: an ordering, a matching heuristic, a search path -- something that
answers a whole class and can be stated in a docstring. Never a per-file patch.
A per-file patch is exactly the non-generalisable thing the pipeline exists to
avoid, and 1,500 of them is not a fix, it is a fork of the corpus.

The loop
--------
1. ``python -m tools.repair_lab.sweep``            what still fails, statically
2. ``python -m tools.repair_lab.compile --count N`` what still fails, for real
3. read the buckets; write ONE rule in ``src/latexally/``
4. re-run 1 and 2; keep it only if nothing regressed
5. ``python -m tools.repair_lab.sweep --write``    record the new baseline
"""
