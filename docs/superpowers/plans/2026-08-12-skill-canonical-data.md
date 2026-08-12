# Canonical Skill Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministically convert the complete `raw_skills/` corpus into a validated, lossless canonical `skills.sqlite` database that future hybrid retrieval can trust.

**Architecture:** Add a new `toram_skills` package that owns source discovery, canonical skill/tree models, deterministic parsing, validation, SQLite schema/repository, and corpus import. The importer preserves every skill block and tree-level source text even when a field cannot be normalized, and it replaces the generated database only after a complete zero-error import. Existing item/search/Discord code remains untouched in this milestone.

**Tech Stack:** Python >=3.12, standard-library `dataclasses`, `pathlib`, `re`, `json`, `hashlib`, `sqlite3`, `tempfile`, and the repository's existing `unittest` test style. No new dependency and no LLM call.

## Global Constraints

- Source corpus: `raw_skills/assist_skills/`, `raw_skills/other_skill_trees/`, `raw_skills/sub_weapon_skills/`, `raw_skills/weapon_class_skills/`.
- Generated database: `coryn_data/database/skills.sqlite`, separate from `items.sqlite`.
- `raw_skills/` remains the editable source of truth; generated data must be reproducible.
- No LLM is used to discover, split, normalize, validate, or import source data.
- Unrecognized or uncertain source content must remain available in canonical text sections and/or `raw_text`.
- Do not infer tier, damage type, ailment, weapon restrictions, or other game facts from general Toram knowledge.
- Duplicate normalized skill names are allowed across different trees but are import errors within the same tree.
- Tree IDs are stable from relative source paths; skill IDs are stable from tree ID + normalized skill name.
- This plan intentionally excludes FTS5, embeddings, hybrid ranking, `gemma4:e4b`, formula evaluation, skill query routing, and Discord integration.
- Existing item search, Qwen behavior, item database, and Discord behavior must not change.

---

## File Structure Locked by This Plan

Create this package:

```text
toram_skills/
    __init__.py          # public canonical-data exports only
    source_inventory.py  # discover/classify raw source files
    models.py            # immutable canonical drafts/results/issues
    parsing.py           # common marker parser + Minstrel parser + field normalization
    schema.py            # SQLite DDL and schema verification constants
    repository.py        # read/write canonical records, no search behavior
    importer.py          # transaction-safe whole-corpus build
    report.py            # JSON + human import report rendering

build_skills.py          # thin CLI around importer
```

Create focused tests:

```text
tests/test_skill_source_inventory.py
tests/test_skill_parsing.py
tests/test_skill_field_normalization.py
tests/test_skill_repository.py
tests/test_skill_importer.py
```

Do not add skill code to `toram_data`, `toram_search`, or `toram_discord` during this milestone.

---

### Task 1: Discover and Audit the Raw Skill Corpus

**Files:**
- Create: `toram_skills/__init__.py`
- Create: `toram_skills/source_inventory.py`
- Create: `tests/test_skill_source_inventory.py`

**Interfaces:**
- Consumes: repository root containing `raw_skills/`.
- Produces:
  - `SkillSource(path: Path, relative_path: str, tree_group: str, text: str, declared_category: str | None, marker_count: int)`
  - `discover_skill_sources(raw_root: Path) -> tuple[SkillSource, ...]`
  - `source_manifest_hash(sources: tuple[SkillSource, ...]) -> str`

- [ ] **Step 1: Write failing source-discovery tests**

```python
from pathlib import Path
import tempfile
import unittest

from toram_skills.source_inventory import discover_skill_sources, source_manifest_hash


class SkillSourceInventoryTests(unittest.TestCase):
    def test_discovers_txt_files_in_supported_groups_in_stable_order(self):
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

    def test_rejects_files_outside_known_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "unknown").mkdir()
            (root / "unknown" / "x.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported raw skill group"):
                discover_skill_sources(root)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python -m unittest tests.test_skill_source_inventory -v
```

Expected: import failure because `toram_skills.source_inventory` does not exist.

