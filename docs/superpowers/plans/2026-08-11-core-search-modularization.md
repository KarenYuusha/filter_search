# Core Search Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the reverse dependency on `search_items.py`, make item-filter configuration single-source, and split reusable repository/ranking/parser/routing code into focused modules without changing supported search behavior.

**Architecture:** Keep the current deterministic-first search behavior and Qwen safety boundary. Refactor incrementally behind compatibility imports: `toram_data` owns database/domain/filter concerns, `toram_search` owns ranking/parsing/routing/orchestration, and top-level `search_items.py` remains the terminal frontend plus a temporary compatibility surface. Discord behavior is intentionally unchanged in this plan.

**Tech Stack:** Python 3, stdlib `dataclasses`/`unittest`/`ast`, SQLite, existing `rapidfuzz`, existing Discord.py frontend, existing Ollama/Qwen fallback.

## Global Constraints

- No database schema changes.
- No new third-party dependencies.
- Preserve deterministic-first routing and the existing Qwen confirmation/validation boundary.
- Preserve exact item search, upgrade search, stat search, stat-expression search, reconstruction, clarification, and fuzzy-confirmation behavior.
- Preserve current aliases, including `consume -> Usable` and `dte -> % stronger against earth`.
- Keep `crit` intentionally ambiguous between Critical Rate and Critical Damage.
- Do not add boss, skill, build-role, plugin-domain, or generic domain abstraction layers.
- Do not redesign Discord UI or interaction behavior.
- Compatibility imports from `search_items.py` are allowed during migration, but no module under `toram_data/` or `toram_search/` may import `search_items` when this plan is complete.
- Every task ends with a green focused gate before the next task begins.
- The final repository suite must be fully green. Fix the existing structured-fallback logging mismatch before structural work so later regressions cannot hide behind a baseline failure.

---

## Target Module Map

**Create**
- `toram_data/item_filters.py` — item-filter dataclasses, configured filter catalog, exact extraction, trailing extraction.
- `toram_data/models.py` — domain/database result dataclasses currently owned by `search_items.py`.
- `toram_data/repository.py` — SQLite `ItemRepository`.
- `toram_search/ranking.py` — item-name ranking and paging.
- `toram_search/parser.py` — parsed-search models, deterministic parsing, structured-request validation.
- `toram_search/routing.py` — deterministic route classification.
- `toram_search/fallback_adapter.py` — Toram-specific `QwenFallbackService` construction.
- `tests/test_item_filters.py` — direct single-source filter regressions.
- `tests/test_core_module_boundaries.py` — compatibility and dependency-direction regressions.

**Modify**
- `toram_search/fallback.py`
- `toram_data/stat_query.py`
- `toram_search/item_query_entities.py`
- `toram_search/reconstruction.py`
- `toram_search/service.py`
- `search_items.py`
- focused tests already covering fallback, relevance, reconstruction, understanding, parsing, and service behavior.

**Defer to later plans**
- splitting `discord_bot.py` into config/session/render/views/runtime;
- Discord session expiration/cleanup;
- replacing nested stat/source/image dictionaries with typed dataclasses;
- moving terminal screens into a `toram_cli` package.

---

### Task 1: Restore a Fully Green Baseline

**Files:**
- Modify: `toram_search/fallback.py`
- Modify: `tests/test_structured_fallback.py`

**Produces:**
- unchanged `FallbackOutcome` behavior;
- `{"intent": "search"}` reports `missing or invalid search candidates`;
- a search payload containing valid `candidates` plus extra keys still reports `search payload has unexpected fields`.

- [ ] **Step 1: Add the strict-extra-field regression**

Add to `StructuredFallbackTests`:

```python
def test_search_payload_with_candidates_and_extra_field_is_rejected_as_unexpected_fields(self):
    fallback = service(
        {
            "intent": "search",
            "candidates": [
                {
                    "stats": [{"name": "Critical Rate"}],
                    "item_filter": "bow",
                }
            ],
            "extra": True,
        }
    )

    with self.assertLogs("toram_search.fallback", level="DEBUG") as captured:
        outcome = fallback.interpret("cr bow", ())

    self.assertEqual(outcome.kind, "failed")
    self.assertIn(
        "search payload has unexpected fields",
        "\n".join(captured.output),
    )
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest \
  tests.test_structured_fallback.StructuredFallbackTests.test_rejected_payload_is_logged_with_reason \
  tests.test_structured_fallback.StructuredFallbackTests.test_search_payload_with_candidates_and_extra_field_is_rejected_as_unexpected_fields \
  -v
```

