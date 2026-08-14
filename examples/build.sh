#!/usr/bin/env bash
# Build the demonstration PDFs.
#
#   ./examples/build.sh
#
# Produces examples/build/:
#
#   demo-homework-sol.pdf     the standalone class, full solutions
#   demo-homework-prob.pdf    the standalone class, student version, same source
#   demo-legacy.pdf           legacy \qns/\qitem/\sol markup via the shim
#
# Open any of them and look at the bookmark pane.
#
# This script builds ONLY the self-contained demonstrations that ship with the
# repository. Converting real course material is `latexa11y build`, which lives
# in Python where it can be tested:
#
#   latexa11y -p eecs16a run                      pick scope and options
#   latexa11y -p eecs16a build sp26/hw/9 --write  or say it directly
#
# An earlier version of this script also converted a real assignment, using a
# `sed` expression that was the only definition anywhere of what conversion
# means. Four defects were hiding in it -- see src/latexa11y/build/__init__.py.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
OUT="$HERE/build"
mkdir -p "$OUT"

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

echo "==> demo-legacy (legacy markup through the compatibility shim)"
for _ in $(runs); do
  pdflatex -interaction=nonstopmode -file-line-error \
           -output-directory="$OUT" -jobname=demo-legacy \
           "$REPO/tests/fixtures/golden_legacy.tex" > /dev/null 2>&1 || true
done
report demo-legacy || true

echo
echo "PDFs are in $OUT"
echo
echo "To convert your own material, with a before/after fidelity number:"
echo "    latexa11y -p eecs16a run"
