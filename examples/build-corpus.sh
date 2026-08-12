#!/usr/bin/env bash
# Build a batch of REAL course assignments, before and after, and report on each.
#
#   ./examples/build-corpus.sh                     # the default sample
#   ./examples/build-corpus.sh sp26/hw/3 sp26/hw/7 # specific assignments
#
# For every assignment this builds the untouched driver and an accessible
# variant that differs by exactly two lines, then reports LaTeX errors, tagpdf
# warnings, page count, bookmark count and the pixel difference between them.
#
# Source is compiled IN PLACE from the course tree and only the PDFs are written
# to examples/build/corpus/. Course material is deliberately not copied into
# this repository: the documents carry "All Rights Reserved. This may not be
# publicly shared without explicit permission" in their own footer.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
OUT="$HERE/build/corpus"
mkdir -p "$OUT"

CORPUS="${CORPUS:-$(cd "$REPO/.." && pwd)/questionBank}"
export TEXINPUTS="$REPO/tex:"

DEFAULT_SET=(sp26/hw/1 sp26/hw/5 sp26/hw/9 sp26/hw/13 sp26/dis/03A sp26/dis/09A)
ASSIGNMENTS=("${@:-}")
[ -z "${ASSIGNMENTS[0]:-}" ] && ASSIGNMENTS=("${DEFAULT_SET[@]}")

# Locate the solutions driver for an assignment directory.
find_driver() {
  local dir="$1" name
  name="$(basename "$dir")"
  for cand in "sol$name.tex" "sol.tex" "$name.tex"; do
    [ -f "$dir/$cand" ] && { echo "$cand"; return 0; }
  done
  return 1
}

printf '%-16s %-9s %7s %7s %6s %6s %9s\n' \
  ASSIGNMENT BUILD ERRORS WARNINGS PAGES BMARKS DIFF
printf '%.0s-' {1..72}; echo

for rel in "${ASSIGNMENTS[@]}"; do
  dir="$CORPUS/$rel"
  if [ ! -d "$dir" ]; then
    printf '%-16s %-9s %s\n' "$rel" "-" "no such directory"; continue
  fi
  driver="$(find_driver "$dir")" || {
    printf '%-16s %-9s %s\n' "$rel" "-" "no solutions driver found"; continue; }

  tag="$(echo "$rel" | tr '/' '-')"

  # The accessible driver: the original plus \DocumentMetadata as the very first
  # line and \usepackage{latexa11y-ee16} just before the body is pulled in.
  # Written to the OUTPUT directory; the course tree is never modified.
  {
    echo '\DocumentMetadata{lang=en-US,pdfversion=2.0,testphase={phase-III,math,table,graphic,firstaid}}'
    sed 's|^\(\\input{body[^}]*}\)$|\\usepackage{latexa11y-ee16}\n\1|' "$dir/$driver"
  } > "$OUT/$tag-accessible.tex"

  # Both builds run back to back: ee16.sty prints \timestamp in the running
  # header, so building them minutes apart would make every page differ.
  for variant in original accessible; do
    src="$driver"; [ "$variant" = accessible ] && src="$OUT/$tag-accessible.tex"
    ( cd "$dir" && for _ in 1 2 3; do
        pdflatex -interaction=nonstopmode -file-line-error \
                 -output-directory="$OUT" -jobname="$tag-$variant" \
                 "$src" > /dev/null 2>&1
      done )
  done

  for variant in original accessible; do
    job="$OUT/$tag-$variant"
    if [ ! -f "$job.pdf" ]; then
      printf '%-16s %-9s %7s\n' "$rel" "$variant" "BUILD FAILED"; continue
    fi
    errs=$(grep -cE '^\./[^:]+\.tex:[0-9]+:' "$job.log" 2>/dev/null) || true
    warns=$(grep -ci 'tagpdf warning' "$job.log" 2>/dev/null) || true
    read -r pages bmarks <<<"$(python3 - "$job.pdf" <<'PY'
import sys
try:
    import pymupdf
    d = pymupdf.open(sys.argv[1])
    def count(items):
        return sum(1 + count(i.children) for i in items)
    try:
        with __import__("pikepdf").open(sys.argv[1]) as p, p.open_outline() as o:
            n = count(o.root)
    except Exception:
        n = 0
    print(d.page_count, n)
except Exception:
    print("?", "?")
PY
)"
    diff="-"
    if [ "$variant" = accessible ] && [ -f "$OUT/$tag-original.pdf" ]; then
      diff=$(python3 - "$OUT/$tag-original.pdf" "$job.pdf" <<'PY'
import sys
try:
    import pymupdf
except ImportError:
    print("-"); sys.exit()
a, b = (pymupdf.open(p) for p in sys.argv[1:3])
if a.page_count != b.page_count:
    print(f"{a.page_count}v{b.page_count}pp"); sys.exit()
tot = bad = 0
for i in range(a.page_count):
    pa = a[i].get_pixmap(dpi=110, colorspace=pymupdf.csGRAY)
    pb = b[i].get_pixmap(dpi=110, colorspace=pymupdf.csGRAY)
    sa, sb = pa.samples, pb.samples
    if len(sa) != len(sb):
        print("size"); sys.exit()
    bad += sum(1 for k in range(len(sa)) if abs(sa[k]-sb[k]) > 96)
    tot += len(sa)
print(f"{100*bad/tot:.2f}%")
PY
)
    fi
    printf '%-16s %-9s %7s %7s %6s %6s %9s\n' "$rel" "$variant" "$errs" "$warns" "$pages" "$bmarks" "$diff"
  done
done

echo
echo "PDFs in $OUT"
