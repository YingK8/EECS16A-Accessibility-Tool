import argparse
import os
import re
import sys
from datetime import datetime
import accessibility_tool

if __name__ == "__main__":

    print(
        """
        EECS 16A Accessibility Tool
        -----------------------------------
        This tool reformats LaTeX question bank files to help meet WCAG 2.1 accessibility standards.
        It can achieve a 100% score on the bCourses Ally Checker using EECS 16A Discussion and Homework PDFs.

        What this tool currently does:
        - Ensures proper PDF tagging for:
            • 1.3.1 Info and Relationships (semantic headings: title→H1, \qitem→H2, etc.)
            • 2.4.2 Page Titled (A): Sets descriptive PDF document title metadata

        Planned future features:
        - 1.1.1 Non-text Content (A): Add alt-text for images and non-text elements
        - 1.3.4 Orientation: Ensure content is not restricted to a single orientation
        - Contrast (AA): Validate sufficient contrast between text and background colors

        Additional goal: Enable students to use Ally to generate “Alternative Formats” (Tagged PDF, HTML, Braille, ePub, MP3, etc.) for course files and bCourses Pages.

        Note: This tool is a work in progress and currently focuses on PDF tagging.
        """
    )

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

    accessibility_tool.main(root_dir, create_backup, create_log)
