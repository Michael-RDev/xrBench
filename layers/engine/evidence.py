"""Inspect a returned headset report on a development computer, without a device or DB."""

import argparse
import copy
import json
from pathlib import Path

from layers.engine.reports import digest, summarize, validate, save


def inspect_report(path):
    original = json.loads(Path(path).read_text())
    report = summarize(validate(copy.deepcopy(original)))
    return dict(
        run_id=report["run_id"],
        source_sha256=digest(original),
        device=report["device"],
        configuration=report["configuration"],
        kernel_build=report.get("kernel_build"),
        kernel_comparison=report.get("kernel_comparison"),
        results=[
            {
                k: row.get(k)
                for k in (
                    "case",
                    "variant",
                    "arm",
                    "pair_index",
                    "status",
                    "correctness",
                    "timing_valid",
                    "timings",
                    "dispatch_summary",
                )
            }
            for row in report["results"]
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Save a separate analysis JSON; never modifies the source report",
    )
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.report.resolve():
        parser.error("Output must differ from the original report")
    result = inspect_report(args.report)
    if args.output:
        save(args.output, result)
    else:
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
