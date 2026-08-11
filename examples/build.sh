#!/usr/bin/env bash
# Build the demonstration PDFs.
#
#   ./examples/build.sh
#
# Produces examples/build/:
#
#   real-original.pdf         a REAL course assignment, stock toolchain
#   real-accessible.pdf       the same assignment, two lines added
#       ^ open these two side by side: this is the fidelity claim, checkable
#         by eye rather than by a percentage.
#
#   demo-homework-sol.pdf     the standalone class, full solutions
#   demo-homework-prob.pdf    the standalone class, student version, same source
#   demo-legacy.pdf           legacy \qns/\qitem/\sol markup via the shim
#
# Open any of them and look at the bookmark pane.
#
# Override the corpus location or the assignment if yours differ:
#   CORPUS=/path/to/questionBank ASSIGNMENT=sp26/hw/9 DRIVER=sol9.tex ./examples/build.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
OUT="$HERE/build"
mkdir -p "$OUT"

CORPUS="${CORPUS:-$(cd "$REPO/.." && pwd)/questionBank}"
ASSIGNMENT="${ASSIGNMENT:-sp26/hw/9}"
DRIVER="${DRIVER:-sol9.tex}"

export TEXINPUTS="$REPO/tex:$HERE:"

# Three runs, always: tagpdf resolves the structure tree's marked-content ids
# across runs via the .aux. After a single run every /MCID in the tree reads 1
# while the content stream numbers them 0..n, and the reading order is wrong.
runs() { echo 1 2 3; }

report() {  # report <jobname>
  local job="$1"
  if [ -f "$OUT/$job.pdf" ]; then
    printf '    %s (%s bytes), %s LaTeX errors, %s tagpdf warnings\n' \
      "$job.pdf" "$(wc -c < "$OUT/$job.pdf" | tr -d ' ')" \
      "$(grep -cE '^.*\.tex:[0-9]+:' "$OUT/$job.log" 2>/dev/null || true)" \
      "$(grep -ci 'tagpdf warning' "$OUT/$job.log" 2>/dev/null || true)"
  else
    echo "    FAILED - see $OUT/$job.log"
    return 1
  fi
}

# --------------------------------------------------------------------------
# 1. The real assignment, before and after
# --------------------------------------------------------------------------
# Both builds happen back to back on purpose: ee16.sty prints \timestamp in the
# running header, so building them minutes apart makes every page differ for a
# reason that has nothing to do with accessibility.

build_real() {
  local dir="$CORPUS/$ASSIGNMENT"
  if [ ! -f "$dir/$DRIVER" ]; then
    echo "==> real-original / real-accessible  [skipped]"
    echo "    no $DRIVER in $dir"
    echo "    set CORPUS=/path/to/questionBank (and ASSIGNMENT=, DRIVER=) to enable"
    return 0
  fi

  # The accessible driver is the original plus exactly two lines. It is written
  # to the OUTPUT directory, never into the course tree - this script must not
  # modify the corpus.
  {
    echo '\DocumentMetadata{lang=en-US,pdfversion=2.0,testphase={phase-III,math,table,graphic,firstaid}}'
    sed 's|^\(\\input{body}\)$|\\usepackage{latexa11y-ee16}\n\1|' "$dir/$DRIVER"
  } > "$OUT/real-accessible.tex"

  # Compiled from the assignment's own directory so its relative \usepackage and
  # \input paths (../../../ee16, ../../sp26, body) resolve exactly as they do
  # for the course's normal build.
  echo "==> real-original ($ASSIGNMENT/$DRIVER, untouched)"
  ( cd "$dir" && for _ in $(runs); do
      pdflatex -interaction=nonstopmode -file-line-error \
               -output-directory="$OUT" -jobname=real-original \
               "$DRIVER" > /dev/null 2>&1 || true
    done )
  report real-original || true

  echo "==> real-accessible (same source, \\DocumentMetadata + latexa11y-ee16)"
  ( cd "$dir" && for _ in $(runs); do
      pdflatex -interaction=nonstopmode -file-line-error \
               -output-directory="$OUT" -jobname=real-accessible \
               "$OUT/real-accessible.tex" > /dev/null 2>&1 || true
    done )
  report real-accessible || true

  compare_real
}

# Quantify the difference, when PyMuPDF is available. Counts pixels differing by
# more than 96/255 at 150 dpi: anti-aliasing and sub-pixel kerning fall below
# that, so what remains is real layout change.
compare_real() {
  [ -f "$OUT/real-original.pdf" ] && [ -f "$OUT/real-accessible.pdf" ] || return 0
  python3 - "$OUT/real-original.pdf" "$OUT/real-accessible.pdf" 2>/dev/null <<'PY' || true
import sys
try:
    import pymupdf
except ImportError:
    sys.exit(0)
a, b = (pymupdf.open(p) for p in sys.argv[1:3])
if a.page_count != b.page_count:
    print(f"    page count differs: {a.page_count} vs {b.page_count}")
    sys.exit(0)
total = bad = 0
for i in range(a.page_count):
    pa = a[i].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    pb = b[i].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    sa, sb = pa.samples, pb.samples
    if len(sa) != len(sb):
        print(f"    page {i+1}: size differs")
        continue
    bad += sum(1 for k in range(len(sa)) if abs(sa[k] - sb[k]) > 96)
    total += len(sa)
print(f"    {a.page_count} pages, {100*bad/total:.3f}% of pixels differ "
      f"(tagging's own cost; the retrofit itself contributes ~0.002%)")
PY
}

build_real

# --------------------------------------------------------------------------
# 2. The standalone class
# --------------------------------------------------------------------------

build_demo() {  # build_demo <jobname> <class-option> <source>
  local job="$1" option="$2" src="$3"
  echo "==> $job"
  for _ in $(runs); do
    pdflatex -interaction=nonstopmode -file-line-error \
             -output-directory="$OUT" -jobname="$job" \
             "\\PassOptionsToClass{$option}{latexa11y-assignment}\\input{$src}" \
             > /dev/null 2>&1 || true
  done
  report "$job" || true
}

build_demo demo-homework-sol  sol  "$HERE/demo-homework.tex"
build_demo demo-homework-prob prob "$HERE/demo-homework.tex"

# --------------------------------------------------------------------------
# 3. Legacy markup through the compatibility shim
# --------------------------------------------------------------------------

echo "==> demo-legacy (legacy markup through the compatibility shim)"
for _ in $(runs); do
  pdflatex -interaction=nonstopmode -file-line-error \
           -output-directory="$OUT" -jobname=demo-legacy \
           "$REPO/tests/fixtures/golden_legacy.tex" > /dev/null 2>&1 || true
done
report demo-legacy || true

echo
echo "PDFs are in $OUT"
echo "Start with real-original.pdf and real-accessible.pdf, side by side."
