"""Audit the alt-text pipeline across every document in the corpus.

    python -m tools.repair_lab.alt_audit            every assignment
    python -m tools.repair_lab.alt_audit --limit 50 the first 50

Answers the question the per-document checks cannot: does the description
pipeline actually reach every figure in the corpus, or only the ones that
happen to sit in an assignment's own folder.

Three things per assignment, none of which needs LaTeX:

figures found
    Resolved through the document's include graph, which is what a build sees.
    A folder-scoped scan finds a different, smaller and partly disjoint set --
    that mismatch is the defect this exists to measure.

locations usable
    Every entry's ``at:`` must name a file that exists and a line inside it.
    "Go exactly to the file location" is the whole job of that field, and a
    stale or empty one fails silently: the description still writes, it just
    cannot be found again.

descriptions recoverable
    The file must round-trip. A hand-typed colon used to make it invalid YAML
    and silently discard every description in it, so this reads each one back
    and compares.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from latexally.build import source_files_for
from latexally.catalog import build_catalog
from latexally.config import load_profile
from latexally.discover import discover_assignments
from latexally.scan import scan_corpus


def audit_assignment(profile, assignment) -> dict:
    """Figure coverage and location health for one assignment."""
    files = sorted(set(source_files_for(assignment, profile)))
    by_document = scan_corpus(profile, files=files)
    by_folder = scan_corpus(profile, assignment.path)

    document_ids = {reference.id for reference in by_document}
    folder_ids = {reference.id for reference in by_folder}

    root = profile.corpus.root.resolve()
    unusable = 0
    for reference in by_document:
        path = Path(reference.file)
        if not path.is_file() or reference.line < 1:
            unusable += 1

    return {
        "assignment": assignment.path,
        "files": len(files),
        "figures": len(document_ids),
        "folder_only": len(folder_ids - document_ids),
        "missed_by_folder_scan": len(document_ids - folder_ids),
        "unusable_locations": unusable,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="stop after N assignments")
    parser.add_argument("--scope", default=None)
    args = parser.parse_args()

    profile = load_profile("eecs16a")
    assignments = discover_assignments(profile, args.scope)
    if args.limit:
        assignments = assignments[: args.limit]

    totals = Counter()
    worst: list[dict] = []
    for index, assignment in enumerate(assignments, 1):
        try:
            row = audit_assignment(profile, assignment)
        except Exception as exc:  # a broken document must not stop the audit
            totals["errored"] += 1
            worst.append({"assignment": assignment.path, "error": str(exc)[:80]})
            continue
        for key in ("files", "figures", "folder_only", "missed_by_folder_scan",
                    "unusable_locations"):
            totals[key] += row[key]
        if row["missed_by_folder_scan"] or row["unusable_locations"]:
            worst.append(row)
        if index % 200 == 0:
            print(f"  ...{index}/{len(assignments)}", file=sys.stderr)

    print(f"{len(assignments)} assignments")
    print(f"  figures reachable from the documents : {totals['figures']}")
    print(f"  figures a folder scan would MISS     : {totals['missed_by_folder_scan']}")
    print(f"  figures a folder scan adds in error  : {totals['folder_only']}")
    print(f"  entries with an unusable location    : {totals['unusable_locations']}")
    print(f"  assignments that errored             : {totals['errored']}")

    if worst:
        print(f"\nworst {min(len(worst), 15)}:")
        for row in worst[:15]:
            print(f"  {row}")
    return 1 if totals["unusable_locations"] or totals["errored"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
