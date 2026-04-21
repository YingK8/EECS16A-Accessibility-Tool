#!/usr/bin/env python3
r"""
assignment_macro_updater.py

Recursively scans a specified directory for LaTeX assignment documents (.tex files)
and applies semantic heading tagging and PDF accessibility features as per the EECS 16A Implementation Guide.

Features:
- Normalizes \qitem content to the braced form \qitem{...}
- Preserves style-owned semantics for \qns and \title
- Uses exact relative paths to inject \usepackage{.../accessibility_format}
- Safely injects package header on \documentclass definitions
- Injects \AccessibilitySetDocumentTitle{...} when missing
- Ensures \AccessibilityApplyDocumentMetadata runs at begin document
- Updates PDF tagging preamble: adds \RequirePackage{pdfmanagement-testphase} and
  \DocumentMetadata{lang=en,pdfstandard=ua-1} before \documentclass, and ensures
  \tagpdfsetup{activate-all,uncompress} after style packages
- Backs up each modified file (toggleable) and logs all changes

Usage:
    python assignment_macro_updater.py /path/to/target/directory [--no-backup]
"""

import argparse
import os
import re
import sys
from datetime import datetime

from common import (
    ensure_accessibility_package,
    backup_file,
    ensure_pdf_tagging_preamble,
    replace_first_matching_pattern,
)

from patterns import (
    SOL_PATTERNS,
    REQUIRED_RENEW,
    REQUIRED_NEW,
    REQUIRED_DEF,
)

QITEM_DEF_PATTERN = re.compile(
    r"^\s*\\newcommand\{\\qitem\}\{\\qpart\\item\}\s*$", re.MULTILINE
)

# Inline qitem usage is normalized to the braced form, capturing multi-line content.
# This only matches unbraced \qitem usage; braced \qitem{...} blocks are left alone.
# The capture stops at the next top-level solution marker so any nested list structure
# inside the question stays inside the question block.
# Ex: "\qitem Describe X \n \sol" -> "\qitem{Describe X \n}\n\n\sol"
INLINE_QITEM_PATTERN = re.compile(
    r"^([ \t]*)\\qitem(?![a-zA-Z])(?:[ \t]*\n)?[ \t]*(?!\{)((?:(?!\\qitem(?![a-zA-Z])|\\sol(?![a-zA-Z])|\\solution(?![a-zA-Z])|\\end\{qns\}|\\end\{qun\}).)*)",
    re.DOTALL | re.MULTILINE,
)

SOLUTION_SEPARATOR_PATTERN = re.compile(
    r"(?<!\n)\n[ \t]*(?=\\sol(?![a-zA-Z])|\\solution(?![a-zA-Z]))"
)

