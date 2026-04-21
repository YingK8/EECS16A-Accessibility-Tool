#!/usr/bin/env python3
r"""
style_macro_updater.py

Scans and updates .sty files in a specified directory to ensure macro definitions match the accessibility requirements in the EECS 16A Implementation Guide.

- Ensures \title stores title text for safe reuse and metadata
- Ensures \@maketitle renders an H1 from stored title text
- Ensures \qns uses \AccessibilityHeadingTwo
- Ensures \q uses \AccessibilityHeadingThree
- Ensures \sol is not altered
- Ensures \AccessibilityHeading* macros are defined as required
- Backs up each modified file
- Logs all changes
- Idempotently normalizes accessibility format loader imports, injecting it securely if it is missing.

Usage:
    python style_macro_updater.py /path/to/target/directory
"""

import argparse
import os
import sys
import re
from datetime import datetime

from patterns import (
    ACCESSIBLE_TITLE_STORAGE_PATTERN,
    TITLE_H1_RENDER_PATTERNS,
    TITLE_RENDER_CONTEXT_PATTERNS,
    QNS_PATTERNS,
    Q_PATTERNS,
    REQUIRED_ACCESSIBLE_TITLE_STORAGE,
    REQUIRED_TITLE_SETTER,
    REQUIRED_TITLE_H1_RENDER,
    REQUIRED_RENEW,
    REQUIRED_NEW,
    REQUIRED_DEF,
    TITLE_PATTERNS,
    SOL_PATTERNS,
    ACC_HEADINGS,
)

from common import (
    ensure_accessibility_package,
    backup_file,
    replace_first_matching_pattern,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Assuming the "tools" directory is at the repo root
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
LOG_DIR = os.path.join(SCRIPT_DIR, "log")
LOG_FILE = os.path.join(
    LOG_DIR,
    f"style_macro_updater_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
)


def update_macros_in_content(content, filepath):
    filename = os.path.basename(filepath)
    changes = []

    # Ensure title storage macro exists for \title setter + render path.
    title_match = re.search(
        r"^\s*\\(?:renewcommand|newcommand)\{\\title\}\[1\]\{.*\}\s*$|^\s*\\def\\title#1\{.*\}\s*$",
        content,
        re.MULTILINE,
    )
    has_title_render_context = any(
        pattern.search(content) for pattern in TITLE_RENDER_CONTEXT_PATTERNS
    )
    should_manage_accessible_title = bool(title_match or has_title_render_context)
    if should_manage_accessible_title and not ACCESSIBLE_TITLE_STORAGE_PATTERN.search(
        content
    ):
        insert_at = title_match.start() if title_match else 0
        storage_block = REQUIRED_ACCESSIBLE_TITLE_STORAGE + "\n"
        content = content[:insert_at] + storage_block + content[insert_at:]
        changes.append(f"Inserted \\accessibletitle storage macro in {filename}")

    # Ensure a title setter exists so \accessibletitle is actually populated.
    if should_manage_accessible_title and not title_match:
        storage_match = ACCESSIBLE_TITLE_STORAGE_PATTERN.search(content)
        if storage_match:
            insert_at = storage_match.end()
            setter_block = "\n" + REQUIRED_TITLE_SETTER + "\n"
            content = content[:insert_at] + setter_block + content[insert_at:]
        else:
            content = REQUIRED_TITLE_SETTER + "\n" + content
        changes.append(f"Inserted \\title setter in {filename}")

    # Ensure \@maketitle title line uses H1 wrapper around stored title text.
    for pattern in TITLE_H1_RENDER_PATTERNS:
        content, n_render = pattern.subn(
            lambda _m: REQUIRED_TITLE_H1_RENDER, content, count=1
        )
        if n_render:
            changes.append(f"Normalized title H1 render in {filename}")
            break

    # Ensure footer title references use stored title text (\title expects an argument).
    footer_changes = 0
    content, n_odd = re.subn(
        r"(\\def\\oddfoottext\{[\s\S]*?)\\title",
        r"\1\\accessibletitle",
        content,
        count=1,
    )
    footer_changes += n_odd
    content, n_even = re.subn(
        r"(\\def\\evenfoottext\{[\s\S]*?)\\title",
        r"\1\\accessibletitle",
        content,
        count=1,
    )
    footer_changes += n_even
    if footer_changes:
        changes.append(f"Updated footer title references in {filename}")

    # Update \title
    content, n = replace_first_matching_pattern(
        content,
        TITLE_PATTERNS,
        REQUIRED_RENEW["title"],
        REQUIRED_NEW["title"],
        REQUIRED_DEF["title"],
    )
    if n:
        changes.append(f"Updated \\title in {filename}")

    # Update \sol
    content, n = replace_first_matching_pattern(
        content,
        SOL_PATTERNS,
        REQUIRED_RENEW["sol"],
        REQUIRED_NEW["sol"],
        REQUIRED_DEF["sol"],
    )
    if n:
        changes.append(f"Updated \\sol in {filename}")

    # Update \qns
    content, n = replace_first_matching_pattern(
        content,
        QNS_PATTERNS,
        REQUIRED_RENEW["qns"],
        REQUIRED_NEW["qns"],
        REQUIRED_DEF["qns"],
    )
    if n:
        changes.append(f"Updated \\qns in {filename}")

    # Update \q
    content, n = replace_first_matching_pattern(
        content,
        Q_PATTERNS,
        REQUIRED_RENEW["q"],
        REQUIRED_NEW["q"],
        REQUIRED_DEF["q"],
    )
    if n:
        changes.append(f"Updated \\q in {filename}")

    # Only define \AccessibilityHeading* macros natively in accessibility_format.sty
    if filename == "accessibility_format.sty":
        for name, pattern, required in ACC_HEADINGS:
            if not re.search(pattern, content):
                content = required + "\n" + content
                changes.append(
                    f"Inserted fallback \\AccessibilityHeading{name} in {filename}"
                )

    # Normalizes existing loaders and safely injects \RequirePackage into .sty files missing it
    content, pkg_changed = ensure_accessibility_package(
        content,
        filepath=filepath,
        root_dir=REPO_ROOT,
        insert_after_documentclass=False,
        insert_at_top=True,  # Enable top-level injection for .sty files
        use_require=True,
    )
    if pkg_changed:
        changes.append(
            f"Normalized/Injected accessibility package loader in {filename}"
        )

    return content, changes


def process_file(filepath, log, create_backup=True):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    updated_content, changes = update_macros_in_content(content, filepath)

    if changes:
        if create_backup:
            backup_path = backup_file(filepath)
            backup_msg = f"Backup: {backup_path}"
        else:
            backup_msg = "Backup: Disabled"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated_content)
        log.append(
            f"[MODIFIED] {filepath}\n  Backup: {backup_msg}\n  Changes: {changes}\n"
        )

    else:
        log.append(f"[UNCHANGED] {filepath}\n")


def main(root_dir, create_backup=True, create_log=True) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    log = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        for filename in filenames:
            # Skip any backups inherently created by script
            if filename.endswith(".sty") and not filename.endswith(".bak"):
                filepath = os.path.join(dirpath, filename)
                process_file(filepath, log, create_backup)

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
