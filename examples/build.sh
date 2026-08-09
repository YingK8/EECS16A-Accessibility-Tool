#!/usr/bin/env bash
# Build the demonstration PDFs.
#
#   ./examples/build.sh
#
# Produces examples/build/:
#   demo-homework-sol.pdf     full solutions
#   demo-homework-prob.pdf    student version, same source
#   demo-legacy.pdf           legacy \qns/\qitem/\sol markup via the shim
#
# Open any of them and look at the bookmark pane.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
OUT="$HERE/build"
mkdir -p "$OUT"

export TEXINPUTS="$REPO/tex:$HERE:"

build() {  # build <jobname> <class-option> <source>
  local job="$1" option="$2" src="$3"
  echo "==> $job"
  # Three runs: tagpdf resolves the structure tree's marked-content ids across
  # runs via the .aux. After a single run every /MCID in the tree reads 1 while
  # the content stream numbers them 0..n, and the reading order is wrong.
  for _ in 1 2 3; do
    pdflatex -interaction=nonstopmode -file-line-error \
             -output-directory="$OUT" -jobname="$job" \
             "\\PassOptionsToClass{$option}{latexa11y-assignment}\\input{$src}" \
             > /dev/null 2>&1 || true
  done
  if [ -f "$OUT/$job.pdf" ]; then
    printf '    %s (%s bytes), %s LaTeX errors, %s tagpdf warnings\n' \
      "$job.pdf" "$(wc -c < "$OUT/$job.pdf" | tr -d ' ')" \
      "$(grep -cE '^.*\.tex:[0-9]+:' "$OUT/$job.log" || true)" \
      "$(grep -ci 'tagpdf warning' "$OUT/$job.log" || true)"
  else
    echo "    FAILED - see $OUT/$job.log"
  fi
}

build demo-homework-sol  sol  "$HERE/demo-homework.tex"
build demo-homework-prob prob "$HERE/demo-homework.tex"

echo "==> demo-legacy (legacy markup through the compatibility shim)"
for _ in 1 2 3; do
  pdflatex -interaction=nonstopmode -file-line-error \
           -output-directory="$OUT" -jobname=demo-legacy \
           "$REPO/tests/fixtures/golden_legacy.tex" > /dev/null 2>&1 || true
done
printf '    demo-legacy.pdf (%s bytes)\n' "$(wc -c < "$OUT/demo-legacy.pdf" | tr -d ' ')"

echo
echo "PDFs are in $OUT"