TITLE_DEF_PATTERN = re.compile(
    r"^\s*\\def\\title\{([^{}]+)\}\s*$",
    re.MULTILINE,
)
TITLE_CMD_PATTERN = re.compile(
    r"^\s*\\title\{([^{}]+)\}\s*$",
    re.MULTILINE,
)
ACCESSIBILITY_SET_TITLE_PATTERN = re.compile(
    r"^\s*\\AccessibilitySetDocumentTitle\{[^\n]*\}\s*$",
    re.MULTILINE,
)
ACCESSIBILITY_APPLY_METADATA_PATTERN = re.compile(
    r"^\s*\\AtBeginDocument\{\\AccessibilityApplyDocumentMetadata\}\s*$",
    re.MULTILINE,
)
BEGIN_DOCUMENT_PATTERN = re.compile(
    r"^\s*\\begin\{document\}\s*$",
    re.MULTILINE,
)
USEPACKAGE_PATTERN = re.compile(
    r"^\s*\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{[^}]+\}\s*$",
    re.MULTILINE,
)
INPUT_PATTERN = re.compile(
    r"^\s*\\input\{[^}]+\}\s*$",
    re.MULTILINE,
)
WRAPPER_PREAMBLE_INPUT_PATTERN = re.compile(
    r"^\s*\\input\{[^}]*preamble[^}]*\}\s*$", re.MULTILINE | re.IGNORECASE
)
WRAPPER_BODY_INPUT_PATTERN = re.compile(
    r"^\s*\\input\{body[^}]*\}\s*$", re.MULTILINE | re.IGNORECASE
)
WRAPPER_PDF_REQUIRE_PATTERN = re.compile(
    r"^\s*\\RequirePackage\{pdfmanagement-testphase\}\s*$", re.MULTILINE
)
WRAPPER_PDF_METADATA_PATTERN = re.compile(
    r"^\s*\\DocumentMetadata\{[^\n]*pdfstandard=ua-1[^\n]*\}\s*$", re.MULTILINE
)
WRAPPER_PDF_REQUIRE_LINE = r"\RequirePackage{pdfmanagement-testphase}"
WRAPPER_PDF_METADATA_LINE = (
    r"\DocumentMetadata{lang=en-US,pdfstandard=ua-1,pdfversion=2.0}"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
LOG_DIR = os.path.join(SCRIPT_DIR, "log")
LOG_FILE = os.path.join(
    LOG_DIR,
    f"assignment_macro_updater_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
)


def should_process_file(filepath: str) -> bool:
    return filepath.endswith(".tex")


def _infer_document_title(content: str, filepath: str) -> str:
    title_match = TITLE_DEF_PATTERN.search(content)
    if title_match:
        return title_match.group(1).strip()

    title_match = TITLE_CMD_PATTERN.search(content)
    if title_match:
        return title_match.group(1).strip()

    fallback = os.path.splitext(os.path.basename(filepath))[0]
    return fallback.replace("_", " ").strip() or "EECS 16A Assignment"


def ensure_accessibility_document_title(content: str, filepath: str):
    """Ensure title metadata is configured for screen readers."""
    original_content = content
    has_documentclass = (
        re.search(r"^\s*\\documentclass", content, re.MULTILINE) is not None
    )
    title_match_existing = ACCESSIBILITY_SET_TITLE_PATTERN.search(content)

    # For wrapper files (no \documentclass), ensure title line appears after preamble \input.
    if not has_documentclass:
        preamble_input_match = WRAPPER_PREAMBLE_INPUT_PATTERN.search(content)
        any_input_match = INPUT_PATTERN.search(content)
        anchor_match = preamble_input_match or any_input_match

        if title_match_existing and anchor_match:
            if title_match_existing.start() < anchor_match.end():
                title_line = title_match_existing.group(0).strip()
                content = ACCESSIBILITY_SET_TITLE_PATTERN.sub("", content, count=1)
                anchor_match = WRAPPER_PREAMBLE_INPUT_PATTERN.search(
                    content
                ) or INPUT_PATTERN.search(content)
                if anchor_match:
                    insert_pos = anchor_match.end()
                    content = (
                        content[:insert_pos] + "\n" + title_line + content[insert_pos:]
                    )
                else:
                    content = title_line + "\n" + content
            return content, (content != original_content)

        if not title_match_existing:
            title_value = _infer_document_title(content, filepath)
            set_title_line = f"\\AccessibilitySetDocumentTitle{{{title_value}}}"
            if anchor_match:
                insert_pos = anchor_match.end()
                content = (
                    content[:insert_pos] + "\n" + set_title_line + content[insert_pos:]
                )
            else:
                content = set_title_line + "\n" + content
            return content, (content != original_content)

        return content, False

    if not title_match_existing:
        title_value = _infer_document_title(content, filepath)
        set_title_line = f"\\AccessibilitySetDocumentTitle{{{title_value}}}"

        title_match = TITLE_DEF_PATTERN.search(content) or TITLE_CMD_PATTERN.search(
            content
        )
        if title_match:
            insert_pos = title_match.end()
            content = (
                content[:insert_pos] + "\n" + set_title_line + content[insert_pos:]
            )
        else:
            package_matches = list(USEPACKAGE_PATTERN.finditer(content))
            if package_matches:
                insert_pos = package_matches[-1].end()
                content = (
                    content[:insert_pos] + "\n" + set_title_line + content[insert_pos:]
                )
            else:
                docclass_match = re.search(
                    r"^\s*\\documentclass.*$", content, re.MULTILINE
                )
                if docclass_match:
                    insert_pos = docclass_match.end()
                    content = (
                        content[:insert_pos]
                        + "\n"
                        + set_title_line
                        + content[insert_pos:]
                    )
                else:
                    input_match = INPUT_PATTERN.search(content)
                    if input_match:
                        insert_pos = input_match.end()
                        content = (
                            content[:insert_pos]
                            + "\n"
                            + set_title_line
                            + content[insert_pos:]
                        )
                    else:
                        content = set_title_line + "\n" + content

    if has_documentclass and not ACCESSIBILITY_APPLY_METADATA_PATTERN.search(content):
        apply_line = "\\AtBeginDocument{\\AccessibilityApplyDocumentMetadata}"

        begin_doc_match = BEGIN_DOCUMENT_PATTERN.search(content)
        if begin_doc_match:
            insert_pos = begin_doc_match.start()
        else:
            set_title_match = ACCESSIBILITY_SET_TITLE_PATTERN.search(content)
            if set_title_match:
                insert_pos = set_title_match.end()
            else:
                package_matches = list(USEPACKAGE_PATTERN.finditer(content))
                if package_matches:
                    insert_pos = package_matches[-1].end()
                else:
                    input_match = INPUT_PATTERN.search(content)
                    if input_match:
                        insert_pos = input_match.start()
                    else:
                        docclass_match = re.search(
                            r"^\s*\\documentclass.*$", content, re.MULTILINE
                        )
                        insert_pos = docclass_match.end() if docclass_match else 0

        insertion = "\n" + apply_line + "\n"
        content = content[:insert_pos] + insertion + content[insert_pos:]

    return content, (content != original_content)


def ensure_wrapper_pdf_metadata(content: str):
    """Ensure wrapper .tex files (without documentclass) include PDF metadata lines."""
    original_content = content
    has_documentclass = (
        re.search(r"^\s*\\documentclass", content, re.MULTILINE) is not None
    )
    if has_documentclass:
        return content, False

    insertion_lines = []
    if not WRAPPER_PDF_REQUIRE_PATTERN.search(content):
        insertion_lines.append(WRAPPER_PDF_REQUIRE_LINE)
    if not WRAPPER_PDF_METADATA_PATTERN.search(content):
        insertion_lines.append(WRAPPER_PDF_METADATA_LINE)

    if insertion_lines:
        block = "\n".join(insertion_lines) + "\n"
        content = block + content.lstrip("\n")

    return content, (content != original_content)


def is_discussion_wrapper_file(content: str) -> bool:
    """Detect wrapper files that load a preamble/body via \input and no documentclass."""
    has_documentclass = (
        re.search(r"^\s*\\documentclass", content, re.MULTILINE) is not None
    )
    return (
        not has_documentclass
        and WRAPPER_PREAMBLE_INPUT_PATTERN.search(content) is not None
        and WRAPPER_BODY_INPUT_PATTERN.search(content) is not None
    )


def cleanup_nonwrapper_preamble_lines(content: str):
    """Remove preamble-only lines if they were accidentally added to include fragments."""
    original_content = content
    content = WRAPPER_PDF_REQUIRE_PATTERN.sub("", content)
    content = WRAPPER_PDF_METADATA_PATTERN.sub("", content)
    content = ACCESSIBILITY_SET_TITLE_PATTERN.sub("", content)
    # Collapse runs of blank lines introduced by removals.
    content = re.sub(r"\n{3,}", "\n\n", content)
    content = re.sub(r"^\n+", "", content)
    return content, (content != original_content)


def process_file(
    filepath: str,
    root_dir: str,
    log: list,
    create_backup: bool = True,
) -> None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        log.append(f"[ERROR] Could not decode {filepath}\n")
        return

    original_content = content
    changes = []
    has_documentclass = re.search(r"^\s*\\documentclass", content, re.MULTILINE)
    is_wrapper = is_discussion_wrapper_file(content)

    new_content = QITEM_DEF_PATTERN.sub(
        r"\\long\\def\\qitem#1{\\qpart\\item #1}", content
    )
    if new_content != content:
        content = new_content
        changes.append("Upgraded legacy \\qitem definition to long form")

    # 1. Normalize \qitem inline strings to braced syntax
    def qitem_repl(m):
        leading_ws = m.group(1) or ""
        content_body = m.group(2)
        # Remove leading/trailing whitespace and blank lines
        content_lines = content_body.splitlines()
        while content_lines and content_lines[0].strip() == "":
            content_lines.pop(0)
        while content_lines and content_lines[-1].strip() == "":
            content_lines.pop()
        # Indent each line by 4 spaces (for readability)
        indented_body = "\n".join(
            "    " + line if line.strip() != "" else "" for line in content_lines
        )
        indented_body = re.sub(r"[ \t\r\n]+\Z", "", indented_body)
        # Output as: [indent]\qitem{\n    question\n}\n\n
        return f"{leading_ws}\\qitem{{\n{indented_body}\n}}\n\n"

    new_content = INLINE_QITEM_PATTERN.sub(qitem_repl, content)
    if new_content != content:
        content = new_content
        changes.append(
            "Normalized inline \\qitem(s) to securely braced form up to next \\sol"
        )

    new_content = SOLUTION_SEPARATOR_PATTERN.sub("\n\n", content)
    if new_content != content:
        content = new_content
        changes.append("Ensured a blank line before \\sol/\\solution blocks")

    # 2. Ensure accessibility format is loaded with a relative path
    if has_documentclass:
        content, pkg_changed = ensure_accessibility_package(
            content,
            filepath=filepath,
            root_dir=root_dir,
            insert_after_documentclass=True,
        )
        if pkg_changed:
            changes.append(
                "Ensured accessibility_format loader is securely positioned with relative path"
            )

    new_content, n = replace_first_matching_pattern(
        content,
        SOL_PATTERNS,
        REQUIRED_RENEW["sol"],
        REQUIRED_NEW["sol"],
        REQUIRED_DEF["sol"],
    )
    if n:
        content = new_content
        changes.append("Updated \\sol macro to use AccessibilityHeadingFour (H4)")

    # 3. Ensure PDF tagging preamble is correct
    new_content, preamble_changed = ensure_pdf_tagging_preamble(content)
    if preamble_changed:
        content = new_content
        changes.append(
            "Updated PDF tagging preamble (pdfmanagement-testphase, DocumentMetadata, tagpdfsetup)"
        )

    # 3b. Wrapper files without \documentclass still need metadata lines.
    if is_wrapper:
        new_content, wrapper_meta_changed = ensure_wrapper_pdf_metadata(content)
        if wrapper_meta_changed:
            content = new_content
            changes.append(
                "Ensured wrapper file has pdfmanagement-testphase and DocumentMetadata lines"
            )

    # 4. Ensure document title metadata is configured for accessibility.
    if has_documentclass or is_wrapper:
        new_content, title_meta_changed = ensure_accessibility_document_title(
            content, filepath
        )
        if title_meta_changed:
            content = new_content
            changes.append(
                "Ensured AccessibilitySetDocumentTitle and begin-document metadata apply hook"
            )
    else:
        new_content, cleaned = cleanup_nonwrapper_preamble_lines(content)
        if cleaned:
            content = new_content
            changes.append(
                "Removed preamble-only metadata/title lines from include fragment"
            )

    # Save outputs
    if content != original_content:
        if create_backup:
            backup_path = backup_file(filepath)
            backup_msg = f"Backup: {backup_path}"
        else:
            backup_msg = "Backup: Disabled"

        log.append(f"[MODIFIED] {filepath}\n  {backup_msg}\n  Changes: {changes}\n")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

    else:
        log.append(f"[UNCHANGED] {filepath}\n")


def collect_target_files(root_dir: str) -> list[str]:
    """Collect .tex targets in subtree plus ancestor preamble*.tex files."""
    targets = set()

    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            if should_process_file(filepath) and not filepath.endswith(".bak"):
                targets.add(os.path.normpath(filepath))

    current = os.path.normpath(root_dir)
    repo_root_norm = os.path.normpath(REPO_ROOT)

    while True:
        if current.startswith(repo_root_norm):
            try:
                for name in os.listdir(current):
                    if (
                        name.startswith("preamble")
                        and name.endswith(".tex")
                        and not name.endswith(".bak")
                    ):
                        targets.add(os.path.join(current, name))
            except OSError:
                pass
        if current == repo_root_norm:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    return sorted(targets)


def main(root_dir, create_backup=True, create_log=True) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    log = []

    for filepath in collect_target_files(root_dir):
        process_file(filepath, root_dir, log, create_backup)

    if create_log:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(log)
        print(
            f"Assignment Update complete. {len(log)} files processed. Log saved to {LOG_FILE}"
        )
    else:
        print(f"Assignment Update complete. {len(log)} files processed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LaTeX assignment semantic heading tagger."
    )
    parser.add_argument("target_directory", help="/path/to/target/directory")
    parser.add_argument(
        "--no-backup", action="store_true", help="Disable generating .bak files"
    )
    parser.add_argument(
        "--no-log", action="store_true", help="Disable generating log file"
    )
    args = parser.parse_args()

    root_dir = os.path.abspath(args.target_directory)
    create_backup = not args.no_backup
    create_log = not args.no_log

    main(root_dir, create_backup, create_log)
