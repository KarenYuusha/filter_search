from __future__ import annotations

import json

from .models import ImportReport, ParseIssue


def _sorted_issues(report: ImportReport) -> tuple[ParseIssue, ...]:
    return tuple(
        sorted(
            report.issues,
            key=lambda issue: (
                issue.source_file,
                issue.skill_name or "",
                issue.code,
                issue.message,
            ),
        )
    )


def _issue_dict(issue: ParseIssue) -> dict[str, str | None]:
    return {
        "level": issue.level,
        "code": issue.code,
        "source_file": issue.source_file,
        "skill_name": issue.skill_name,
        "message": issue.message,
    }


def render_import_report(report: ImportReport) -> str:
    lines = [
        "Skill import report",
        f"Files discovered: {report.files_discovered}",
        f"Trees created: {report.trees_created}",
        f"Skill blocks discovered: {report.skill_blocks_discovered}",
        f"Skills created: {report.skills_created}",
        f"Errors: {len(report.errors)}",
        f"Warnings: {len(report.warnings)}",
        f"Manifest: {report.manifest_hash}",
    ]
    issues = _sorted_issues(report)
    if issues:
        lines.append("")
    for issue in issues:
        skill = f" [{issue.skill_name}]" if issue.skill_name else ""
        lines.append(
            f"{issue.level.upper()} {issue.code} {issue.source_file}{skill}: {issue.message}"
        )
    return "\n".join(lines)


def report_to_json(report: ImportReport) -> str:
    payload = {
        "files_discovered": report.files_discovered,
        "trees_created": report.trees_created,
        "skill_blocks_discovered": report.skill_blocks_discovered,
        "skills_created": report.skills_created,
        "errors": len(report.errors),
        "warnings": len(report.warnings),
        "manifest_hash": report.manifest_hash,
        "issues": [_issue_dict(issue) for issue in _sorted_issues(report)],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
