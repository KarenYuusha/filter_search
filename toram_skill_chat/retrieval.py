from __future__ import annotations

from collections.abc import Callable

from toram_skill_search.runtime import DEFAULT_SEMANTIC_RUNTIME
from toram_skills.hybrid_search import HybridSkillSearcher
from toram_skills.repository import SkillRepository

from .models import SkillEvidence


class SkillEvidenceRetriever:
    def __init__(
        self,
        repository: SkillRepository,
        *,
        semantic_runtime=DEFAULT_SEMANTIC_RUNTIME,
        searcher_factory: Callable[..., object] = HybridSkillSearcher,
    ) -> None:
        self.repository = repository
        self.semantic_runtime = semantic_runtime
        self.searcher_factory = searcher_factory

    def _document_rows_for_skill(self, skill_id: str):
        return tuple(
            self.repository.connection.execute(
                """
                SELECT id, skill_id, position, kind, label, text
                FROM skill_search_documents
                WHERE skill_id = ?
                ORDER BY CASE WHEN kind = 'summary' THEN 0 ELSE 1 END, position, id
                """,
                (skill_id,),
            )
        )

    def _document_row(self, document_id: str):
        return self.repository.connection.execute(
            """
            SELECT id, skill_id, position, kind, label, text
            FROM skill_search_documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()

    def _to_evidence(self, row, text: str | None = None) -> SkillEvidence:
        skill_id = str(row["skill_id"])
        skill = self.repository.get_skill(skill_id)
        tree = self.repository.get_tree(skill.tree_id)
        return SkillEvidence(
            document_id=str(row["id"]),
            skill_id=skill_id,
            skill_name=skill.name,
            tree_name=tree.name,
            text=str(row["text"]) if text is None else text,
            source_kind=str(row["kind"]),
            label=None if row["label"] is None else str(row["label"]),
        )

    @staticmethod
    def _apply_budget(
        evidence: list[SkillEvidence],
        *,
        limit: int,
        max_chars: int,
    ) -> tuple[SkillEvidence, ...]:
        if limit <= 0 or max_chars <= 0:
            return ()
        output: list[SkillEvidence] = []
        remaining = max_chars
        for chunk in evidence:
            if len(output) >= limit or remaining <= 0:
                break
            text = chunk.text
            if len(text) > remaining:
                text = text[:remaining]
            if not text:
                break
            output.append(
                SkillEvidence(
                    document_id=chunk.document_id,
                    skill_id=chunk.skill_id,
                    skill_name=chunk.skill_name,
                    tree_name=chunk.tree_name,
                    text=text,
                    source_kind=chunk.source_kind,
                    label=chunk.label,
                )
            )
            remaining -= len(text)
        return tuple(output)

    def _known_skill_evidence(
        self,
        skill_ids: tuple[str, ...],
    ) -> list[SkillEvidence]:
        rows_by_skill = [self._document_rows_for_skill(skill_id) for skill_id in skill_ids]
        ordered_rows: list[object] = []

        # Seed one summary/first document for every requested skill before consuming
        # additional sections so a comparison cannot starve one side of evidence.
        for rows in rows_by_skill:
            if rows:
                ordered_rows.append(rows[0])
        depth = 1
        while True:
            added = False
            for rows in rows_by_skill:
                if depth < len(rows):
                    ordered_rows.append(rows[depth])
                    added = True
            if not added:
                break
            depth += 1
        return [self._to_evidence(row) for row in ordered_rows]

    def _search_evidence(self, question: str, *, limit: int) -> list[SkillEvidence]:
        semantic_index = None
        if self.semantic_runtime is not None:
            try:
                semantic_index = self.semantic_runtime.get_index(self.repository)
            except Exception:
                # Retrieval must remain available in lexical-only mode when the
                # optional embedding runtime is missing or stale.
                semantic_index = None

        searcher = self.searcher_factory(
            self.repository,
            semantic_index=semantic_index,
        )
        hits = searcher.search(question, limit=max(limit, 1))
        document_ids: list[str] = []
        for hit in hits:
            ids = tuple(getattr(hit, "evidence_document_ids", ()) or ())
            if not ids:
                ids = (f"{hit.skill_id}#summary",)
            document_ids.extend(ids)

        seen: set[str] = set()
        evidence: list[SkillEvidence] = []
        for document_id in document_ids:
            if document_id in seen:
                continue
            seen.add(document_id)
            row = self._document_row(document_id)
            if row is None:
                continue
            evidence.append(self._to_evidence(row))
        return evidence

    def retrieve(
        self,
        question: str,
        *,
        skill_ids: tuple[str, ...] = (),
        limit: int = 5,
        max_chars: int = 12000,
    ) -> tuple[SkillEvidence, ...]:
        if limit <= 0 or max_chars <= 0:
            return ()
        if skill_ids:
            evidence = self._known_skill_evidence(skill_ids)
        else:
            evidence = self._search_evidence(question, limit=limit)

        deduplicated: list[SkillEvidence] = []
        seen: set[str] = set()
        for chunk in evidence:
            if chunk.document_id in seen:
                continue
            seen.add(chunk.document_id)
            deduplicated.append(chunk)
        return self._apply_budget(
            deduplicated,
            limit=limit,
            max_chars=max_chars,
        )


__all__ = ["SkillEvidenceRetriever"]
