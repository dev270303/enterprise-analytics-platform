"""ETL command line.

  python -m app.etl run  data/orders.csv data/order_items.csv --commit
  python -m app.etl run  sample_batches/latest/*.csv            (dry run: validate + report only)
  python -m app.etl generate --out sample_batches/latest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.etl import generate as gen
from app.etl.pipeline import run_batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.etl", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="validate and load CSV files")
    run.add_argument("files", nargs="+")
    run.add_argument("--commit", action="store_true", help="write to the database (default: dry run)")
    run.add_argument("--source", default="cli")
    run.add_argument("--json", action="store_true", help="print the full JSON report")
    g = sub.add_parser("generate", help="generate a messy sample batch")
    g.add_argument("--out", default="sample_batches/latest")
    g.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)

    if args.cmd == "generate":
        print(json.dumps(gen.generate(args.out, args.seed), indent=2))
        return 0

    files = [(Path(f).name, Path(f).read_bytes(), None) for f in args.files]
    results = run_batch(files, mode="COMMIT" if args.commit else "DRY_RUN", source=args.source, triggered_by="cli")
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2, default=str))
    else:
        print(f"{'file':<18} {'entity':<12} {'status':<8} {'in':>6} {'loaded':>7} {'rejected':>9} {'dupes':>6} {'flagged':>8} {'quality':>8}")
        for r in results:
            print(f"{r.file_name:<18} {str(r.entity):<12} {r.status:<8} {r.rows_in:>6} {r.rows_loaded:>7} "
                  f"{r.rows_rejected:>9} {r.rows_duplicate:>6} {r.rows_flagged:>8} {str(r.quality_score):>8}")
            for s in r.to_dict()["issue_summary"][:8]:
                print(f"    {s['severity']:<7} {s['rule']:<20} {str(s['column']):<16} x{s['count']}")
    return 0 if all(r.status != "FAILED" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
