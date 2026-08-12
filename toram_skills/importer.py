from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile

from .models import ImportReport
from .parsing import parse_skill_file
from .repository import SkillRepository
from .schema import create_schema, verify_schema
from .search_documents import build_search_documents, search_document_manifest_hash
from .source_inventory import discover_skill_sources, source_manifest_hash


def import_skill_corpus(raw_root: Path, database_path: Path) -> ImportReport:
    raw_root = Path(raw_root)
    database_path = Path(database_path)

    sources = discover_skill_sources(raw_root)
    manifest_hash = source_manifest_hash(sources)
    parsed_files = tuple(parse_skill_file(source) for source in sources)
    issues = tuple(issue for parsed in parsed_files for issue in parsed.issues)
    discovered_blocks = sum(parsed.discovered_skill_blocks for parsed in parsed_files)
    parsed_skills = sum(len(parsed.skills) for parsed in parsed_files)

    report = ImportReport(
        files_discovered=len(sources),
        trees_created=len(parsed_files),
        skill_blocks_discovered=discovered_blocks,
        skills_created=parsed_skills,
        manifest_hash=manifest_hash,
        issues=issues,
    )
    if not report.is_valid:
        return report

    documents_by_skill = {
        skill.id: build_search_documents(parsed.tree, skill)
        for parsed in parsed_files
        for skill in parsed.skills
    }
    search_documents = tuple(
        document
        for parsed in parsed_files
        for skill in parsed.skills
        for document in documents_by_skill[skill.id]
    )
    document_manifest_hash = search_document_manifest_hash(search_documents)

    database_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        dir=database_path.parent,
        suffix=".sqlite",
    )
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        bootstrap = sqlite3.connect(temp_path)
        try:
            create_schema(bootstrap)
            verify_schema(bootstrap)
            bootstrap.commit()
        finally:
            bootstrap.close()

        with SkillRepository(temp_path) as repo:
            repo.connection.execute("BEGIN")
            try:
                for parsed in parsed_files:
                    repo.insert_tree(parsed.tree)
                    for skill in parsed.skills:
                        repo.insert_skill(skill)
                        for document in documents_by_skill[skill.id]:
                            repo.connection.execute(
                                """
                                INSERT INTO skill_search_documents(
                                    id, skill_id, position, kind, label, text, text_hash
                                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    document.id,
                                    document.skill_id,
                                    document.position,
                                    document.kind,
                                    document.label,
                                    document.text,
                                    document.text_hash,
                                ),
                            )
                            repo.connection.execute(
                                """
                                INSERT INTO skill_fts(document_id, skill_id, name, tree_name, text)
                                VALUES (?, ?, ?, ?, ?)
                                """,
                                (
                                    document.id,
                                    skill.id,
                                    skill.name,
                                    parsed.tree.name,
                                    document.text,
                                ),
                            )

                repo.set_metadata("schema_version", "2")
                repo.set_metadata("source_manifest_hash", manifest_hash)
                repo.set_metadata("source_file_count", str(len(sources)))
                repo.set_metadata("search_document_manifest_hash", document_manifest_hash)

                verify_schema(repo.connection)
                if repo.count_trees() != len(parsed_files):
                    raise RuntimeError("Skill tree row count does not match parsed corpus")
                if repo.count_skills() != parsed_skills:
                    raise RuntimeError("Skill row count does not match parsed corpus")
                document_count = int(
                    repo.connection.execute("SELECT COUNT(*) FROM skill_search_documents").fetchone()[0]
                )
                fts_count = int(repo.connection.execute("SELECT COUNT(*) FROM skill_fts").fetchone()[0])
                if document_count != len(search_documents):
                    raise RuntimeError("Skill search document row count does not match generated documents")
                if fts_count != len(search_documents):
                    raise RuntimeError("Skill FTS row count does not match generated documents")
                repo.connection.execute("COMMIT")
            except Exception:
                repo.connection.execute("ROLLBACK")
                raise

        os.replace(temp_path, database_path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise

    return report
