#!/usr/bin/env bash
# Rebuild the question-bank demonstration PDFs.
#
#   ./examples/questionbank-demo/build.sh
#
# Produces build/:
#   demo-questionbank-prob.pdf   student version, 6 described figures
#   demo-questionbank-sol.pdf    full solutions, 9 described figures
#
# The extra three are marked `solution-only: yes` in the worklogs, so their
# descriptions may disclose the answer; the student build never sees them.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OUT="$HERE/build"
mkdir -p "$OUT"

export TEXINPUTS="$REPO/tex:$HERE:"

# Three runs: tagpdf resolves marked-content ids across runs via the .aux.
build() {  # build <jobname> <class-option>
  echo "==> $1"
  for _ in 1 2 3; do
    pdflatex -interaction=nonstopmode -file-line-error \
             -output-directory="$OUT" -jobname="$1" \
             "\\PassOptionsToClass{$2}{latexally-assignment}\\input{demo-questionbank}" \
             > /dev/null 2>&1 || true
  done
  printf '    %s.pdf (%s bytes), %s LaTeX errors\n' "$1" \
    "$(wc -c < "$OUT/$1.pdf" | tr -d ' ')" \
    "$(grep -cE '^.*\.tex:[0-9]+:' "$OUT/$1.log" || true)"
}

build demo-questionbank-prob prob
build demo-questionbank-sol  sol

echo
echo "PDFs are in $OUT"
