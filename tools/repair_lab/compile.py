"""Build a reproducible sample of the corpus and bucket the outcomes.

    python -m tools.repair_lab.compile --count 40 --seed 16

Three buckets, because "failed" hides the only distinction that matters when
you are deciding what to work on next:

    ok           a PDF came out with a clean log
    needs-alt    it built; a figure or formula is still waiting on a human
    unspeakable  it built cleanly and a reader would hear silence or gibberish
    inherited    the unconverted source fails the same way -- not ours
    regression   converting it broke it -- ours, and the only list to work from

Slow by nature (one pdflatex run per document, twice, plus the baseline), so it
is a tool you run deliberately, not a test that runs on every edit.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

from corpus_compile import compile_sample  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=16)
    parser.add_argument("--out", type=Path, default=Path(".lab-out/compile"))
    args = parser.parse_args()

    results = compile_sample(count=args.count, seed=args.seed, out=args.out)
    tally = collections.Counter(item.verdict for item in results)
    print(f"{len(results)} documents  " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))

    for verdict in ("regression", "unspeakable", "unexplained", "error",
                    "inherited", "needs-alt"):
        rows = [item for item in results if item.verdict == verdict]
        if not rows:
            continue
        print(f"\n{verdict} ({len(rows)})")
        for item in rows:
            first = (item.errors or [""])[0][-110:]
            print(f"  {item.assignment} {item.variant}: {first}")
    return 1 if tally["regression"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