Expected before the fix: the existing missing-candidates test fails because `{"intent": "search"}` currently logs `search payload has unexpected fields`.

- [ ] **Step 3: Reorder only the search-payload checks**

Use this branch in `QwenFallbackService.interpret`:

```python
if intent == "search":
    candidates_obj = payload.get("candidates")
    if not isinstance(candidates_obj, list) or not 1 <= len(candidates_obj) <= 3:
        return self._failed("missing or invalid search candidates", payload)
    if set(payload) != {"intent", "candidates"}:
        return self._failed("search payload has unexpected fields", payload)

    valid: list[SearchIntentRequest] = []
    seen: set[SearchIntentRequest] = set()
    for candidate_obj in candidates_obj:
        request = self._search_candidate_from_payload(candidate_obj)
        if request is None or request in seen:
            continue
        seen.add(request)
        if self.validate_search_request(request):
            valid.append(request)
        else:
            logger.debug(
                "Qwen search candidate failed repository validation: %r",
                request,
            )
    if valid:
        return FallbackOutcome("search_requests", search_requests=tuple(valid))
    return self._failed("no search candidate passed validation", payload)
```

- [ ] **Step 4: Verify focused GREEN**

```bash
python -m unittest tests.test_structured_fallback -v
```

Expected: `OK`.

- [ ] **Step 5: Establish clean repository baseline**

```bash
python -m unittest discover -s tests -v
```

Expected: full suite `OK`. If anything else fails, diagnose it before continuing.

- [ ] **Step 6: Commit**

```bash
git add toram_search/fallback.py tests/test_structured_fallback.py
git commit -m "fix: restore structured fallback rejection contract"
```

---

### Task 2: Create One Item-Filter Source of Truth

**Files:**
- Create: `toram_data/item_filters.py`
- Create: `tests/test_item_filters.py`
- Modify: `toram_data/stat_query.py`
- Modify: `search_items.py`
- Modify: `toram_search/item_query_entities.py`
- Modify: `toram_search/reconstruction.py`

**Produces:**
- `ItemTypeFilter(label, item_types, consumed_text)`;
- `ItemFilterPhrase(phrase, label, item_types)`;
- public functions `normalize_filter_text`, `list_item_filter_phrases`, `extract_item_filter`, `resolve_item_filter`, and `extract_trailing_item_filter`;
- compatibility imports from `toram_data.stat_query` for `ItemTypeFilter`, `ItemFilterPhrase`, and `list_item_filter_phrases`.

- [ ] **Step 1: Write direct filter tests**

Create `tests/test_item_filters.py`:

```python
import unittest

from toram_data.item_filters import (
    extract_item_filter,
    extract_trailing_item_filter,
    list_item_filter_phrases,
)


ITEM_TYPES = {
    "Bow",
    "Usable",
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
    "Normal Crysta",
}


class ItemFilterTests(unittest.TestCase):
    def test_consume_alias_resolves_to_usable_without_consuming_stat(self):
        item_filter, remaining = extract_item_filter("consume dte", ITEM_TYPES)

        self.assertIsNotNone(item_filter)
        self.assertEqual(item_filter.label, "Usable")
        self.assertEqual(item_filter.item_types, ("Usable",))
        self.assertEqual(remaining, "dte")

    def test_weapon_xtal_keeps_combined_crysta_semantics(self):
        item_filter, remaining = extract_item_filter("cr weapon xtal", ITEM_TYPES)

        self.assertIsNotNone(item_filter)
        self.assertEqual(item_filter.label, "Weapon Crysta + Red Enhancer")
        self.assertEqual(
            item_filter.item_types,
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        )
        self.assertEqual(remaining, "cr")

    def test_trailing_filter_uses_same_semantics(self):
        item_filter, remaining = extract_trailing_item_filter(
            "critical rate weapon xtal",
            ITEM_TYPES,
        )

        self.assertIsNotNone(item_filter)
        self.assertEqual(item_filter.label, "Weapon Crysta + Red Enhancer")
        self.assertEqual(remaining, "critical rate")

    def test_catalog_contains_consume_alias(self):
        rows = list_item_filter_phrases(ITEM_TYPES)
        self.assertIn(
            ("consume", "Usable"),
            {(row.phrase, row.label) for row in rows},
        )
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_item_filters -v
```

