# Canonical Skill Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministically convert the complete `raw_skills/` corpus into a validated, lossless canonical `skills.sqlite` database that future hybrid retrieval can trust.

**Architecture:** Add a new `toram_skills` package that owns source discovery, canonical models, deterministic parsing, validation, SQLite schema/repository, and whole-corpus import. The importer preserves every skill block and tree-level source text even when a field cannot be normalized, records explicit source uncertainty, and replaces the generated database only after a complete zero-error import. Existing item/search/Discord code remains untouched.

**Tech Stack:** Python >=3.12; standard-library `dataclasses`, `pathlib`, `re`, `json`, `hashlib`, `sqlite3`, `tempfile`, `os`; existing `unittest` style. No new dependency and no LLM call.

## Global Constraints

- Read only from `raw_skills/assist_skills/`, `raw_skills/other_skill_trees/`, `raw_skills/sub_weapon_skills/`, and `raw_skills/weapon_class_skills/`.
- Generate `coryn_data/database/skills.sqlite`, separate from `items.sqlite`.
- `raw_skills/` is the editable source of truth; generated data must be reproducible.
- Never use an LLM to discover, split, normalize, validate, or import source data.
- Never infer game facts from general Toram knowledge.
- Preserve unrecognized and uncertain source content in canonical sections and `raw_text`.
- Duplicate normalized skill names are allowed across different trees but are import errors within the same tree.
- Tree ID = relative source path without `.txt`; skill ID = tree ID + normalized-name slug.
- No FTS5, embeddings, hybrid ranking, `gemma4:e4b`, formula evaluation, skill routing, or Discord integration in this milestone.
- Do not modify item-search semantics, item Qwen behavior, `items.sqlite`, or Discord behavior.

## File Structure

```text
toram_skills/
    __init__.py
    source_inventory.py
    models.py
    parsing.py
    schema.py
    repository.py
    importer.py
    report.py

build_skills.py

tests/test_skill_source_inventory.py
tests/test_skill_parsing.py
tests/test_skill_field_normalization.py
tests/test_skill_repository.py
tests/test_skill_importer.py
```

Do not put Milestone 1 skill code into `toram_data`, `toram_search`, or `toram_discord`.

---

### Task 1: Source Discovery and Corpus Inventory

**Files:**
- Create: `toram_skills/__init__.py`
- Create: `toram_skills/source_inventory.py`
- Create: `tests/test_skill_source_inventory.py`

**Interfaces:**
- Produces `SkillSource(path, relative_path, tree_group, text, declared_category, marker_count)`.
- Produces `discover_skill_sources(raw_root: Path) -> tuple[SkillSource, ...]`.
- Produces `source_manifest_hash(sources: tuple[SkillSource, ...]) -> str`.

- [ ] **Step 1: Write the failing discovery tests**

```python
from pathlib import Path
import tempfile
import unittest

from toram_skills.source_inventory import discover_skill_sources, source_manifest_hash


class SkillSourceInventoryTests(unittest.TestCase):
    def test_discovers_supported_groups_in_stable_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assist_skills").mkdir()
            (root / "weapon_class_skills").mkdir()
            (root / "assist_skills" / "battle_skills.txt").write_text(
                "Category: Battle Skills\nSKILL: MAGIC UP\n", encoding="utf-8"
            )
            (root / "weapon_class_skills" / "magic_skills.txt").write_text(
                "Category: Magic Skills\nSKILL: MAGIC: ARROWS\n", encoding="utf-8"
            )

            sources = discover_skill_sources(root)

            self.assertEqual(
                [source.relative_path for source in sources],
                ["assist_skills/battle_skills.txt", "weapon_class_skills/magic_skills.txt"],
            )
            self.assertEqual(sources[0].tree_group, "assist")
            self.assertEqual(sources[1].tree_group, "weapon-class")
            self.assertEqual(sources[0].declared_category, "Battle Skills")
            self.assertEqual(sources[0].marker_count, 1)
            self.assertEqual(source_manifest_hash(sources), source_manifest_hash(sources))
```

Add a second test that creates `unknown/x.txt` and expects `ValueError("Unsupported raw skill group: unknown")`.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_source_inventory -v
```

Expected: missing `toram_skills.source_inventory`.

- [ ] **Step 3: Implement deterministic discovery**

```python
# toram_skills/source_inventory.py
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

