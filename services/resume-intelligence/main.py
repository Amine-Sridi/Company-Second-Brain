"""
Main entry point for Resume Intelligence Pipeline
"""

import argparse
import logging
from pathlib import Path

from pipeline.orchestrator import process_file, process_folder


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main():
    parser = argparse.ArgumentParser(description="Resume Intelligence Pipeline")
    parser.add_argument(
        "--input",
        type=str,
        default="./tests/sample_resumes",
        help="Path to resume file or directory of resumes",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="./output/candidates_output.xlsx",
        help="Output XLSX file path",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-file progress",
    )
    args = parser.parse_args()

    input_path = Path(args.input)

    print("Starting Resume Intelligence Pipeline...")
    print(f"Input:  {input_path}")
    print(f"Output: {args.output}")

    if input_path.is_file():
        rows = process_file(str(input_path))
        print(f"Done. Generated {len(rows)} row(s).")
    else:
        output_path = process_folder(
            folder=str(input_path),
            output=args.output,
            verbose=args.verbose,
        )
        print(f"\nDone. Output: {output_path}")


if __name__ == "__main__":
    main()