Expected: import failure because `toram_data.item_filters` does not exist.

- [ ] **Step 3: Build `toram_data/item_filters.py` from existing code**

Use this import boundary:

```python
from __future__ import annotations

from dataclasses import dataclass

from toram_data.aliases import (
    ALL_CRYSTA_TYPES,
    ITEM_TYPE_ALIASES,
    ITEM_WORD_ALIASES,
    MAIN_WEAPON_TYPES,
    normalize_name,
    normalize_stat_text,
)
```

Move, without semantic changes, the current filter dataclasses and the shared logic for:
- filter text normalization;
- available-type normalization;
- configured Weapon/Special/Armor/Additional crysta combinations;
- colored enhancer filters;
- `xtal`, `wp`, and `weapon` filters;
- every `ITEM_TYPE_ALIASES` entry;
- list/extract/resolve operations;
- trailing-filter extraction used by stat expressions.

There must be exactly one configured catalog implementation after this task.

- [ ] **Step 4: Make `stat_query.py` consume the shared module**

Import:

```python
from toram_data.item_filters import (
    ItemFilterPhrase,
    ItemTypeFilter,
    extract_trailing_item_filter,
    list_item_filter_phrases,
)
```

Delete its local filter dataclasses and configured catalog. Replace local trailing-filter calls with `extract_trailing_item_filter`. Do not change boolean/comparison/natural-language grammar.

- [ ] **Step 5: Make `search_items.py` consume the shared module**

Import:

```python
from toram_data.item_filters import (
    ItemTypeFilter,
    extract_item_filter,
    list_item_filter_phrases,
    resolve_item_filter,
)
```

Delete its duplicate filter normalizer, type lookup helpers, phrase finder, configured catalog, extractor, and resolver.

Until fallback construction moves in Task 7, build Qwen filter labels from:

```python
filter_labels = []
seen_filter_labels = set()
for row in list_item_filter_phrases(repository.list_item_types()):
    rendered = f"{row.phrase} -> {row.label}"
    if rendered in seen_filter_labels:
        continue
    seen_filter_labels.add(rendered)
    filter_labels.append(rendered)
```

- [ ] **Step 6: Point understanding/reconstruction at the canonical filter module**

`toram_search/item_query_entities.py` and `toram_search/reconstruction.py` import `ItemFilterPhrase` / `list_item_filter_phrases` from `toram_data.item_filters`, not `toram_data.stat_query`.

- [ ] **Step 7: Verify focused GREEN**

```bash
python -m unittest \
  tests.test_item_filters \
  tests.test_item_query_entities \
  tests.test_query_reconstruction \
  tests.test_item_query_understanding \
  -v
```

Expected: `OK`.

- [ ] **Step 8: Verify full suite and commit**

```bash
python -m unittest discover -s tests -v

git add \
  toram_data/item_filters.py \
  toram_data/stat_query.py \
  toram_search/item_query_entities.py \
  toram_search/reconstruction.py \
  search_items.py \
  tests/test_item_filters.py
git commit -m "refactor: centralize item filter definitions"
```

---

### Task 3: Extract Domain Models and SQLite Repository

**Files:**
- Create: `toram_data/models.py`
- Create: `toram_data/repository.py`
- Create: `tests/test_core_module_boundaries.py`
- Modify: `search_items.py`

**Produces:**
- `toram_data.models`: `ItemSummary`, `ItemDetail`, `StatRow`, `RankedStatItem`, `ClauseMatch`, `RankedExpressionItem`, `UpgradeGraph`;
- `toram_data.repository.ItemRepository` with the same constructor and public methods as today;
- compatibility re-exports from `search_items.py`.

- [ ] **Step 1: Write compatibility tests**

Create `tests/test_core_module_boundaries.py`:

