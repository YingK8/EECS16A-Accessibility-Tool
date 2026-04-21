#!/usr/bin/env python3
"""Shared helpers for canonical accessibility_format package handling."""

import os
import re
import shutil


# Centralized backup utility for all updater scripts
def backup_file(filepath: str) -> str:
    backup_path = filepath + ".bak"
    shutil.copy2(filepath, backup_path)
    return backup_path


def replace_first_matching_pattern(
    content, patterns, required_renew, required_new, required_def
):
    """Replace the first matching macro definition based on command style.

    Expected pattern ordering:
      0 -> renewcommand form
      1 -> newcommand form
      2 -> def form
    """
    for idx, pattern in enumerate(patterns):
        if pattern.search(content):
            if idx == 0:
                replacement = required_renew
            elif idx == 1:
                replacement = required_new
            else:
                replacement = required_def
            return pattern.subn(lambda _m: replacement, content, count=1)
    return content, 0


# Matches any variation of the accessibility_format package loader, including relative paths
ACCESSIBILITY_ANY_PACKAGE_PATTERN = re.compile(
    r"\\(usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{(?:[^{}]+/)*accessibility_format\}",
    re.IGNORECASE,
)
ACCESSIBILITY_ANY_PACKAGE_LINE = re.compile(
    r"^(\s*)\\(usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{(?:[^{}]+/)*accessibility_format\}\s*$",
    re.MULTILINE | re.IGNORECASE,
)
# Safely match \documentclass declarations
DOCUMENTCLASS_LINE = re.compile(
    r"^\s*\\documentclass(?:\[[^\]]*\])?\s*\{[^}]+\}.*$",
    re.IGNORECASE | re.MULTILINE,
)


def _insert_after_documentclass(content, package_line):
    """Inserts the package line immediately after \documentclass."""
    match = DOCUMENTCLASS_LINE.search(content)
    if match:
        insert_pos = match.end()
        return content[:insert_pos] + "\n" + package_line + content[insert_pos:]
    return package_line + "\n" + content


def ensure_accessibility_package(
    content,
    filepath="",
    root_dir="",
    insert_after_documentclass=False,
    insert_at_top=False,
    use_require=False,
):
    """
    Ensure accessibility_format is loaded with a canonical package name.
    Complies with IMPLEMENTATION_GUIDE_LATEST.md rules.
    """
    filename = os.path.basename(filepath) if filepath else ""

    # Rule 1 Guardrail: No style file should ever load itself as a package.
    # If found in accessibility_format.sty, aggressively strip it out.
    if filename == "accessibility_format.sty":
        if ACCESSIBILITY_ANY_PACKAGE_LINE.search(content):
            new_content = ACCESSIBILITY_ANY_PACKAGE_LINE.sub("", content)
            return new_content, True
        return content, False

    # TEXINPUTS-based workflow: always use plain package name
    package_spec = "accessibility_format"
    command = "\\RequirePackage" if use_require else "\\usepackage"
    canonical_line = f"{command}{{{package_spec}}}"
    original_content = content

    # Normalization: Update any existing, messy imports to the canonical relative line
    if ACCESSIBILITY_ANY_PACKAGE_LINE.search(content):
        content = ACCESSIBILITY_ANY_PACKAGE_LINE.sub(
            lambda match: f"{match.group(1)}{canonical_line}", content
        )
    elif ACCESSIBILITY_ANY_PACKAGE_PATTERN.search(content):
        content = ACCESSIBILITY_ANY_PACKAGE_PATTERN.sub(canonical_line, content)

    # Insertion Logic:
    if not ACCESSIBILITY_ANY_PACKAGE_PATTERN.search(original_content):
        if insert_after_documentclass:
            content = _insert_after_documentclass(content, canonical_line)
        elif insert_at_top:
            # Safely inject the load statement into .sty files.
            # Place after \ProvidesPackage or \NeedsTeXFormat if they exist, otherwise at the top.
            match_iter = list(
                re.finditer(
                    r"^\s*\\(ProvidesPackage|NeedsTeXFormat).*?$", content, re.MULTILINE
                )
            )
            if match_iter:
                insert_pos = match_iter[-1].end()
                content = (
                    content[:insert_pos] + "\n" + canonical_line + content[insert_pos:]
                )
            else:
                # If no declarative package tags, skip initial header comments then insert
                insert_pos = 0
                for line in content.splitlines(True):
                    if line.strip().startswith("%") or line.strip() == "":
                        insert_pos += len(line)
                    else:
                        break
                content = (
                    content[:insert_pos] + canonical_line + "\n" + content[insert_pos:]
                )

    return content, (content != original_content)