_GROUPS = {
    "assist_skills": "assist",
    "other_skill_trees": "other",
    "sub_weapon_skills": "sub-weapon",
    "weapon_class_skills": "weapon-class",
}
_CATEGORY_RE = re.compile(r"(?mi)^Category:\s*(.+?)\s*$")
_SKILL_MARKER_RE = re.compile(r"(?mi)^SKILL:\s*.+?\s*$")


@dataclass(frozen=True)
class SkillSource:
    path: Path
    relative_path: str
    tree_group: str
    text: str
    declared_category: str | None
    marker_count: int


def discover_skill_sources(raw_root: Path) -> tuple[SkillSource, ...]:
    root = Path(raw_root)
    result: list[SkillSource] = []
    for path in sorted(root.rglob("*.txt")):
        relative = path.relative_to(root).as_posix()
        group_dir = Path(relative).parts[0]
        if group_dir not in _GROUPS:
            raise ValueError(f"Unsupported raw skill group: {group_dir}")
        text = path.read_text(encoding="utf-8")
        category = _CATEGORY_RE.search(text)
        result.append(SkillSource(
            path=path,
            relative_path=relative,
            tree_group=_GROUPS[group_dir],
            text=text,
            declared_category=category.group(1).strip() if category else None,
            marker_count=len(_SKILL_MARKER_RE.findall(text)),
        ))
    return tuple(result)