```python
import unittest

import search_items
from toram_data.models import (
    ClauseMatch,
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    StatRow,
    UpgradeGraph,
)
from toram_data.repository import ItemRepository


class CoreModuleBoundaryTests(unittest.TestCase):
    def test_search_items_reexports_domain_models(self):
        self.assertIs(search_items.ItemSummary, ItemSummary)
        self.assertIs(search_items.ItemDetail, ItemDetail)
        self.assertIs(search_items.StatRow, StatRow)
        self.assertIs(search_items.RankedStatItem, RankedStatItem)
        self.assertIs(search_items.ClauseMatch, ClauseMatch)
        self.assertIs(search_items.RankedExpressionItem, RankedExpressionItem)
        self.assertIs(search_items.UpgradeGraph, UpgradeGraph)

    def test_search_items_reexports_repository(self):
        self.assertIs(search_items.ItemRepository, ItemRepository)
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_core_module_boundaries -v
```

Expected: import failures because the new modules do not exist.

- [ ] **Step 3: Move domain/database dataclasses into `toram_data/models.py`**

Use:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from toram_data.stat_query import ResolvedClause
```

Move the seven existing class definitions unchanged. Keep `stats`, `sources`, and `images` in `ItemDetail` as their existing list/dictionary forms. Do not move `ParsedSearch`, `StatResolution`, `DeterministicRoute`, `RankedItem`, or presentation-only structures.

- [ ] **Step 4: Move `ItemRepository` into `toram_data/repository.py`**

Move the class and its private helpers without changing SQL. Preserve public methods:
- `close`;
- `list_items`;
- `list_upgrade_items`;
- `exact_upgrade_name_matches`;
- `list_item_types`;
- `list_stat_names`;
- `count_items_total`;
- `count_items_by_types`;
- `count_items_with_stat`;
- `exact_name_matches`;
- `get_item`;
- `get_upgrade_predecessors`;
- `get_upgrade_successors`;
- `get_upgrade_component`;
- `search_by_stat`;
- `search_by_expression`.

The module imports domain models from `toram_data.models`, normalization/crysta helpers from `toram_data.aliases`, and expression types/helpers from `toram_data.stat_query`.

- [ ] **Step 5: Replace moved definitions in `search_items.py` with imports**

```python
from toram_data.models import (
    ClauseMatch,
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    StatRow,
    UpgradeGraph,
)
from toram_data.repository import ItemRepository
```

Delete the original duplicate class definitions.

- [ ] **Step 6: Verify focused GREEN**

```bash
python -m unittest \
  tests.test_core_module_boundaries \
  tests.test_search_service \
  tests.test_item_search_relevance \
  -v
```

Expected: `OK`.

- [ ] **Step 7: Run real-database repository smoke**

```bash
python - <<'PY'
from pathlib import Path
from toram_data.repository import ItemRepository

repo = ItemRepository(Path("coryn_data/database/items.sqlite"))
try:
    assert repo.list_items()
    assert "Usable" in repo.list_item_types()
    assert repo.list_stat_names()
finally:
    repo.close()
print("repository smoke: OK")
PY
```

Expected: `repository smoke: OK`.

- [ ] **Step 8: Verify full suite and commit**

```bash
python -m unittest discover -s tests -v

git add toram_data/models.py toram_data/repository.py search_items.py tests/test_core_module_boundaries.py
git commit -m "refactor: extract item repository and domain models"
```

---

### Task 4: Extract Item-Name Ranking

**Files:**
- Create: `toram_search/ranking.py`
- Modify: `search_items.py`
- Modify: `tests/test_core_module_boundaries.py`
- Test: `tests/test_item_search_relevance.py`

**Produces:**
- `RankedItem(item, score, match_kind)`;
- `rank_items(query, items)`;
- `page_results(results, page, page_size)`;
- existing constants `MIN_QUERY_LENGTH` and `ITEM_FUZZY_RELEVANCE_THRESHOLD` owned by `toram_search.ranking`.

- [ ] **Step 1: Add compatibility assertions**

Inside `CoreModuleBoundaryTests`:

```python
def test_search_items_reexports_ranking_symbols(self):
    from toram_search.ranking import RankedItem, rank_items

    self.assertIs(search_items.RankedItem, RankedItem)
    self.assertIs(search_items.rank_items, rank_items)
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_core_module_boundaries.CoreModuleBoundaryTests.test_search_items_reexports_ranking_symbols -v
```

Expected: import failure for `toram_search.ranking`.

- [ ] **Step 3: Move ranking code unchanged**

Create `toram_search/ranking.py` with imports:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rapidfuzz import fuzz

from toram_data.aliases import normalize_name
from toram_data.models import ItemSummary
```

