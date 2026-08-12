from __future__ import annotations

import argparse
from pathlib import Path

from toram_skills.importer import import_skill_corpus
from toram_skills.report import render_import_report, report_to_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the canonical Toram skill database")
    parser.add_argument("--source", type=Path, default=Path("raw_skills"))
    parser.add_argument("--database", type=Path, default=Path("coryn_data/database/skills.sqlite"))
    parser.add_argument("--json-report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = import_skill_corpus(args.source, args.database)
    print(render_import_report(report))
    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(report_to_json(report), encoding="utf-8")
    return 0 if report.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