- [ ] **Step 3: Implement immutable source records and deterministic discovery**

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
    discovered: list[SkillSource] = []
    for path in sorted(root.rglob("*.txt")):
        relative = path.relative_to(root).as_posix()
        top = Path(relative).parts[0]
        if top not in _GROUPS:
            raise ValueError(f"Unsupported raw skill group: {top}")
        text = path.read_text(encoding="utf-8")
        category_match = _CATEGORY_RE.search(text)
        discovered.append(SkillSource(
            path=path,
            relative_path=relative,
            tree_group=_GROUPS[top],
            text=text,
            declared_category=category_match.group(1).strip() if category_match else None,
            marker_count=len(_SKILL_MARKER_RE.findall(text)),
        ))
    return tuple(discovered)


def source_manifest_hash(sources: tuple[SkillSource, ...]) -> str:
    digest = sha256()
    for source in sources:
        digest.update(source.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
```

`toram_skills/__init__.py` initially re-exports only these public source interfaces.

- [ ] **Step 4: Add a real-corpus inventory assertion**

Add this test using the repository source tree:

```python
def test_real_corpus_is_nonempty_and_every_file_is_classified(self):
    raw_root = Path(__file__).resolve().parents[1] / "raw_skills"
    sources = discover_skill_sources(raw_root)
    self.assertGreater(len(sources), 0)
    self.assertTrue(all(source.text.strip() for source in sources))
    self.assertEqual(
        {source.tree_group for source in sources},
        {"assist", "other", "sub-weapon", "weapon-class"},
    )
```

- [ ] **Step 5: Run Task 1 tests and full existing suite**

```bash
python -m unittest tests.test_skill_source_inventory -v
python -m unittest discover -s tests -v
```

Expected: all tests pass; no existing item/Discord test changes.

- [ ] **Step 6: Commit Task 1**

```bash
git add toram_skills/__init__.py toram_skills/source_inventory.py tests/test_skill_source_inventory.py
git commit -m "feat: inventory raw skill sources"
```

---

### Task 2: Define Canonical Models and Parse Standard `SKILL:` Blocks

**Files:**
- Create: `toram_skills/models.py`
- Create: `toram_skills/parsing.py`
- Create: `tests/test_skill_parsing.py`
- Modify: `toram_skills/__init__.py`

**Interfaces:**
- Consumes: `SkillSource` from Task 1.
- Produces:
  - `ParseIssue(level, code, source_file, skill_name, message)`
  - `SkillSection(position, label, normalized_label, body)`
  - `SkillTreeDraft(id, name, normalized_name, tree_group, source_file, general_text, tier_requirements, weapon_restrictions, issues)`
  - `SkillDraft(id, tree_id, source_order, name, normalized_name, tier, required_level, skill_type, mp_cost_text, mp_cost_value, damage_type, element, cast_range_text, hit_range_text, cast_time_text, hit_count_text, ailments, weapon_requirements, weapon_restrictions, sections, description, game_description, raw_text, issues)`
  - `ParsedSkillFile(tree: SkillTreeDraft, skills: tuple[SkillDraft, ...], issues: tuple[ParseIssue, ...])`
  - `normalize_skill_name(text: str) -> str`
  - `skill_tree_id(relative_path: str) -> str`
  - `skill_id(tree_id: str, skill_name: str) -> str`
  - `parse_standard_skill_file(source: SkillSource) -> ParsedSkillFile`

- [ ] **Step 1: Write failing model and standard-parser tests**

Use the real Battle and Magic files as representative structured sources:

```python
from pathlib import Path
import unittest

from toram_skills.source_inventory import discover_skill_sources
from toram_skills.parsing import parse_standard_skill_file


ROOT = Path(__file__).resolve().parents[1]


class SkillParsingTests(unittest.TestCase):
    def _source(self, suffix: str):
        sources = discover_skill_sources(ROOT / "raw_skills")
        return next(source for source in sources if source.relative_path == suffix)

    def test_battle_skills_split_into_named_records_and_preserve_raw_blocks(self):
        parsed = parse_standard_skill_file(self._source("assist_skills/battle_skills.txt"))
        names = [skill.name for skill in parsed.skills]
        self.assertIn("MAGIC UP", names)
        magic_up = next(skill for skill in parsed.skills if skill.name == "MAGIC UP")
        self.assertEqual(magic_up.tree_id, "assist_skills/battle_skills")
        self.assertIn("MATK Increase", magic_up.raw_text)
        self.assertTrue(parsed.tree.general_text.startswith("Category: Battle Skills"))

    def test_magic_file_preserves_source_details_as_section_text(self):
        parsed = parse_standard_skill_file(self._source("weapon_class_skills/magic_skills.txt"))
        arrows = next(skill for skill in parsed.skills if skill.name == "MAGIC: ARROWS")
        labels = [section.normalized_label for section in arrows.sections]
        self.assertIn("source details", labels)
        self.assertIn("Base Skill Multiplier", arrows.raw_text)
```

- [ ] **Step 2: Run the tests and verify RED**

```bash
python -m unittest tests.test_skill_parsing -v
```

Expected: import failure for missing `toram_skills.models` / `toram_skills.parsing`.

- [ ] **Step 3: Implement canonical immutable dataclasses and stable IDs**

The model definitions use tuples, not mutable lists, so parsed records cannot be accidentally mutated after validation. Stable IDs follow these exact rules:

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

Define `ParseIssue.level` as `Literal["error", "warning"]`. Define `tier_requirements` as `tuple[tuple[int, int | None], ...]` and weapon lists/ailments as `tuple[str, ...]`.

- [ ] **Step 4: Implement standard block splitting without field inference**

Use the source marker as the only standard block boundary:

```python
_SKILL_BLOCK_RE = re.compile(
    r"(?ms)^=+\s*\nSKILL:\s*(?P<name>[^\n]+)\s*\n=+\s*\n(?P<body>.*?)(?=^=+\s*\nSKILL:|\Z)"
)


def _split_standard_blocks(text: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    matches = list(_SKILL_BLOCK_RE.finditer(text))
    if not matches:
        return text, ()
    general_text = text[:matches[0].start()].rstrip()
    blocks = tuple((m.group("name").strip(), m.group(0).strip()) for m in matches)
    return general_text, blocks
```

For each block, create a `SkillDraft` with identity/raw fields populated and normalized fields left `None`/empty until Task 4. Extract named sections conservatively from standalone `Label:` lines while retaining their original body text; the entire block always remains in `raw_text`.

- [ ] **Step 5: Reject duplicate normalized names within one tree**

Add a parser test with two same-tree blocks named `TEST SKILL` / `Test Skill`. `parse_standard_skill_file` must include an error issue with code `duplicate_skill_name` and must not silently overwrite one record.

- [ ] **Step 6: Run focused tests**

```bash
python -m unittest tests.test_skill_parsing -v
```

Expected: Battle and Magic records are split correctly, raw text is preserved, duplicate names are reported.

- [ ] **Step 7: Commit Task 2**

```bash
git add toram_skills/models.py toram_skills/parsing.py toram_skills/__init__.py tests/test_skill_parsing.py
git commit -m "feat: parse canonical skill blocks"
```

---

### Task 3: Parse Minstrel's Nonstandard Format and Tree-Level Rules

**Files:**
- Modify: `toram_skills/parsing.py`
- Modify: `tests/test_skill_parsing.py`

**Interfaces:**
- Consumes: `SkillSource`, canonical models from Tasks 1-2.
- Produces:
  - `parse_skill_file(source: SkillSource) -> ParsedSkillFile`
  - standard `SKILL:` files route to `parse_standard_skill_file`
  - `assist_skills/minstrel_skills.txt` routes to a deterministic roster-based parser

- [ ] **Step 1: Add failing Minstrel tests**

```python
def test_ministrel_file_uses_embedded_tier_roster_to_split_skills(self):
    parsed = parse_skill_file(self._source("assist_skills/minstrel_skills.txt"))
    names = [skill.name for skill in parsed.skills]
    self.assertIn("Healing Song", names)
    self.assertIn("Beat Blast", names)
    self.assertIn("Battle Anthem", names)
    healing = next(skill for skill in parsed.skills if skill.name == "Healing Song")
    anthem = next(skill for skill in parsed.skills if skill.name == "Battle Anthem")
    self.assertEqual(healing.tier, 1)
    self.assertEqual(anthem.tier, 3)
    self.assertIn("Acoustic Buff", parsed.tree.general_text)
    self.assertIn("EXP Gain", healing.raw_text)
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest tests.test_skill_parsing.SkillParsingTests.test_ministrel_file_uses_embedded_tier_roster_to_split_skills -v
```

Expected: missing `parse_skill_file` or no parsed Minstrel skills.

- [ ] **Step 3: Implement a source-derived Minstrel roster parser**

Do not hardcode skill names. Parse the final roster beginning with `Lvl req,` and `Tier I`/`Tier II`/`Tier III`. Convert Roman tier headings with:

```python
_ROMAN_TIERS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
```

Build an ordered `(skill_name, tier)` roster from lines beneath each tier heading. For every roster skill, locate its first standalone body heading before the final roster and bound the block by its corresponding trailing line matching:

```python
rf"(?mi)^{re.escape(skill_name)}\s*\|\s*#.*$"
```

The tree's `general_text` is everything before the first skill body. If a roster skill cannot be located exactly once, emit an `error` issue `unresolved_roster_skill`; do not fabricate a block.

- [ ] **Step 4: Add deterministic tree-level extraction**

From general text, extract only explicit tier requirement lines matching forms such as:

```text
- Tier IV: Level 180
- Tier I: None
Lvl req, T1 none, T2 lv60, T3 lv120
```

Store `(tier, required_level_or_none)` pairs on `SkillTreeDraft.tier_requirements`. For Minstrel, also parse the explicit tree-wide weapon restriction sentence beginning `All Minstrel skills are limited to` into a tuple of source spellings; retain the sentence in `general_text` regardless.

- [ ] **Step 5: Add corpus dispatch safety test**

```python
def test_every_real_source_is_parseable_by_registered_strategy(self):
    sources = discover_skill_sources(ROOT / "raw_skills")
    parsed = [parse_skill_file(source) for source in sources]
    self.assertEqual(len(parsed), len(sources))
    unhandled = [
        result.tree.source_file
        for result in parsed
        if any(issue.code == "unsupported_source_format" for issue in result.issues)
    ]
    self.assertEqual(unhandled, [])
```

`parse_skill_file` may use standard marker parsing for any file with marker blocks. Only source formats proven by the corpus audit to lack usable markers receive an explicit path-based parser. Unknown future unmarked files return `unsupported_source_format` rather than guessed records.

- [ ] **Step 6: Run parsing tests and full suite**

```bash
python -m unittest tests.test_skill_parsing -v
python -m unittest discover -s tests -v
```

Expected: all current raw source files are handled by a deterministic registered strategy.

- [ ] **Step 7: Commit Task 3**

```bash
git add toram_skills/parsing.py tests/test_skill_parsing.py
git commit -m "feat: parse nonstandard skill sources"
```

---

### Task 4: Conservatively Normalize High-Value Skill Fields

**Files:**
- Modify: `toram_skills/parsing.py`
- Create: `tests/test_skill_field_normalization.py`

**Interfaces:**
- Consumes: parsed raw skill blocks from Tasks 2-3.
- Produces normalized fields already declared on `SkillDraft`; no new database/search API.

- [ ] **Step 1: Add failing representative normalization tests**

Cover structured, rich, and free-form records:

```python
class SkillFieldNormalizationTests(unittest.TestCase):
    def test_magic_finale_normalizes_explicit_common_fields(self):
        finale = self._skill("weapon_class_skills/magic_skills.txt", "MAGIC: FINALE")
        self.assertEqual(finale.tier, 4)
        self.assertEqual(finale.required_level, 150)
        self.assertEqual(finale.mp_cost_value, 1600)
        self.assertEqual(finale.damage_type, "Magic")
        self.assertEqual(finale.cast_range_text, "12m")

    def test_assassin_stab_normalizes_explicit_legacy_lines_without_losing_source(self):
        stab = self._skill("sub_weapon_skills/assassin_skills.txt", "ASSASSIN STAB")
        self.assertEqual(stab.tier, 1)
        self.assertEqual(stab.required_level, 15)
        self.assertEqual(stab.mp_cost_value, 300)
        self.assertEqual(stab.skill_type, "Active")
        self.assertEqual(stab.damage_type, "Physical")
        self.assertIn("Skill multiplier varies", stab.raw_text)

    def test_uncertain_or_expression_mp_cost_remains_text_only(self):
        strike = self._skill("sub_weapon_skills/assassin_skills.txt", "ARCANE STRIKE")
        self.assertIsNone(strike.mp_cost_value)
        self.assertIn("remaining MP", strike.mp_cost_text)
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest tests.test_skill_field_normalization -v
```

Expected: fields are currently `None`/empty.

- [ ] **Step 3: Implement label normalization and safe scalar parsing**

Use exact label aliases, not semantic guessing:

```python
_LABELS = {
    "mp cost": "mp_cost",
    "mp cost ": "mp_cost",
    "damage type": "damage_type",
    "maximum cast range": "cast_range",
    "action range": "cast_range",
    "hit range": "hit_range",
    "base cast time": "cast_time",
    "hit count": "hit_count",
    "description": "description",
    "game description": "game_description",
    "game description ": "game_description",
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

For `MP Cost: 1600`, store `mp_cost_text="1600"` and `mp_cost_value=1600`. For expressions such as `100+remaining MP from MPbar`, keep the text and leave `mp_cost_value=None`.

- [ ] **Step 4: Implement explicit legacy type-line parsing**

Support only explicit patterns present in source, including:

```python
_LEGACY_TYPE_RE = re.compile(
    r"(?i)^(?P<kind>Active|Passive|Support)\s+skill(?:\s*\((?P<detail>[^)]+)\))?"
)
```

Map `Active skill(physical)` to `skill_type="Active"`, `damage_type="Physical"`; map `Passive skill` to `skill_type="Passive"`. Do not infer type from a prose description.

- [ ] **Step 5: Normalize explicit ailments and weapon rules conservatively**

- `Ailment: Tumble` -> `ailments=("Tumble",)`.
- Semicolon-separated explicit ailments remain individual source-spelling values.
- `Limitation: Dagger/Scroll Only` -> `weapon_requirements=("Dagger", "Scroll")` only when the phrase ends in `only`.
- Lines named `Staff bonus`, `Magic Device bonus`, `Dagger/Scroll bonus`, and equivalent explicit weapon bonus/penalty labels remain named sections in this milestone; they are not converted into numeric mechanics.
- Tree-wide weapon restrictions from Task 3 remain on the tree record.

- [ ] **Step 6: Ensure unknown sections and uncertainty remain lossless**

Add assertions that the Assassin source's informal notes and Alchemy's `Synthesis Success Effect: Full effect is unknown in the source.` are still present in `raw_text`/sections and are never converted to invented numeric fields.

- [ ] **Step 7: Run focused tests and all parsing tests**

```bash
python -m unittest tests.test_skill_field_normalization -v
python -m unittest tests.test_skill_parsing -v
```

Expected: representative normalized facts pass and source text remains intact.

- [ ] **Step 8: Commit Task 4**

```bash
git add toram_skills/parsing.py tests/test_skill_field_normalization.py
git commit -m "feat: normalize canonical skill fields"
```

---

### Task 5: Add `skills.sqlite`, Repository, and Transactional Corpus Import

**Files:**
- Create: `toram_skills/schema.py`
- Create: `toram_skills/repository.py`
- Create: `toram_skills/importer.py`
- Create: `tests/test_skill_repository.py`
- Create: `tests/test_skill_importer.py`
- Modify: `toram_skills/__init__.py`

**Interfaces:**
- Consumes: `ParsedSkillFile` records from `parse_skill_file`.
- Produces:
  - `create_schema(connection: sqlite3.Connection) -> None`
  - `verify_schema(connection: sqlite3.Connection) -> None`
  - `SkillRepository(database_path: Path)`
  - `SkillRepository.count_trees() -> int`
  - `SkillRepository.count_skills() -> int`
  - `SkillRepository.get_skill(skill_id: str) -> SkillDraft`
  - `SkillRepository.get_skill_by_name(tree_id: str, normalized_name: str) -> SkillDraft | None`
  - `SkillRepository.list_tree_names() -> tuple[str, ...]`
  - `import_skill_corpus(raw_root: Path, database_path: Path) -> ImportReport`

- [ ] **Step 1: Write failing schema/repository tests**

Use a temporary SQLite file and create exact required tables:

```python
REQUIRED_TABLES = {
    "metadata",
    "skill_trees",
    "skills",
    "skill_sections",
    "skill_ailments",
    "skill_weapon_requirements",
    "skill_weapon_restrictions",
}
```

Test that `SkillRepository` refuses a database missing required tables and can round-trip a small parsed Battle fixture once schema/write methods exist.

- [ ] **Step 2: Run repository tests and verify RED**

```bash
python -m unittest tests.test_skill_repository -v
```

Expected: missing schema/repository imports.

- [ ] **Step 3: Implement the exact SQLite schema**

Use this structure:

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

Enable foreign keys and verify required columns with `PRAGMA table_info`, following the existing repository's schema-verification style.

- [ ] **Step 4: Implement repository serialization/round-trip**

Serialize tuple/list metadata as compact UTF-8 JSON (`ensure_ascii=False`, `separators=(",", ":")`). Reconstruct `SkillSection`, `ParseIssue`, `SkillDraft`, and tree data without altering source text. Repository code must not add fuzzy matching or search behavior in this milestone.

- [ ] **Step 5: Write failing transactional importer tests**

```python
def test_import_builds_complete_database_and_records_manifest_hash(self):
    report = import_skill_corpus(raw_root, db_path)
    self.assertTrue(report.is_valid)
    with SkillRepository(db_path) as repo:
        self.assertGreater(repo.count_trees(), 0)
        self.assertGreater(repo.count_skills(), 0)
    self.assertEqual(report.manifest_hash, source_manifest_hash(discover_skill_sources(raw_root)))


def test_failed_import_does_not_replace_existing_database(self):
    db_path.write_bytes(b"keep-me")
    report = import_skill_corpus(bad_raw_root, db_path)
    self.assertFalse(report.is_valid)
    self.assertEqual(db_path.read_bytes(), b"keep-me")
```

- [ ] **Step 6: Implement whole-corpus transactional import**

The importer must:

1. discover all sources;
2. parse every source;
3. aggregate issues;
4. refuse replacement when any error issue exists;
5. otherwise create a temporary SQLite file in the target directory;
6. write all trees/skills/sections/child rows in one transaction;
7. write metadata keys `schema_version=1`, `source_manifest_hash=<hash>`, and `source_file_count=<count>`;
8. `os.replace(temp_path, database_path)` only after commit + schema verification;
9. remove the temporary file on failure.

Use `tempfile.NamedTemporaryFile(delete=False, dir=database_path.parent, suffix=".sqlite")` and close it before SQLite opens the path.

- [ ] **Step 7: Run repository/importer tests**

```bash
python -m unittest tests.test_skill_repository -v
python -m unittest tests.test_skill_importer -v
```

Expected: round-trip source fidelity, schema checks, manifest metadata, and no replacement on failed import all pass.

- [ ] **Step 8: Commit Task 5**

```bash
git add toram_skills/schema.py toram_skills/repository.py toram_skills/importer.py toram_skills/__init__.py tests/test_skill_repository.py tests/test_skill_importer.py
git commit -m "feat: build canonical skill database"
```

---

### Task 6: Add Validation Reports, CLI Build, and Full-Corpus Acceptance Gate

**Files:**
- Create: `toram_skills/report.py`
- Create: `build_skills.py`
- Modify: `toram_skills/importer.py`
- Modify: `tests/test_skill_importer.py`
- Create/Generate after tests pass: `coryn_data/database/skills.sqlite`

**Interfaces:**
- Consumes: `ImportReport` from Task 5.
- Produces:
  - `ImportReport(files_discovered, trees_created, skill_blocks_discovered, skills_created, manifest_hash, issues)`
  - `ImportReport.errors`, `warnings`, `is_valid`, `error_codes`
  - `render_import_report(report: ImportReport) -> str`
  - `report_to_json(report: ImportReport) -> str`
  - CLI exit code `0` for valid import, `1` for import errors.

- [ ] **Step 1: Add failing report and full-corpus acceptance tests**

The acceptance test must parse/import the actual repository corpus into a temporary database and prove source accounting:

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

Also assert source-backed representative facts after round-trip:

```python
expected = (
    ("weapon_class_skills/magic_skills/magic-finale", "MAGIC: FINALE", 1600),
    ("sub_weapon_skills/assassin_skills/assassin-stab", "ASSASSIN STAB", 300),
    ("assist_skills/minstrel_skills/healing-song", "Healing Song", 100),
    ("other_skill_trees/alchemist_skills/process-material", "PROCESS MATERIAL", None),
)
for skill_id, name, mp in expected:
    skill = repo.get_skill(skill_id)
    self.assertEqual(skill.name, name)
    self.assertEqual(skill.mp_cost_value, mp)
    self.assertTrue(skill.raw_text.strip())
```

- [ ] **Step 2: Run the acceptance test and verify any remaining parser gaps are visible**

```bash
python -m unittest tests.test_skill_importer -v
```

Expected before final Task 6 implementation: report/rendering API is missing or acceptance exposes explicit error issues; there must be no silent skipped file/block.

- [ ] **Step 3: Implement report aggregation and rendering**

Human output format:

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

JSON output contains the same counters plus issue dictionaries in source order. Keep warning/error ordering deterministic by `(source_file, skill_name or "", code, message)`.

- [ ] **Step 4: Implement the thin build CLI**

`build_skills.py` uses `argparse` with these exact defaults:

```python
parser.add_argument("--source", type=Path, default=Path("raw_skills"))
parser.add_argument("--database", type=Path, default=Path("coryn_data/database/skills.sqlite"))
parser.add_argument("--json-report", type=Path)
```

Behavior:

```python
report = import_skill_corpus(args.source, args.database)
print(render_import_report(report))
if args.json_report is not None:
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(report_to_json(report), encoding="utf-8")
return 0 if report.is_valid else 1
```

No Discord/Ollama import is allowed in this CLI.

- [ ] **Step 5: Run the real build and inspect its report**

```bash
python build_skills.py \
  --source raw_skills \
  --database coryn_data/database/skills.sqlite \
  --json-report /tmp/skills-import-report.json
```

Acceptance requirements:

- exit code `0`;
- `Errors: 0`;
- every discovered file creates exactly one tree record;
- every discovered skill block creates exactly one skill record;
- warnings may exist only when they preserve/report source uncertainty or nonfatal unnormalized content;
- `skills.sqlite` passes `SkillRepository.verify_schema()` and representative record checks.

- [ ] **Step 6: Add a module-boundary regression**

Extend `tests/test_core_module_boundaries.py` so files under `toram_skills/` do not import `search_items`, `discord_bot`, `toram_discord`, or Ollama modules. This protects Milestone 1 as a pure data layer.

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

Expected: compile passes; all focused tests pass; full existing suite passes with zero failures/errors.

- [ ] **Step 8: Review generated database provenance before commit**

Open `skills.sqlite` through `SkillRepository` and assert metadata equals the current raw source manifest. Do not commit a database whose `source_manifest_hash` differs from `source_manifest_hash(discover_skill_sources(Path("raw_skills")))`.

- [ ] **Step 9: Commit Task 6 and generated canonical database**

```bash
git add toram_skills/report.py toram_skills/importer.py build_skills.py tests/test_skill_importer.py tests/test_core_module_boundaries.py coryn_data/database/skills.sqlite
git commit -m "feat: validate canonical skill corpus"
```

---

## Milestone 1 Completion Criteria

Milestone 1 is complete only when all of the following are true:

- every `.txt` source under the four supported `raw_skills/` groups is discovered;
- every source is represented by exactly one canonical tree record;
- every source-defined skill block is either represented by exactly one canonical skill record or appears as an explicit import error;
- the final accepted import has zero error issues;
- raw tree text and raw skill block text survive round-trip through SQLite;
- representative Battle, Magic, Assassin, Minstrel, and Alchemy facts pass source-backed tests;
- source uncertainty remains uncertainty rather than being converted into guessed facts;
- generated `skills.sqlite` contains a manifest hash matching the current raw corpus;
- a failed import cannot overwrite a previously valid database;
- no item-search, Qwen, Discord, FTS, vector, Gemma, or formula-evaluation behavior changes;
- the full existing test suite remains green.

After this milestone is accepted, create a separate implementation plan for the retrieval milestone (structured lookup + FTS5 + semantic benchmark + deterministic fusion).