Move `RankedItem`, `_score_item`, `_is_relevant_ranked_item`, `rank_items`, and `page_results` unchanged. Preserve the threshold of `70.0` and all four current RapidFuzz score components.

- [ ] **Step 4: Re-export from `search_items.py`**

```python
from toram_search.ranking import RankedItem, page_results, rank_items
```

Delete the original ranking definitions.

- [ ] **Step 5: Verify relevance suite**

```bash
python -m unittest tests.test_item_search_relevance tests.test_core_module_boundaries -v
```

Expected: `OK`, including existing 69.999/70.0 threshold tests.

- [ ] **Step 6: Verify full suite and commit**

```bash
python -m unittest discover -s tests -v

git add toram_search/ranking.py search_items.py tests/test_core_module_boundaries.py
git commit -m "refactor: extract item name ranking"
```

---

### Task 5: Extract Deterministic Query Parsing

**Files:**
- Create: `toram_search/parser.py`
- Modify: `search_items.py`
- Modify: `tests/test_core_module_boundaries.py`
- Test: existing stat-query, structured-intent, service, and reconstruction suites.

**Produces:**
- parser models `SearchIntent`, `StatResolution`, `ParsedSearch` with their current fields;
- `SEARCH_ONLY_STAT_ALIASES`;
- public functions `resolve_stat_choices`, `resolve_stat_name`, `parse_expression_request`, `parse_structured_search_request`, `format_structured_search_request`, `extract_natural_upgrade_target`, `parse_search_query`, `build_search_stat_terms`, and `find_non_overlapping_stat_terms`;
- compatibility re-exports from `search_items.py`.

- [ ] **Step 1: Add parser compatibility assertions**

Inside `CoreModuleBoundaryTests`:

```python
def test_search_items_reexports_parser_symbols(self):
    from toram_search import parser as parser_module

    self.assertIs(search_items.ParsedSearch, parser_module.ParsedSearch)
    self.assertIs(search_items.StatResolution, parser_module.StatResolution)
    self.assertIs(search_items.parse_search_query, parser_module.parse_search_query)
    self.assertIs(
        search_items.parse_structured_search_request,
        parser_module.parse_structured_search_request,
    )
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_core_module_boundaries.CoreModuleBoundaryTests.test_search_items_reexports_parser_symbols -v
```

Expected: import failure for `toram_search.parser`.

- [ ] **Step 3: Move noninteractive parser code into `toram_search/parser.py`**

Move without grammar changes:
- `SearchIntent`, `StatResolution`, `ParsedSearch`;
- stat choice/name resolution;
- negative bare-expression parsing;
- expression request parsing;
- structured request validation;
- natural-upgrade target extraction;
- base `parse_search_query`;
- stat-term discovery helpers used by routing;
- database stat/filter validation helpers used by structured requests.

Rename `_format_structured_search_request` to the public `format_structured_search_request` in the canonical module.

The parser imports directly from `toram_data.aliases`, `toram_data.item_filters`, `toram_data.repository`, `toram_data.stat_query`, and `toram_search.fallback`. It must not import `search_items`.

- [ ] **Step 4: Keep terminal interaction code in `search_items.py`**

Interactive functions such as `resolve_expression_interactively`, `prompt_guided_stat`, result screens, and the terminal loop stay in `search_items.py`. Update them to consume the imported parser APIs.

Retain temporary private-name compatibility:

```python
from toram_search.parser import format_structured_search_request

_format_structured_search_request = format_structured_search_request
```

- [ ] **Step 5: Verify focused parser/service behavior**

```bash
python -m unittest \
  tests.test_core_module_boundaries \
  tests.test_structured_fallback \
  tests.test_query_reconstruction \
  tests.test_item_query_understanding \
  tests.test_search_service \
  -v
```

Also run every existing test module whose filename contains `stat_query`, `structured_intent`, or `natural` before committing.

- [ ] **Step 6: Verify full suite and commit**

```bash
python -m unittest discover -s tests -v

git add toram_search/parser.py search_items.py tests/test_core_module_boundaries.py
git commit -m "refactor: extract deterministic query parser"
```

---

