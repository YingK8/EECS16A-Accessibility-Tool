"""Run the static corpus sweep and say what changed.

    python -m tools.repair_lab.sweep            compare against the baseline
    python -m tools.repair_lab.sweep --write    record the current state

``--write`` is a deliberate, separate act. The baseline is the record of what
this pipeline can and cannot do; overwriting it automatically would let a
regression record itself as the new normal.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

from corpus_sweep import compare, read_baseline, sweep, write_baseline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="record this run as the baseline")
    parser.add_argument("--show", type=int, default=20, help="how many broken documents to list")
    args = parser.parse_args()

    results = sweep()
    broken = [item for item in results if not item.ok]
    print(f"{len(results)} documents, {len(broken)} with unresolved references")

    targets = collections.Counter(t for item in broken for t in item.unresolved)
    for target, count in targets.most_common(args.show):
        print(f"  {count:5d}  {target}")

    report = compare(results, read_baseline())
    for label in ("regressed", "fixed", "appeared", "vanished"):
        if report[label]:
            print(f"\n{label}: {len(report[label])}")
            for key in report[label][:args.show]:
                print(f"  {key}")

    if args.write:
        write_baseline(results)
        print(f"\nbaseline written: {len(results)} documents")
    return 1 if report["regressed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
