#!/usr/bin/env bash
# Fetch the PDFBox command-line jar, which `latexally formats` uses as its
# stand-in for Canvas Ally.
#
#   ./vendor/pdfbox/fetch.sh
#
# Not committed and not a submodule. Apache publishes pdfbox-app as a release
# artifact on Maven Central, so there is no checkout that yields the jar --
# building it from source means Maven plus the whole apache/pdfbox history, for
# a file this repository only ever reads text with. The alternative, committing
# 13.5 MB of binary, is permanent in every clone. So: fetch it, pin it, and let
# `latexally doctor` say when it is missing.

set -euo pipefail

VERSION="3.0.5"
SHA256="d076467fd02214ebc7b5b9d5b3b9ac0891ef768168114ed8a4811b5d16606285"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$HERE/pdfbox-app.jar"
URL="https://repo1.maven.org/maven2/org/apache/pdfbox/pdfbox-app/$VERSION/pdfbox-app-$VERSION.jar"

verify() {  # verify <path> -- true when it is the jar we pinned
  [ -f "$1" ] && [ "$(shasum -a 256 "$1" | cut -d' ' -f1)" = "$SHA256" ]
}

if verify "$TARGET"; then
  echo "pdfbox-app $VERSION already present"
  exit 0
fi

echo "fetching pdfbox-app $VERSION"
curl -fsSL --retry 3 -o "$TARGET.part" "$URL"

# Checked before the file is put in place, never after. A truncated download
# that lands at the real path is what the next run would then accept as cached.
if ! verify "$TARGET.part"; then
  rm -f "$TARGET.part"
  echo "checksum mismatch; refusing to install" >&2
  exit 1
fi

mv "$TARGET.part" "$TARGET"
echo "installed $TARGET"