### Task 6: Extract Deterministic Routing

**Files:**
- Create: `toram_search/routing.py`
- Modify: `search_items.py`
- Modify: `toram_search/service.py`
- Modify: `tests/test_core_module_boundaries.py`

**Produces:**
- `DeterministicRoute` with current fields;
- `make_database_question_service(repository)`;
- `route_deterministically(query, repository, all_items, help_service, database_service)`;
- current route precedence unchanged.

- [ ] **Step 1: Add routing compatibility assertions**

Inside `CoreModuleBoundaryTests`:

```python
def test_search_items_reexports_routing_symbols(self):
    from toram_search.routing import DeterministicRoute, route_deterministically

    self.assertIs(search_items.DeterministicRoute, DeterministicRoute)
    self.assertIs(search_items.route_deterministically, route_deterministically)
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_core_module_boundaries.CoreModuleBoundaryTests.test_search_items_reexports_routing_symbols -v
```

Expected: import failure for `toram_search.routing`.

- [ ] **Step 3: Move routing-only logic into `toram_search/routing.py`**

Move unchanged:
- `DeterministicRoute`;
- simple `highest`/`best` route helper;
- parsed-expression unknown-stat detection used by routing;
- failed-stat query detection;
- out-of-scope build concept detection;
- assistant-question detection;
- database-question service factory;
- `route_deterministically`.

Use parser APIs from `toram_search.parser`; do not duplicate parsing logic.

Preserve precedence exactly:

```text
parsed direct search
-> simple ranking search
-> direct help
-> direct database question
-> out-of-scope refusal
-> assistant-question fallback
-> failed-stat fallback
-> ordinary item search
```

- [ ] **Step 4: Re-export routing from `search_items.py` and update service routing import**

```python
from toram_search.routing import (
    DeterministicRoute,
    make_database_question_service,
    route_deterministically,
)
```

In `toram_search/service.py`, import `route_deterministically` directly. The remaining `import search_items as core` is removed in Task 7, not here.

- [ ] **Step 5: Verify focused GREEN**

```bash
python -m unittest \
  tests.test_core_module_boundaries \
  tests.test_search_service \
  tests.test_item_query_understanding \
  tests.test_query_reconstruction \
  -v
```

Expected: `OK`.

- [ ] **Step 6: Verify full suite and commit**

```bash
python -m unittest discover -s tests -v

git add toram_search/routing.py toram_search/service.py search_items.py tests/test_core_module_boundaries.py
git commit -m "refactor: extract deterministic search routing"
```

---

### Task 7: Extract Fallback Construction and Decouple `SearchService`

**Files:**
- Create: `toram_search/fallback_adapter.py`
- Modify: `toram_search/service.py`
- Modify: `search_items.py`
- Modify: `tests/test_core_module_boundaries.py`

**Produces:**
- `build_fallback_service(repository, database_service, llm_client)`;
- no `search_items` import anywhere under `toram_data/` or `toram_search/`.

- [ ] **Step 1: Add dependency-direction test**

Add module imports at top of `tests/test_core_module_boundaries.py`:

```python
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
```

Add helper and test inside the module/class:

```python
def imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class CoreModuleBoundaryTests(unittest.TestCase):
    # keep existing tests above this method

    def test_package_modules_do_not_import_top_level_search_items(self):
        offenders = []
        for package in ("toram_data", "toram_search"):
            for path in sorted((PROJECT_ROOT / package).glob("*.py")):
                if "search_items" in imported_module_names(path):
                    offenders.append(str(path.relative_to(PROJECT_ROOT)))

        self.assertEqual(offenders, [])
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_core_module_boundaries.CoreModuleBoundaryTests.test_package_modules_do_not_import_top_level_search_items -v
```

Expected: FAIL listing at least `toram_search/service.py`.

- [ ] **Step 3: Create `toram_search/fallback_adapter.py`**

Use canonical sources:

```python
from toram_data.aliases import STAT_ALIASES, STAT_AMBIGUOUS_GROUPS
from toram_data.item_filters import list_item_filter_phrases
from toram_data.repository import ItemRepository
from toram_search.fallback import QwenFallbackService
from toram_search.parser import (
    SEARCH_ONLY_STAT_ALIASES,
    parse_structured_search_request,
)
```

Build alias text exactly as today:

```python
aliases: list[str] = []
for alias, target in sorted(STAT_ALIASES.items()):
    aliases.append(f"{alias} -> {target}")
for alias, targets in sorted(STAT_AMBIGUOUS_GROUPS.items()):
    aliases.append(f"{alias} -> {' / '.join(targets)}")
for alias, target in sorted(SEARCH_ONLY_STAT_ALIASES.items()):
    aliases.append(f"{alias} -> {target}")
```

Build deduplicated filter labels from `list_item_filter_phrases(repository.list_item_types())` and construct `QwenFallbackService` with:
- repository-backed structured-search validation;
- existing database action validation;
- repository stat catalog;
- alias catalog above;
- canonical filter label catalog.

- [ ] **Step 4: Remove every `core.*` dependency from `toram_search/service.py`**

Replace `import search_items as core` with direct imports from:
- `toram_data.aliases`;
- `toram_data.models`;
- `toram_data.repository`;
- `toram_search.fallback_adapter`;
- `toram_search.parser`;
- `toram_search.ranking`;
- `toram_search.routing`.

Keep `SearchService` decisions/materialization unchanged. This task changes ownership/imports, not search semantics.

- [ ] **Step 5: Make the terminal frontend use the canonical fallback factory**

In `search_items.py`:

```python
from toram_search.fallback_adapter import build_fallback_service

_build_fallback_service = build_fallback_service
```

Update its terminal initialization to:

```python
fallback_service = build_fallback_service(
    repository,
    database_service,
    client,
)
```

Delete the old local factory implementation.

- [ ] **Step 6: Verify dependency GREEN**

```bash
python -m unittest tests.test_core_module_boundaries.CoreModuleBoundaryTests.test_package_modules_do_not_import_top_level_search_items -v
```

Expected: `OK`.

- [ ] **Step 7: Verify focused behavior**

```bash
python -m unittest \
  tests.test_search_service \
  tests.test_structured_fallback \
  tests.test_item_query_understanding \
  tests.test_query_reconstruction \
  tests.test_item_search_relevance \
  tests.test_core_module_boundaries \
  -v
```

Expected: `OK`.

- [ ] **Step 8: Compile the modularized core**

```bash
python -m py_compile \
  toram_data/aliases.py \
  toram_data/item_filters.py \
  toram_data/models.py \
  toram_data/repository.py \
  toram_data/stat_query.py \
  toram_search/fallback.py \
  toram_search/fallback_adapter.py \
  toram_search/item_query_entities.py \
  toram_search/parser.py \
  toram_search/ranking.py \
  toram_search/reconstruction.py \
  toram_search/routing.py \
  toram_search/service.py \
  toram_search/understanding.py \
  search_items.py \
  discord_bot.py
```

Expected: exit code 0 with no output.

- [ ] **Step 9: Commit**

```bash
git add \
  toram_search/fallback_adapter.py \
  toram_search/service.py \
  search_items.py \
  tests/test_core_module_boundaries.py
git commit -m "refactor: decouple search service from cli module"
```

---

### Task 8: Final Compatibility Cleanup and End-to-End Verification

**Files:**
- Modify: `search_items.py`
- Modify: `tests/test_core_module_boundaries.py`
- Modify only other files needed to remove dead duplicate imports/definitions.

**Produces:**
- `search_items.py` remains the supported terminal entry point and compatibility surface;
- reusable implementation lives under `toram_data` / `toram_search`;
- Discord remains behaviorally unchanged.

- [ ] **Step 1: Add final canonical-export assertions**

Inside `CoreModuleBoundaryTests`:

```python
def test_search_items_exports_point_to_canonical_modules(self):
    from toram_data import item_filters
    from toram_data.repository import ItemRepository
    from toram_search import parser as parser_module
    from toram_search import ranking as ranking_module
    from toram_search import routing as routing_module

    self.assertIs(search_items.extract_item_filter, item_filters.extract_item_filter)
    self.assertIs(search_items.resolve_item_filter, item_filters.resolve_item_filter)
    self.assertIs(search_items.ItemRepository, ItemRepository)
    self.assertIs(search_items.rank_items, ranking_module.rank_items)
    self.assertIs(search_items.parse_search_query, parser_module.parse_search_query)
    self.assertIs(
        search_items.route_deterministically,
        routing_module.route_deterministically,
    )
```