# PDF Tagging Preamble Patterns
PREAMBLE_REQUIRE = r"\RequirePackage{pdfmanagement-testphase}"
PREAMBLE_METADATA = r"\DocumentMetadata{lang=en-US,pdfstandard=ua-1,pdfversion=2.0}"
TAGPDF_SETUP = r"\tagpdfsetup{activate-all,uncompress}"


def create_preamble_metadata(lang="en-US", pdfstandard="ua-1", pdfversion="2.0"):
    return f"\DocumentMetadata{{lang={lang},pdfstandard={pdfstandard},pdfversion={pdfversion}}}"


# Pattern to find tagpdfsetup if it already exists
TAGPDF_SETUP_PATTERN = re.compile(
    r"^\s*\\tagpdfsetup\{[^\}]*\}\s*$",
    re.MULTILINE | re.IGNORECASE,
)

PREAMBLE_REQUIRE_LINE = re.compile(
    r"^\s*\\RequirePackage\{pdfmanagement-testphase\}\s*$",
    re.MULTILINE,
)
PREAMBLE_METADATA_LINE = re.compile(
    r"^\s*\\DocumentMetadata\{lang=en-US,pdfstandard=ua-1,pdfversion=2.0\}\s*$",
    re.MULTILINE,
)
TAGPDF_PACKAGE_ANY = re.compile(
    r"\\(?:RequirePackage|usepackage)(?:\[[^\]]*\])?\{tagpdf\}",
    re.IGNORECASE,
)

# Pattern to find main style package includes (sp26, ee16, etc.)
STYLE_PACKAGE_PATTERN = re.compile(
    r"^\s*\\usepackage\{[\.\./]*(?:sp26|ee16|accessibility_format|timestamp|markup)\}\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def ensure_pdf_tagging_preamble(content):
    """
    Update LaTeX preamble with correct PDF accessibility tagging sequence.

    Returns: (updated_content, was_changed)
    """
    original_content = content

    # Step 1: Find \documentclass and insert only missing required lines.
    docclass_match = DOCUMENTCLASS_LINE.search(content)
    if not docclass_match:
        # No documentclass found, leave unchanged
        return content, False

    docclass_pos = docclass_match.start()
    prefix = content[:docclass_pos]

    insertion_lines = []
    if not PREAMBLE_REQUIRE_LINE.search(prefix):
        insertion_lines.append(PREAMBLE_REQUIRE)
    if not PREAMBLE_METADATA_LINE.search(prefix):
        insertion_lines.append(PREAMBLE_METADATA)
    if insertion_lines:
        content = (
            content[:docclass_pos]
            + "\n".join(insertion_lines)
            + "\n"
            + content[docclass_pos:]
        )

    # Recompute \documentclass match after possible insertion above.
    docclass_match = DOCUMENTCLASS_LINE.search(content)
    if not docclass_match:
        return content, (content != original_content)

    # Step 2: Ensure tagpdf loader exists; if missing, add after \documentclass.
    if not TAGPDF_PACKAGE_ANY.search(content):
        docclass_end = docclass_match.end()
        content = (
            content[:docclass_end]
            + "\n\\RequirePackage{tagpdf}"
            + content[docclass_end:]
        )

    # Step 3: Find last style package include and add tagpdfsetup after it
    last_style_match = None
    for match in STYLE_PACKAGE_PATTERN.finditer(content):
        last_style_match = match

    if last_style_match:
        # Insert tagpdfsetup after the last style package
        insert_pos = last_style_match.end()
        # Skip any whitespace/newlines on the line
        while insert_pos < len(content) and content[insert_pos] in " \t":
            insert_pos += 1
        if insert_pos < len(content) and content[insert_pos] == "\n":
            insert_pos += 1

        # Only add tagpdfsetup if missing
        if not TAGPDF_SETUP_PATTERN.search(content):
            # Add tagpdfsetup on its own line
            content = content[:insert_pos] + f"{TAGPDF_SETUP}\n" + content[insert_pos:]

    return content, (content != original_content)
