#!/usr/bin/env python3
r"""
figure_updater.py

Recursively scans .tex files in a specified directory and adds white background
styling to all TikZ axis tick label nodes for improved accessibility and clarity.

Features:
- Targets node labels in tikzpicture environments with directional placement
- Adds fill=white,inner sep=1pt to nodes with [left], [right], [above], [below]
- Skips nodes that already have fill= styles
- Backs up each modified file and logs all changes

Usage:
    python figure_updater.py /path/to/target/directory [--no-backup]

Example transformations:
    \node[left] {1};               => \node[left,fill=white,inner sep=1pt] {1};
    \node[right] {0.5};            => \node[right,fill=white,inner sep=1pt] {0.5};
    \node[left,fill=yellow] {x};   => (unchanged - already has fill=)
"""

import argparse
import os
import re
import shutil
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "log")
LOG_FILE = os.path.join(
    LOG_DIR,
    f"figure_updater_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
)

# Pattern to match node[ options ] where options contain directional keywords
# This captures the entire node[...] part so we can check and modify
NODE_PATTERN = re.compile(r"(\bnode\s*\[\s*([^\]]*)\s*\])", re.IGNORECASE)

# Direction keywords to look for
DIRECTION_KEYWORDS = (r"\b(left|right|above|below)\b",)


def backup_file(filepath):
    """Create a backup of the file with .bak extension."""
    backup_path = filepath + ".bak"
    shutil.copy2(filepath, backup_path)
    return backup_path


def should_add_background(options_str):
    """
    Check if we should add white background to this node.
    Returns True if:
    - Options contain a direction keyword (left, right, above, below)
    - AND options do not already contain fill=
    """
    # Check for direction keyword
    has_direction = any(
        re.search(keyword, options_str, re.IGNORECASE) for keyword in DIRECTION_KEYWORDS
    )

    if not has_direction:
        return False

    # Check if fill= already exists
    if re.search(r"\bfill\s*=", options_str, re.IGNORECASE):
        return False

    return True


def process_axis_labels(content):
    """
    Add white background and inner sep to axis label nodes.
    Returns (new_content, count_of_replacements).
    """
    changes = 0

    def replace_node(match):
        nonlocal changes
        full_match = match.group(1)  # Full "node[...]"
        options_str = match.group(2)  # Just the options inside brackets

        if should_add_background(options_str):
            # Add the white background styling
            new_options = options_str + ",fill=white,inner sep=1pt"
            new_match = f"node[{new_options}]"
            changes += 1
            return new_match

        return full_match  # Return unchanged

    new_content = NODE_PATTERN.sub(replace_node, content)

    return new_content, changes


def process_file(filepath, log, create_backup=True):
    """Process a single .tex file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        log.append(f"[ERROR] Could not decode {filepath}\n")
        return

    original_content = content

    # Process axis labels
    new_content, num_changes = process_axis_labels(content)

    if new_content != original_content:
        if create_backup:
            backup_path = backup_file(filepath)
            log.append(
                f"[MODIFIED] {filepath}\n"
                f"  Backup: {backup_path}\n"
                f"  Changes: Updated {num_changes} axis label node(s) with white background\n"
            )
        else:
            log.append(
                f"[MODIFIED] {filepath}\n"
                f"  Changes: Updated {num_changes} axis label node(s) with white background\n"
            )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
    else:
        log.append(f"[UNCHANGED] {filepath}\n")


def main(root_dir, create_backup=True, create_log=True):
    """Process all .tex files in root_dir."""
    os.makedirs(LOG_DIR, exist_ok=True)
    log = []

    file_count = 0
    modified_count = 0

    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.endswith(".tex") and not filename.endswith(".bak"):
                filepath = os.path.join(dirpath, filename)
                original_size = os.path.getsize(filepath)
                process_file(filepath, log, create_backup)
                file_count += 1
                if os.path.getsize(
                    filepath
                ) > original_size or original_size > os.path.getsize(filepath):
                    modified_count += 1

    if create_log:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(log)
        print(
            f"Figure axis label update complete. {file_count} files scanned. "
            f"Log saved to {LOG_FILE}"
        )
    else:
        print(f"Figure axis label update complete. {file_count} files scanned.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Add white background styling to TikZ axis label nodes."
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