- [ ] **Step 2: Remove dead duplicate core code from `search_items.py`**

There must be no second implementation of:
- item-filter catalog/extraction;
- domain/database models;
- `ItemRepository`;
- item-name ranking;
- deterministic parser;
- deterministic router;
- fallback-service factory.

Keep terminal rendering, terminal input loops, argument parsing, `main()`, and compatibility imports. Do not start the separate CLI extraction here.

- [ ] **Step 3: Run real-database regression smoke**

```bash
python - <<'PY'
from pathlib import Path

from toram_data.repository import ItemRepository
from toram_search.service import SearchService
from toram_search.session import FailedQueryContext


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("deterministic query unexpectedly called Qwen")


repo = ItemRepository(Path("coryn_data/database/items.sqlite"))
try:
    service = SearchService(repo, llm_client=MustNotCallLLM())

    consume = service.handle_query("consume dte", FailedQueryContext(max_entries=3))
    assert consume.kind in {"search", "item_understanding"}, consume

    reconstructed = service.handle_query(
        "xtall cr weapon",
        FailedQueryContext(max_entries=3),
    )
    assert reconstructed.kind == "search", reconstructed

    ambiguous = service.handle_query(
        "crit xtal weapon",
        FailedQueryContext(max_entries=3),
    )
    assert ambiguous.kind in {"search", "item_understanding"}, ambiguous

    exact_name = repo.list_items()[0].name
    exact = service.handle_query(exact_name, FailedQueryContext(max_entries=3))
    assert exact.kind == "search", exact
finally:
    repo.close()

print("core modularization smoke: OK")
PY
```

Expected: `core modularization smoke: OK` and no Qwen call on deterministic cases.

- [ ] **Step 4: Run complete test suite**

```bash
python -m unittest discover -s tests -v
```

Expected: full suite `OK` with zero known baseline failures.

- [ ] **Step 5: Compile all relevant modules**

```bash
python -m compileall -q toram_data toram_search search_items.py discord_bot.py
```

Expected: exit code 0.

- [ ] **Step 6: Inspect dependency direction mechanically**

```bash
python - <<'PY'
import ast
from pathlib import Path

root = Path(".")
offenders = []
for package in ("toram_data", "toram_search"):
    for path in sorted((root / package).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "search_items" for alias in node.names):
                    offenders.append(str(path))
            elif isinstance(node, ast.ImportFrom) and node.module == "search_items":
                offenders.append(str(path))

assert not offenders, offenders
print("dependency direction: OK")
PY
```

Expected: `dependency direction: OK`.

- [ ] **Step 7: Review final diff for refactor-only scope**

The implementation diff must show:
- one canonical filter module;
- reusable models/repository/ranking/parser/routing modules;
- `SearchService` importing package modules directly;
- `search_items.py` retaining terminal/compatibility concerns;
- no Discord UX changes;
- no DB/schema changes;
- no boss/skill abstractions.

Any behavior-changing feature found in review is removed or split into a separate PR.

- [ ] **Step 8: Commit final cleanup**

```bash
git add search_items.py tests/test_core_module_boundaries.py
git commit -m "refactor: finalize core search module boundaries"
```

---

## Completion Criteria

The refactor is complete only when all are true:

1. `python -m unittest discover -s tests -v` is fully green.
2. `toram_data/` and `toram_search/` contain no import of top-level `search_items`.
3. There is only one item-filter catalog/extraction implementation.
4. `SearchService` depends directly on package modules, not CLI code.
5. `search_items.py` still runs as the existing terminal entry point.
6. Existing Discord behavior is unchanged.
7. `consume dte`, `xtall cr weapon`, `crit xtal weapon`, exact item-name search, upgrade search, and structured Qwen confirmation remain covered by tests/smoke checks.
8. No new dependency, DB migration, or cross-domain abstraction was introduced.

## Follow-Up Plans

Do not bundle these into the same implementation PR:

1. **Discord lifecycle modularization** — split config/session/render/views/runtime and add bounded session expiration/cleanup.
2. **Typed item-detail records** — replace nested stat/source/image dictionaries with explicit dataclasses after core imports stabilize.
3. **CLI extraction** — move terminal rendering/screens into a `toram_cli` package if the CLI remains actively maintained.