def source_manifest_hash(sources: tuple[SkillSource, ...]) -> str:
    digest = sha256()
    for source in sources:
        digest.update(source.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
```

- [ ] **Step 4: Add the real-corpus classification test**

```python
def test_real_corpus_is_nonempty_and_all_four_groups_exist(self):
    raw_root = Path(__file__).resolve().parents[1] / "raw_skills"
    sources = discover_skill_sources(raw_root)
    self.assertGreater(len(sources), 0)
    self.assertTrue(all(source.text.strip() for source in sources))
    self.assertEqual(
        {source.tree_group for source in sources},
        {"assist", "other", "sub-weapon", "weapon-class"},
    )
```

- [ ] **Step 5: Verify GREEN and regression safety**

```bash
python -m unittest tests.test_skill_source_inventory -v
python -m unittest discover -s tests -v
```

- [ ] **Step 6: Commit**

```bash
git add toram_skills/__init__.py toram_skills/source_inventory.py tests/test_skill_source_inventory.py
git commit -m "feat: inventory raw skill sources"
```

---

### Task 2: Canonical Models and Standard `SKILL:` Block Parsing

**Files:**
- Create: `toram_skills/models.py`
- Create: `toram_skills/parsing.py`
- Create: `tests/test_skill_parsing.py`
- Modify: `toram_skills/__init__.py`

**Interfaces:**
- `ParseIssue(level: Literal["error", "warning"], code, source_file, skill_name, message)`.
- `SkillSection(position, label, normalized_label, body)`.
- `SkillTreeDraft(id, name, normalized_name, tree_group, source_file, general_text, tier_requirements, weapon_restrictions, issues)`.
- `SkillDraft(id, tree_id, source_order, name, normalized_name, aliases, tier, required_level, skill_type, mp_cost_text, mp_cost_value, damage_type, element, cast_range_text, hit_range_text, cast_time_text, hit_count_text, ailments, weapon_requirements, weapon_restrictions, sections, description, game_description, raw_text, issues)`.
- `ParsedSkillFile(tree, skills, issues, discovered_skill_blocks)`.
- `ImportReport(files_discovered, trees_created, skill_blocks_discovered, skills_created, manifest_hash, issues)` with `errors`, `warnings`, `error_codes`, `is_valid` properties.
- `normalize_skill_name(text)`, `skill_tree_id(relative_path)`, `skill_id(tree_id, skill_name)`.
- `parse_standard_skill_file(source) -> ParsedSkillFile`.

- [ ] **Step 1: Write failing Battle/Magic parser tests**

```python
ROOT = Path(__file__).resolve().parents[1]

class SkillParsingTests(unittest.TestCase):
    def _source(self, path: str):
        return next(
            source for source in discover_skill_sources(ROOT / "raw_skills")
            if source.relative_path == path
        )

    def test_battle_blocks_preserve_raw_source(self):
        parsed = parse_standard_skill_file(self._source("assist_skills/battle_skills.txt"))
        magic_up = next(skill for skill in parsed.skills if skill.name == "MAGIC UP")
        self.assertEqual(magic_up.id, "assist_skills/battle_skills/magic-up")
        self.assertIn("MATK Increase", magic_up.raw_text)
        self.assertTrue(parsed.tree.general_text.startswith("Category: Battle Skills"))

    def test_magic_source_details_are_preserved_as_section(self):
        parsed = parse_standard_skill_file(self._source("weapon_class_skills/magic_skills.txt"))
        arrows = next(skill for skill in parsed.skills if skill.name == "MAGIC: ARROWS")
        self.assertIn("source details", [s.normalized_label for s in arrows.sections])
        self.assertIn("Base Skill Multiplier", arrows.raw_text)
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_parsing -v
```

- [ ] **Step 3: Implement immutable dataclasses and stable IDs**

```python
def normalize_skill_name(text: str) -> str:
    return " ".join(text.casefold().replace("’", "'").split())


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", normalize_skill_name(text)).strip("-")
    if not value:
        raise ValueError("Cannot build identifier from empty text")
    return value


def skill_tree_id(relative_path: str) -> str:
    return Path(relative_path).with_suffix("").as_posix()


def skill_id(tree_id: str, skill_name: str) -> str:
    return f"{tree_id}/{_slug(skill_name)}"
```

Use tuples for aliases, issues, sections, ailments, weapon lists, and tier requirements. `ImportReport.errors`/`warnings` filter `issues`; `is_valid` is `not errors`.

- [ ] **Step 4: Implement standard block splitting**

```python
_SKILL_BLOCK_RE = re.compile(
    r"(?ms)^=+\s*\nSKILL:\s*(?P<name>[^\n]+)\s*\n=+\s*\n(?P<body>.*?)(?=^=+\s*\nSKILL:|\Z)"
)


def _split_standard_blocks(text: str):
    matches = list(_SKILL_BLOCK_RE.finditer(text))
    if not matches:
        return text, ()
    general = text[:matches[0].start()].rstrip()
    return general, tuple((m.group("name").strip(), m.group(0).strip()) for m in matches)
```

Create sections only from explicit standalone `Label:` headings. Preserve each entire block in `raw_text`. At this task, normalized mechanic fields can remain empty.

- [ ] **Step 5: Add duplicate-name safety**

Test two same-tree blocks named `TEST SKILL` and `Test Skill`. Emit `ParseIssue("error", "duplicate_skill_name", ...)`; never overwrite one record silently.

- [ ] **Step 6: Run focused tests**

```bash
python -m unittest tests.test_skill_parsing -v
```

- [ ] **Step 7: Commit**

```bash
git add toram_skills/models.py toram_skills/parsing.py toram_skills/__init__.py tests/test_skill_parsing.py
git commit -m "feat: parse canonical skill blocks"
```

---

### Task 3: Nonstandard Minstrel Parsing and Tree-Level Rules

**Files:**
- Modify: `toram_skills/parsing.py`
- Modify: `tests/test_skill_parsing.py`

**Interfaces:**
- Produces `parse_skill_file(source: SkillSource) -> ParsedSkillFile`.
- Standard marker files dispatch to `parse_standard_skill_file`.
- `assist_skills/minstrel_skills.txt` dispatches to a deterministic roster-based parser.
- Any future unmarked/unregistered format yields error code `unsupported_source_format`.

- [ ] **Step 1: Write the failing Minstrel test**

```python
def test_minstrel_uses_embedded_tier_roster(self):
    parsed = parse_skill_file(self._source("assist_skills/minstrel_skills.txt"))
    names = [skill.name for skill in parsed.skills]
    self.assertIn("Healing Song", names)
    self.assertIn("Beat Blast", names)
    self.assertIn("Battle Anthem", names)
    self.assertEqual(next(s for s in parsed.skills if s.name == "Healing Song").tier, 1)
    self.assertEqual(next(s for s in parsed.skills if s.name == "Battle Anthem").tier, 3)
    self.assertIn("Acoustic Buff", parsed.tree.general_text)
```

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_parsing.SkillParsingTests.test_minstrel_uses_embedded_tier_roster -v
```

- [ ] **Step 3: Implement the roster-derived Minstrel parser**

Parse the final roster beginning with `Lvl req,` and tier headings `Tier I`, `Tier II`, `Tier III`. Use:

```python
_ROMAN_TIERS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
```

Do not hardcode skill names. Build ordered `(name, tier)` entries from the source roster. For each name, locate its first standalone heading before the roster and its trailing source tag line matching:

```python
rf"(?mi)^{re.escape(skill_name)}\s*\|\s*#.*$"
```

If a roster skill cannot be located exactly once, emit error `unresolved_roster_skill`; do not invent a block.

- [ ] **Step 4: Parse explicit tree-tier and tree-weapon rules**

Recognize explicit forms such as:

```text
- Tier IV: Level 180
- Tier I: None
Lvl req, T1 none, T2 lv60, T3 lv120
```

Store `(tier, level_or_none)` pairs. For Minstrel, parse the explicit sentence beginning `All Minstrel skills are limited to` into tree `weapon_restrictions`, while retaining the original sentence in `general_text`.

- [ ] **Step 5: Add whole-corpus dispatch test**

```python
def test_every_real_source_has_registered_deterministic_strategy(self):
    parsed = [parse_skill_file(s) for s in discover_skill_sources(ROOT / "raw_skills")]
    unhandled = [
        result.tree.source_file
        for result in parsed
        if any(issue.code == "unsupported_source_format" for issue in result.issues)
    ]
    self.assertEqual(unhandled, [])
```

Any nonstandard source exposed by this test must receive an explicit deterministic strategy before Task 3 is accepted.

- [ ] **Step 6: Run focused + full regression tests**

```bash
python -m unittest tests.test_skill_parsing -v
python -m unittest discover -s tests -v
```

- [ ] **Step 7: Commit**

```bash
git add toram_skills/parsing.py tests/test_skill_parsing.py
git commit -m "feat: parse nonstandard skill sources"
```

---

### Task 4: Conservative Field Normalization and Uncertainty Marking

**Files:**
- Modify: `toram_skills/parsing.py`
- Create: `tests/test_skill_field_normalization.py`

**Interfaces:**
- Populates existing `SkillDraft` fields only from explicit source labels/patterns.
- Populates `aliases` only from explicit alternate-name/alias source text; otherwise leaves it empty.
- Emits warning code `source_uncertainty` when source text explicitly marks a mechanic/formula as uncertain.

- [ ] **Step 1: Write failing representative tests**

```python
class SkillFieldNormalizationTests(unittest.TestCase):
    def test_magic_finale_common_fields(self):
        finale = self._skill("weapon_class_skills/magic_skills.txt", "MAGIC: FINALE")
        self.assertEqual(finale.tier, 4)
        self.assertEqual(finale.required_level, 150)
        self.assertEqual(finale.mp_cost_value, 1600)
        self.assertEqual(finale.damage_type, "Magic")
        self.assertEqual(finale.cast_range_text, "12m")

    def test_assassin_stab_legacy_explicit_lines(self):
        stab = self._skill("sub_weapon_skills/assassin_skills.txt", "ASSASSIN STAB")
        self.assertEqual(stab.tier, 1)
        self.assertEqual(stab.required_level, 15)
        self.assertEqual(stab.mp_cost_value, 300)
        self.assertEqual(stab.skill_type, "Active")
        self.assertEqual(stab.damage_type, "Physical")
        self.assertIn("Skill multiplier varies", stab.raw_text)

    def test_expression_mp_cost_stays_text_only(self):
        strike = self._skill("sub_weapon_skills/assassin_skills.txt", "ARCANE STRIKE")
        self.assertIsNone(strike.mp_cost_value)
        self.assertIn("remaining MP", strike.mp_cost_text)
```

Add an Alchemy assertion that `Full effect is unknown in the source.` remains in source text and produces a `source_uncertainty` warning.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_field_normalization -v
```

- [ ] **Step 3: Implement exact-label scalar normalization**

```python
_FIELD_LABELS = {
    "mp cost": "mp_cost",
    "damage type": "damage_type",
    "maximum cast range": "cast_range",
    "action range": "cast_range",
    "hit range": "hit_range",
    "base cast time": "cast_time",
    "hit count": "hit_count",
    "description": "description",
    "game description": "game_description",
    "element": "element",
    "ailment": "ailment",
    "limitation": "limitation",
}


def _plain_int(text: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*", text)
    return int(match.group(1)) if match else None


def _level(text: str) -> int | None:
    match = re.fullmatch(r"\s*(?:Level\s*)?(\d+)\s*", text, re.IGNORECASE)
    return int(match.group(1)) if match else None
```

`MP Cost: 1600` stores both text and integer. `100+remaining MP from MPbar` stores text only.

- [ ] **Step 4: Implement explicit legacy type parsing**

```python
_LEGACY_TYPE_RE = re.compile(
    r"(?i)^(?P<kind>Active|Passive|Support)\s+skill(?:\s*\((?P<detail>[^)]+)\))?"
)
```

`Active skill(physical)` -> `skill_type="Active"`, `damage_type="Physical"`; `Passive skill` -> `skill_type="Passive"`. Never infer type from prose.

- [ ] **Step 5: Normalize explicit ailments and weapon requirements**

- `Ailment: Tumble` -> `("Tumble",)`.
- For semicolon-separated explicit ailment entries, store each ailment name before its parenthetical condition; preserve the full original line in sections/raw text.
- `Limitation: Dagger/Scroll Only` -> `weapon_requirements=("Dagger", "Scroll")` only when the source explicitly says `only`.
- Explicit weapon bonus/penalty lines remain sections in Milestone 1; do not turn their mechanics into guessed structured numbers.

- [ ] **Step 6: Mark explicit uncertainty conservatively**

Use a narrow case-insensitive phrase set found in source commentary:

```python
_UNCERTAINTY_PHRASES = (
    "unknown in the source",
    "not sure",
    "expected formula",
    "trial and error",
    "could be",
    "idk",
)
```

If a block contains one of these phrases, append one warning `source_uncertainty` for that skill. The warning does not alter the source text or numeric fields already explicitly stated.

- [ ] **Step 7: Run normalization + parser tests**

```bash
python -m unittest tests.test_skill_field_normalization -v
python -m unittest tests.test_skill_parsing -v
```

- [ ] **Step 8: Commit**

```bash
git add toram_skills/parsing.py tests/test_skill_field_normalization.py
git commit -m "feat: normalize canonical skill fields"
```

---

### Task 5: SQLite Schema, Repository, and Transactional Import

**Files:**
- Create: `toram_skills/schema.py`
- Create: `toram_skills/repository.py`
- Create: `toram_skills/importer.py`
- Create: `tests/test_skill_repository.py`
- Create: `tests/test_skill_importer.py`
- Modify: `toram_skills/__init__.py`

**Interfaces:**
- `create_schema(connection)`, `verify_schema(connection)`.
- `SkillRepository(database_path)` with `close`, context-manager support, `count_trees`, `count_skills`, `get_skill`, `get_skill_by_name`, `list_tree_names`.
- `import_skill_corpus(raw_root: Path, database_path: Path) -> ImportReport` using the Task 2 `ImportReport` type.

- [ ] **Step 1: Write failing schema/repository tests**

Required tables:

```python
REQUIRED_TABLES = {
    "metadata",
    "skill_trees",
    "skills",
    "skill_aliases",
    "skill_sections",
    "skill_ailments",
    "skill_weapon_requirements",
    "skill_weapon_restrictions",
}
```

Test that a missing-table database raises `SchemaError`, and that a small canonical record round-trips without changing `raw_text`, aliases, sections, or issues.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_repository -v
```

- [ ] **Step 3: Implement the exact schema**

```sql
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE skill_trees (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    tree_group TEXT NOT NULL,
    source_file TEXT NOT NULL UNIQUE,
    general_text TEXT NOT NULL,
    tier_requirements_json TEXT NOT NULL,
    weapon_restrictions_json TEXT NOT NULL,
    issues_json TEXT NOT NULL
);

CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    tree_id TEXT NOT NULL REFERENCES skill_trees(id) ON DELETE CASCADE,
    source_order INTEGER NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    tier INTEGER,
    required_level INTEGER,
    skill_type TEXT,
    mp_cost_text TEXT,
    mp_cost_value INTEGER,
    damage_type TEXT,
    element TEXT,
    cast_range_text TEXT,
    hit_range_text TEXT,
    cast_time_text TEXT,
    hit_count_text TEXT,
    description TEXT,
    game_description TEXT,
    raw_text TEXT NOT NULL,
    issues_json TEXT NOT NULL,
    UNIQUE(tree_id, normalized_name),
    UNIQUE(tree_id, source_order)
);

CREATE TABLE skill_aliases (
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    PRIMARY KEY(skill_id, position)
);

CREATE TABLE skill_sections (
    id INTEGER PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    normalized_label TEXT NOT NULL,
    body TEXT NOT NULL,
    UNIQUE(skill_id, position)
);

CREATE TABLE skill_ailments (
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY(skill_id, position)
);

CREATE TABLE skill_weapon_requirements (
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    weapon TEXT NOT NULL,
    PRIMARY KEY(skill_id, position)
);

CREATE TABLE skill_weapon_restrictions (
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    weapon TEXT NOT NULL,
    PRIMARY KEY(skill_id, position)
);
```

Enable foreign keys and verify required columns with `PRAGMA table_info`, following the existing repository's schema-checking style.

- [ ] **Step 4: Implement lossless repository serialization**

Serialize tuple metadata as compact UTF-8 JSON with `ensure_ascii=False` and `separators=(",", ":")`. Reconstruct canonical dataclasses exactly. Repository code must not add search/fuzzy behavior.

- [ ] **Step 5: Write failing transactional importer tests**

```python
def test_import_builds_database_and_manifest_metadata(self):
    report = import_skill_corpus(raw_root, db_path)
    self.assertTrue(report.is_valid)
    self.assertEqual(report.manifest_hash, source_manifest_hash(discover_skill_sources(raw_root)))
    with SkillRepository(db_path) as repo:
        self.assertGreater(repo.count_trees(), 0)
        self.assertGreater(repo.count_skills(), 0)


def test_failed_import_does_not_replace_existing_database(self):
    db_path.write_bytes(b"keep-me")
    report = import_skill_corpus(bad_raw_root, db_path)
    self.assertFalse(report.is_valid)
    self.assertEqual(db_path.read_bytes(), b"keep-me")
```

- [ ] **Step 6: Implement whole-corpus transactional import**

The importer must execute in this order:

1. discover all sources;
2. parse every source;
3. aggregate all issues and discovered-block counts;
4. return an invalid `ImportReport` without touching the target if any error exists;
5. otherwise create a temporary SQLite file in the target directory;
6. write all canonical rows in one transaction;
7. write metadata `schema_version=1`, `source_manifest_hash=<sha256>`, `source_file_count=<count>`;
8. verify schema and row counts;
9. close SQLite and call `os.replace(temp_path, database_path)`;
10. remove the temporary file on any exception.

Use `tempfile.NamedTemporaryFile(delete=False, dir=database_path.parent, suffix=".sqlite")` and close it before SQLite opens it.

- [ ] **Step 7: Run focused tests**

```bash
python -m unittest tests.test_skill_repository -v
python -m unittest tests.test_skill_importer -v
```

- [ ] **Step 8: Commit**

```bash
git add toram_skills/schema.py toram_skills/repository.py toram_skills/importer.py toram_skills/__init__.py tests/test_skill_repository.py tests/test_skill_importer.py
git commit -m "feat: build canonical skill database"
```

---

### Task 6: Validation Reports, Build CLI, and Full-Corpus Acceptance

**Files:**
- Create: `toram_skills/report.py`
- Create: `build_skills.py`
- Modify: `tests/test_skill_importer.py`
- Modify: `tests/test_core_module_boundaries.py`
- Generate after tests pass: `coryn_data/database/skills.sqlite`

**Interfaces:**
- `render_import_report(report: ImportReport) -> str`.
- `report_to_json(report: ImportReport) -> str`.
- CLI defaults: `--source raw_skills`, `--database coryn_data/database/skills.sqlite`, optional `--json-report PATH`.
- CLI returns `0` for valid import and `1` for import errors.

- [ ] **Step 1: Write the failing real-corpus acceptance test**

```python
def test_real_corpus_import_has_zero_errors_and_accounts_for_every_block(self):
    report = import_skill_corpus(ROOT / "raw_skills", db_path)
    self.assertEqual(report.errors, ())
    self.assertEqual(report.files_discovered, report.trees_created)
    self.assertEqual(report.skill_blocks_discovered, report.skills_created)

    with SkillRepository(db_path) as repo:
        self.assertEqual(repo.count_trees(), report.trees_created)
        self.assertEqual(repo.count_skills(), report.skills_created)
```

Add representative round-trip assertions:

```python
expected = (
    ("weapon_class_skills/magic_skills/magic-finale", "MAGIC: FINALE", 1600),
    ("sub_weapon_skills/assassin_skills/assassin-stab", "ASSASSIN STAB", 300),
    ("assist_skills/minstrel_skills/healing-song", "Healing Song", 100),
    ("other_skill_trees/alchemist_skills/process-material", "PROCESS MATERIAL", None),
)
for skill_id, name, mp_cost in expected:
    skill = repo.get_skill(skill_id)
    self.assertEqual(skill.name, name)
    self.assertEqual(skill.mp_cost_value, mp_cost)
    self.assertTrue(skill.raw_text.strip())
```

- [ ] **Step 2: Verify RED or explicit parser gaps**

```bash
python -m unittest tests.test_skill_importer -v
```

No file or block may disappear silently: any unresolved source must appear as an error issue.

- [ ] **Step 3: Implement deterministic report rendering**

Human format:

```text
Skill import report
Files discovered: N
Trees created: N
Skill blocks discovered: N
Skills created: N
Errors: N
Warnings: N
Manifest: <sha256>

ERROR <code> <source_file> [<skill_name>]: <message>
WARNING <code> <source_file> [<skill_name>]: <message>
```

JSON contains the same counters and issue dictionaries. Sort issues by `(source_file, skill_name or "", code, message)`.

- [ ] **Step 4: Implement the thin CLI**

```python
parser.add_argument("--source", type=Path, default=Path("raw_skills"))
parser.add_argument("--database", type=Path, default=Path("coryn_data/database/skills.sqlite"))
parser.add_argument("--json-report", type=Path)

report = import_skill_corpus(args.source, args.database)
print(render_import_report(report))
if args.json_report is not None:
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(report_to_json(report), encoding="utf-8")
return 0 if report.is_valid else 1
```

`build_skills.py` must not import Discord, Ollama, `toram_search`, or item-search entrypoints.

- [ ] **Step 5: Add module-boundary regression**

Extend `tests/test_core_module_boundaries.py` to AST-scan `toram_skills/*.py` and reject imports of `search_items`, `discord_bot`, `toram_discord`, `toram_search`, or `ollama`.

- [ ] **Step 6: Run the real build**

```bash
python build_skills.py \
  --source raw_skills \
  --database coryn_data/database/skills.sqlite \
  --json-report /tmp/skills-import-report.json
```

Acceptance requirements:

- exit `0`;
- `Errors: 0`;
- every discovered file creates one tree;
- every discovered skill block creates one skill record;
- warnings are allowed only for preserved source uncertainty/nonfatal normalization warnings;
- metadata manifest equals `source_manifest_hash(discover_skill_sources(Path("raw_skills")))`.

- [ ] **Step 7: Run fresh final verification**

```bash
python -m compileall -q toram_skills build_skills.py
python -m unittest tests.test_skill_source_inventory -v
python -m unittest tests.test_skill_parsing -v
python -m unittest tests.test_skill_field_normalization -v
python -m unittest tests.test_skill_repository -v
python -m unittest tests.test_skill_importer -v
python -m unittest tests.test_core_module_boundaries -v
python -m unittest discover -s tests -v
```

Expected: compile and every focused/full test pass with zero failures/errors.

- [ ] **Step 8: Verify database provenance before commit**

Open the generated DB through `SkillRepository`; read `source_manifest_hash` metadata; compare it to the current raw-source manifest. Do not commit a stale database.

- [ ] **Step 9: Commit final milestone output**

```bash
git add toram_skills/report.py build_skills.py tests/test_skill_importer.py tests/test_core_module_boundaries.py coryn_data/database/skills.sqlite
git commit -m "feat: validate canonical skill corpus"
```

---

## Milestone 1 Completion Criteria

Milestone 1 is complete only when:

- every `.txt` file in the four supported raw groups is discovered;
- every source file maps to exactly one canonical tree;
- every source-defined skill block maps to exactly one skill or an explicit import error;
- the accepted full-corpus import has zero errors;
- raw tree text and raw skill text survive SQLite round-trip;
- aliases have a canonical storage path even when a skill has none;
- representative Battle, Magic, Assassin, Minstrel, and Alchemy facts are source-backed by tests;
- explicitly uncertain source statements emit `source_uncertainty` and remain raw/preserved;
- `skills.sqlite` manifest matches the current raw corpus;
- a failed import cannot overwrite a valid database;
- no item, Qwen, Discord, FTS, vector, Gemma, or formula-evaluation behavior changes;
- the full repository test suite stays green.

After this milestone is accepted, write a separate implementation plan for structured lookup + FTS5 + semantic-model benchmark + deterministic hybrid fusion.