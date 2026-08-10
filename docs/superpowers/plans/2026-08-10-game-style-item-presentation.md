# Game-Style Item Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Toram item/stat output use game-style stat units, names, and equipment-condition grouping, and automatically open the sole matching item instead of showing a one-choice result screen.

**Architecture:** Extend the shared presentation helpers in `search_items.py` so Discord delegates stat/condition semantics to the same code as the terminal. Keep canonical database/query values unchanged; `toram_data/aliases.py` only adds deterministic input aliases, while `toram_search/service.py` materializes unique result sets directly into detail payloads. The legacy terminal interaction loops remain direct and therefore receive matching one-result short-circuits of their own.

**Tech Stack:** Python 3, SQLite-backed `ItemRepository`, `unittest`, `discord.py`, RapidFuzz, existing deterministic parser/service architecture.

## Global Constraints

- Do not rename or migrate canonical database stat rows or stored condition strings/JSON.
- `Motion Speed %` remains canonical; only user-visible output says `Action Speed`.
- `action speed`, `action speed %`, and `action speed%` resolve deterministically to `Motion Speed %`; existing motion aliases continue to work.
- Move only a trailing canonical `%` marker from the visible stat name to the visible numeric value.
- Preserve the existing unavailable/presence behavior: `Flinch/Stun/Tumble Unavailable = 1` renders label-only, while value `0` remains explicit.
- Full item details group conditional stats; a stat applying to multiple recognized equipment alternatives appears in every applicable group.
- Compact stat/expression result lists remain compact and collapse multiple recognized equipment alternatives into one `With A / B` suffix.
- Use full equipment names such as `With 1-Handed Sword` and `With 2-Handed Sword`; do not display `1H`/`2H` abbreviations.
- Unknown/non-equipment conditions are preserved instead of being blindly rewritten.
- Preserve source/database stat order and first-seen condition-group order.
- Exactly one result auto-opens the same destination manual selection would open; zero results and two-or-more results keep existing behavior.
- Duplicate exact-name database rows remain multiple results.
- Do not broaden semantic build/role search or add new Qwen usage.
- Preserve the known unrelated full-suite baseline if it still exists: `test_structured_fallback.StructuredFallbackTests.test_rejected_payload_is_logged_with_reason` may be the sole failure because it expects `missing or invalid search candidates` while current code logs `search payload has unexpected fields`.

---

## File Structure

**Modify:**
- `toram_data/aliases.py` — deterministic input aliases only; add action-speed aliases pointing to canonical `motion speed %`.
- `search_items.py` — shared stat display naming/unit logic, shared condition formatting/grouping model, terminal rendering, and terminal one-result short-circuits.
- `discord_bot.py` — consume the shared condition/group helpers; do not duplicate game-style mappings.
- `toram_search/service.py` — convert one-item result sets into `ItemDetailPayload` or `UpgradeDetailPayload` before Discord selection UI is built.

**Create:**
- `tests/test_game_style_stat_display.py` — stat unit/name display, condition labels, full-detail grouping, compact-condition rendering, terminal/Discord parity.
- `tests/test_action_speed_alias.py` — deterministic action-speed resolution and no-Qwen regression coverage.
- `tests/test_single_result_auto_open.py` — service and terminal auto-open coverage for item, upgrade, stat, and expression searches plus multi-result controls.

No new production module is required: the existing codebase intentionally centralizes shared presentation in `search_items.py`, and moving only this feature to a new module would add import churn without reducing current risk.

---

### Task 1: Game-style stat names, percent placement, and Action Speed aliases

**Files:**
- Modify: `toram_data/aliases.py` in `STAT_ALIASES`
- Modify: `search_items.py` around `_UNAVAILABLE_PRESENCE_STATS`, `format_stat_value()`, and `format_stat_display()`
- Create: `tests/test_game_style_stat_display.py`
- Create: `tests/test_action_speed_alias.py`

**Interfaces:**
- Consumes: `normalize_stat_text()`, `STAT_ALIASES`, existing `format_stat_value(stat_name, amount, signed=True) -> str`
- Produces: `format_stat_name(stat_name: object) -> tuple[str, str]`, where the tuple is `(visible_name, value_suffix)`, and updated `format_stat_display(stat_name, amount) -> str`
- Produces aliases: `action speed`, `action speed %`, `action speed%` -> `motion speed %`

- [ ] **Step 1: Write failing stat-presentation tests**

Create `tests/test_game_style_stat_display.py` with the initial formatter cases:

```python
from __future__ import annotations

import unittest

import search_items as core


class GameStyleStatFormattingTests(unittest.TestCase):
    def test_trailing_percent_moves_to_value(self):
        self.assertEqual(core.format_stat_display("STR %", 6), "STR +6%")
        self.assertEqual(core.format_stat_display("Stability %", -5), "Stability -5%")
        self.assertEqual(core.format_stat_display("Stability %", 0), "Stability 0%")

    def test_non_percent_stat_keeps_existing_numeric_format(self):
        self.assertEqual(core.format_stat_display("Critical Rate", 1), "Critical Rate +1")

    def test_motion_speed_uses_game_name_without_changing_canonical_input(self):
        self.assertEqual(core.format_stat_display("Motion Speed %", 10), "Action Speed +10%")

    def test_prefix_percent_stat_is_not_rewritten_as_trailing_unit(self):
        self.assertEqual(
            core.format_stat_display("% Stronger Against Earth", 10),
            "% Stronger Against Earth +10",
        )

    def test_unavailable_presence_behavior_is_preserved(self):
        self.assertEqual(core.format_stat_display("Tumble Unavailable", 1), "Tumble Unavailable")
        self.assertEqual(core.format_stat_display("Tumble Unavailable", 0), "Tumble Unavailable 0")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the formatter tests and confirm RED**

Run:

```bash
python -m unittest tests.test_game_style_stat_display -v
```

Expected: failures for trailing-percent placement and `Motion Speed % -> Action Speed`; existing unavailable controls should already pass.

- [ ] **Step 3: Implement shared stat display parts**

In `search_items.py`, add the display alias and helper immediately above `format_stat_display()`:

```python
_STAT_DISPLAY_ALIASES = {
    "motion speed %": "Action Speed",
}


def format_stat_name(stat_name: object) -> tuple[str, str]:
    raw_name = str(stat_name or "Unknown stat")
    normalized = normalize_stat_text(raw_name)
    has_trailing_percent = normalized.endswith(" %") and raw_name.rstrip().endswith("%")
    visible_name = raw_name.rstrip()
    value_suffix = "%" if has_trailing_percent else ""
    if has_trailing_percent:
        visible_name = visible_name[:-1].rstrip()
    visible_name = _STAT_DISPLAY_ALIASES.get(normalized, visible_name)
    return visible_name, value_suffix


def format_stat_display(stat_name: object, amount: object) -> str:
    visible_name, value_suffix = format_stat_name(stat_name)
    value = format_stat_value(stat_name, amount, signed=True)
    if not value:
        return visible_name
    return f"{visible_name} {value}{value_suffix}"
```

Do not change `_UNAVAILABLE_PRESENCE_STATS` or the numeric logic in `format_stat_value()`.

- [ ] **Step 4: Add deterministic Action Speed aliases**

In `toram_data/aliases.py`, add these entries to `STAT_ALIASES` beside the existing motion aliases:

```python
    "action speed": "motion speed %",
    "action speed %": "motion speed %",
    "action speed%": "motion speed %",
```

Keep the existing:

```python
    "motion": "motion speed %",
    "motion %": "motion speed %",
    "motion%": "motion speed %",
```

- [ ] **Step 5: Write failing deterministic alias/no-Qwen tests**

Create `tests/test_action_speed_alias.py`:

```python
from __future__ import annotations

import unittest

import search_items as core
from toram_search.service import SearchService
from toram_search.session import FailedQueryContext


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("action speed must resolve deterministically")


class AliasRepository:
    def __init__(self):
        self.stats = ["Motion Speed %"]

    def list_items(self):
        return []

    def list_item_types(self):
        return {"Armor"}

    def list_stat_names(self):
        return list(self.stats)

    def exact_name_matches(self, query):
        return []

    def exact_upgrade_name_matches(self, query):
        return []

    def search_by_stat(self, stat_name, item_types):
        return []

    def search_by_expression(self, expression, item_types, *, primary_sort_ascending=False):
        return []

    def count_items_total(self):
        return 0

    def count_items_by_types(self, item_types):
        return 0

    def count_items_with_stat(self, stat_name):
        return 0


class ActionSpeedAliasTests(unittest.TestCase):
    def test_alias_forms_resolve_to_motion_speed(self):
        available = ["Motion Speed %"]
        for typed in ("action speed", "action speed %", "action speed%", "motion"):
            with self.subTest(typed=typed):
                resolution = core.resolve_stat_term(typed, available, allow_fuzzy=False)
                self.assertIn(resolution.status, {"exact", "alias"})
                self.assertEqual(resolution.candidates, ("Motion Speed %",))

    def test_action_speed_query_never_calls_qwen(self):
        service = SearchService(AliasRepository(), llm_client=MustNotCallLLM())
        outcome = service.handle_query(
            "armor with action speed",
            FailedQueryContext(max_entries=3),
        )
        self.assertEqual(outcome.kind, "search")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run Task 1 tests**

Run:

```bash
python -m unittest \
  tests.test_game_style_stat_display \
  tests.test_action_speed_alias \
  tests.test_unavailable_stat_display -v
```

Expected: all pass; unavailable `+1`/zero behavior remains unchanged.

- [ ] **Step 7: Commit Task 1**

```bash
git add toram_data/aliases.py search_items.py \
  tests/test_game_style_stat_display.py tests/test_action_speed_alias.py
git commit -m "feat: add game-style stat display aliases"
```

---

### Task 2: Shared equipment-condition formatting and full item-detail grouping

**Files:**
- Modify: `search_items.py` around `_parse_conditions()`, `_condition_label()`, and `render_item()`
- Modify: `tests/test_game_style_stat_display.py`

**Interfaces:**
- Consumes: `format_stat_display()`, `_parse_conditions()`, `normalize_name()`
- Produces: `StatDisplayGroup(heading: str | None, lines: tuple[str, ...])`
- Produces: `format_condition_display(row: StatRow | dict[str, Any]) -> str | None`
- Produces: `build_item_stat_groups(stats: Iterable[dict[str, Any]]) -> tuple[StatDisplayGroup, ...]`

- [ ] **Step 1: Add Altadar-like failing grouping tests**

Extend `tests/test_game_style_stat_display.py` with fixtures and assertions:

```python
import json

from discord_bot import build_item_detail_embed


def stat(name, amount, *conditions, condition_text=None, needs_review=False):
    return {
        "stat_name": name,
        "amount": amount,
        "conditions_json": json.dumps(list(conditions)),
        "condition_text": condition_text,
        "coryn_applies_to": None,
        "needs_condition_review": needs_review,
    }


def altadar_like_detail():
    return core.ItemDetail(
        summary=core.ItemSummary(1, "Altadar", "Armor Crysta"),
        sell_price=None,
        process_material=None,
        process_amount=None,
        badge=None,
        note=None,
        page_url=None,
        stats=[
            stat("STR %", 6),
            stat("VIT %", 6),
            stat("Stability %", 11),
            stat("Short Range Damage %", 11, "Light Armor", condition_text="Light Armor only"),
            stat("Long Range Damage %", 11, "Heavy Armor", condition_text="Heavy Armor only"),
            stat(
                "Stability %",
                -5,
                "Light Armor",
                "Heavy Armor",
                condition_text="Heavy Armor,Light Armor only",
            ),
        ],
        sources=[],
        images=[],
        upgrade_predecessors=[],
        upgrade_successors=[],
    )


class GameStyleConditionTests(unittest.TestCase):
    def test_known_equipment_conditions_use_full_with_wording(self):
        cases = (
            (stat("STR", 1, "Light Armor", condition_text="Light Armor only"), "With Light Armor"),
            (stat("STR", 1, "Heavy Armor", condition_text="Heavy Armor only"), "With Heavy Armor"),
            (stat("STR", 1, "1 Handed Sword", condition_text="1 Handed Sword only"), "With 1-Handed Sword"),
            (stat("STR", 1, "2 Handed Sword", condition_text="2 Handed Sword only"), "With 2-Handed Sword"),
        )
        for row, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(core.format_condition_display(row), expected)

    def test_unknown_only_condition_is_preserved(self):
        row = stat("STR", 1, condition_text="Special state only")
        self.assertEqual(core.format_condition_display(row), "Special state only")

    def test_terminal_full_detail_groups_shared_stat_under_both_equipment_sections(self):
        rendered = core.render_item(altadar_like_detail())
        self.assertIn("- STR +6%", rendered)
        self.assertIn("With Light Armor:", rendered)
        self.assertIn("With Heavy Armor:", rendered)
        self.assertEqual(rendered.count("- Stability -5%"), 2)
        self.assertNotIn("Heavy Armor,Light Armor only", rendered)
        self.assertLess(rendered.index("- STR +6%"), rendered.index("With Light Armor:"))
        self.assertLess(rendered.index("With Light Armor:"), rendered.index("With Heavy Armor:"))

    def test_compact_multi_equipment_condition_uses_single_with_prefix(self):
        row = stat(
            "Stability %",
            -5,
            "Light Armor",
            "Heavy Armor",
            condition_text="Heavy Armor,Light Armor only",
        )
        self.assertEqual(
            core.format_condition_display(row),
            "With Light Armor / Heavy Armor",
        )
```

- [ ] **Step 2: Run grouping tests and confirm RED**

Run:

```bash
python -m unittest tests.test_game_style_stat_display.GameStyleConditionTests -v
```

Expected: failures because `format_condition_display()`/`StatDisplayGroup`/grouped `render_item()` do not exist yet.

- [ ] **Step 3: Add explicit equipment display mapping and structured condition helpers**

In `search_items.py`, replace the current single `_condition_label()` responsibility with raw extraction plus explicit equipment recognition. Use this known mapping; unknown strings must fall back unchanged:

```python
_EQUIPMENT_CONDITION_NAMES = {
    "1 handed sword": "1-Handed Sword",
    "2 handed sword": "2-Handed Sword",
    "bow": "Bow",
    "bowgun": "Bowgun",
    "katana": "Katana",
    "staff": "Staff",
    "magic device": "Magic Device",
    "knuckles": "Knuckles",
    "halberd": "Halberd",
    "arrow": "Arrow",
    "dagger": "Dagger",
    "shield": "Shield",
    "light armor": "Light Armor",
    "heavy armor": "Heavy Armor",
}


def _condition_fields(row: StatRow | dict[str, Any]) -> tuple[str | None, str, bool]:
    if isinstance(row, StatRow):
        return row.condition_text, row.conditions_json, row.needs_condition_review
    return (
        row.get("condition_text"),
        str(row.get("conditions_json") or "[]"),
        bool(row.get("needs_condition_review")),
    )


def _raw_condition_label(row: StatRow | dict[str, Any]) -> str | None:
    condition_text, conditions_json, _needs_review = _condition_fields(row)
    if condition_text:
        return str(condition_text)
    conditions = _parse_conditions(conditions_json)
    return ", ".join(conditions) if conditions else None


def _equipment_condition_names(row: StatRow | dict[str, Any]) -> tuple[str, ...]:
    condition_text, conditions_json, _needs_review = _condition_fields(row)
    parts = _parse_conditions(conditions_json)
    if not parts and condition_text:
        cleaned = re.sub(r"\s+only\s*$", "", str(condition_text), flags=re.IGNORECASE)
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if not parts:
        return ()

    visible: list[str] = []
    for part in parts:
        normalized = normalize_name(re.sub(r"\s+only\s*$", "", part, flags=re.IGNORECASE))
        display = _EQUIPMENT_CONDITION_NAMES.get(normalized)
        if display is None:
            return ()
        if display not in visible:
            visible.append(display)
    return tuple(visible)


def format_condition_display(row: StatRow | dict[str, Any]) -> str | None:
    names = _equipment_condition_names(row)
    raw = _raw_condition_label(row)
    _condition_text, _conditions_json, needs_review = _condition_fields(row)
    if names:
        label = "With " + " / ".join(names)
    else:
        label = raw or ""
    if needs_review:
        label = (label + " [needs review]").strip()
    return label or None
```

Keep `_condition_label()` as a compatibility wrapper during this change so existing callers/tests do not break unexpectedly:

```python
def _condition_label(row: StatRow | dict[str, Any]) -> str | None:
    return format_condition_display(row)
```

- [ ] **Step 4: Add the shared full-detail grouping model**

In `search_items.py` after the condition helpers:

```python
@dataclass(frozen=True)
class StatDisplayGroup:
    heading: str | None
    lines: tuple[str, ...]


def build_item_stat_groups(stats: Iterable[dict[str, Any]]) -> tuple[StatDisplayGroup, ...]:
    unconditional: list[str] = []
    order: list[str] = []
    grouped: dict[str, list[str]] = {}

    for stat in stats:
        line = format_stat_display(
            stat.get("stat_name") or "Unknown stat",
            stat.get("amount"),
        )
        _condition_text, _conditions_json, needs_review = _condition_fields(stat)
        equipment_names = _equipment_condition_names(stat)
        raw_condition = _raw_condition_label(stat)

        if needs_review:
            line += " [needs review]"

        if equipment_names:
            headings = tuple(f"With {name}" for name in equipment_names)
        elif raw_condition:
            headings = (raw_condition,)
        else:
            unconditional.append(line)
            continue

        for heading in headings:
            if heading not in grouped:
                grouped[heading] = []
                order.append(heading)
            grouped[heading].append(line)

    output: list[StatDisplayGroup] = []
    if unconditional:
        output.append(StatDisplayGroup(None, tuple(unconditional)))
    output.extend(StatDisplayGroup(heading, tuple(grouped[heading])) for heading in order)
    return tuple(output)
```

This deliberately uses first-seen heading order and appends each stat in source order. A multi-equipment row is inserted into every recognized equipment group.

- [ ] **Step 5: Switch terminal item details to grouped rendering**

Replace the per-stat inline-condition block in `render_item()` with:

```python
    lines.extend(["", "Stats:"])
    groups = build_item_stat_groups(detail.stats)
    if not groups:
        lines.append("- None")
    else:
        for group_index, group in enumerate(groups):
            if group.heading is not None:
                if group_index > 0:
                    lines.append("")
                lines.append(f"{group.heading}:")
            lines.extend(f"- {line}" for line in group.lines)
```

Do not add IDs, links, or extra metadata to condition headings.

- [ ] **Step 6: Run Task 2 tests**

Run:

```bash
python -m unittest \
  tests.test_game_style_stat_display \
  tests.test_unavailable_stat_display -v
```

Expected: all pass; full-detail terminal output duplicates shared equipment stats into each relevant section and unavailable flags remain correct.

- [ ] **Step 7: Commit Task 2**

```bash
git add search_items.py tests/test_game_style_stat_display.py
git commit -m "feat: group item stats by equipment condition"
```

---

### Task 3: Apply shared game-style presentation to Discord and compact result lists

**Files:**
- Modify: `search_items.py` in `render_stat_results()` and `render_expression_results()`
- Modify: `discord_bot.py` in `_result_lines()` and `build_item_detail_embed()`
- Modify: `tests/test_game_style_stat_display.py`

**Interfaces:**
- Consumes: `format_stat_display()`, `format_condition_display()`, `build_item_stat_groups()` from Tasks 1-2
- Produces: no new public interface; Discord must delegate all stat/condition semantics to `search_items.py`

- [ ] **Step 1: Add failing Discord full-detail and compact-result tests**

Extend `tests/test_game_style_stat_display.py`:

```python
from decimal import Decimal

from discord_bot import build_item_detail_embed, build_search_results_embed
from toram_search.service import ExpressionResultsPayload, StatResultsPayload


def stat_row(name, amount, *conditions, condition_text=None):
    return core.StatRow(
        stat_name=name,
        amount=amount,
        conditions_json=json.dumps(list(conditions)),
        condition_text=condition_text,
        coryn_applies_to=None,
        needs_condition_review=False,
        position=0,
    )


class GameStyleFrontendParityTests(unittest.TestCase):
    def test_discord_full_detail_uses_one_stats_field_with_group_headings(self):
        embed = build_item_detail_embed(altadar_like_detail())
        stats_value = next(field.value for field in embed.fields if field.name == "Stats")
        self.assertIn("STR +6%", stats_value)
        self.assertIn("**With Light Armor**", stats_value)
        self.assertIn("**With Heavy Armor**", stats_value)
        self.assertEqual(stats_value.count("Stability -5%"), 2)
        self.assertNotIn("Heavy Armor,Light Armor only", stats_value)

    def test_terminal_stat_result_keeps_multi_condition_compact(self):
        shared = stat_row(
            "Stability %",
            -5,
            "Light Armor",
            "Heavy Armor",
            condition_text="Heavy Armor,Light Armor only",
        )
        result = core.RankedStatItem(
            item=core.ItemSummary(2, "Compact Item", "Armor"),
            primary=shared,
            alternatives=(),
        )
        rendered = core.render_stat_results("Stability %", None, [result], 0)
        self.assertIn("Stability -5% [With Light Armor / Heavy Armor]", rendered)
        self.assertNotIn("Condition: Heavy Armor,Light Armor only", rendered)

    def test_discord_stat_result_keeps_multi_condition_compact(self):
        shared = stat_row(
            "Stability %",
            -5,
            "Light Armor",
            "Heavy Armor",
            condition_text="Heavy Armor,Light Armor only",
        )
        result = core.RankedStatItem(
            item=core.ItemSummary(3, "Compact Item", "Armor"),
            primary=shared,
            alternatives=(),
        )
        parsed = core.ParsedSearch(
            intent="stat_search",
            raw_query="stability armor",
            stat=core.StatResolution("Stability %", "stability", 100.0, False),
        )
        embed = build_search_results_embed(StatResultsPayload(parsed, (result,)), 0)
        self.assertIn("Stability -5% [With Light Armor / Heavy Armor]", embed.description or "")

    def test_expression_result_uses_action_speed_and_compact_condition(self):
        clause = core.ResolvedClause(
            typed_stat="action speed",
            stat_name="Motion Speed %",
            operator=">=",
            value=Decimal("1"),
        )
        expression = core.ResolvedStatExpression((core.ResolvedAndGroup((clause,)),))
        row = stat_row("Motion Speed %", 10, "1 Handed Sword", condition_text="1 Handed Sword only")
        match = core.ClauseMatch(0, clause, (row,))
        result = core.RankedExpressionItem(
            item=core.ItemSummary(4, "Speed Item", "Armor"),
            matches=(match,),
            primary_amount=10,
        )
        terminal = core.render_expression_results(expression, None, [result], 0)
        self.assertIn("Action Speed +10% [With 1-Handed Sword]", terminal)
```

- [ ] **Step 2: Run frontend parity tests and confirm RED**

Run:

```bash
python -m unittest tests.test_game_style_stat_display.GameStyleFrontendParityTests -v
```

Expected: failures because Discord item details still append inline conditions and stat-result condition lines still use the old layout.

- [ ] **Step 3: Make terminal compact result lines use shared condition display**

In `render_stat_results()`:

```python
        primary_line = format_stat_display(stat_name, result.primary.amount)
        condition = format_condition_display(result.primary)
        if condition:
            primary_line += f" [{condition}]"
        lines.append(
            f"{index}. {result.item.name} — {result.item.item_type} — {primary_line}"
        )
        for alternative in result.alternatives:
            alternative_line = "   Also: " + format_stat_display(stat_name, alternative.amount)
            alternative_condition = format_condition_display(alternative)
            if alternative_condition:
                alternative_line += f" [{alternative_condition}]"
            lines.append(alternative_line)
```

In `render_expression_results()`, replace `_condition_label(row)` with `format_condition_display(row)` while preserving the existing inline bracket form.

- [ ] **Step 4: Make Discord compact result lines use the same helper**

In `discord_bot.py` `_result_lines()`:

```python
        primary = core.format_stat_display(stat_name, result.primary.amount)
        condition = core.format_condition_display(result.primary)
        if condition:
            primary += f" [{condition}]"
        return [
            f"**{result.item.name}** — {result.item.item_type}",
            primary,
        ]
```

For expression rows, keep the existing line structure but replace `_condition_label(row)` with `core.format_condition_display(row)`.

Do not introduce a Discord-only equipment mapping.

- [ ] **Step 5: Render Discord full item details from shared groups**

Replace the per-stat loop in `build_item_detail_embed()` with:

```python
    stat_lines: list[str] = []
    for group in core.build_item_stat_groups(detail.stats):
        if group.heading is not None:
            if stat_lines:
                stat_lines.append("")
            stat_lines.append(f"**{group.heading}**")
        stat_lines.extend(group.lines)
    _safe_field(embed, "Stats", "\n".join(stat_lines) if stat_lines else "None")
```

The entire grouped presentation stays inside one `Stats` embed field.

- [ ] **Step 6: Run Task 3 and existing Discord display regressions**

Run:

```bash
python -m unittest \
  tests.test_game_style_stat_display \
  tests.test_unavailable_stat_display \
  tests.test_discord_bot \
  tests.test_discord_source_display -v
```

Expected: all pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add search_items.py discord_bot.py tests/test_game_style_stat_display.py
git commit -m "feat: use game-style stat presentation in discord"
```

---

### Task 4: Auto-open sole result in `SearchService` so Discord skips one-choice selectors

**Files:**
- Modify: `toram_search/service.py` in `SearchService._materialize()`
- Create: `tests/test_single_result_auto_open.py`

**Interfaces:**
- Consumes: `ItemDetailPayload`, `UpgradeDetailPayload`, `ItemResultsPayload`, `UpgradeResultsPayload`, `StatResultsPayload`, `ExpressionResultsPayload`
- Produces: when the complete materialized result tuple has length `1`, `ItemDetailPayload` for item/stat/expression searches or `UpgradeDetailPayload` for upgrade searches

- [ ] **Step 1: Write failing service-level one-result tests**

Create `tests/test_single_result_auto_open.py` with a configurable repository:

```python
from __future__ import annotations

import unittest

import search_items as core
from toram_search.service import (
    ExpressionResultsPayload,
    ItemDetailPayload,
    ItemResultsPayload,
    SearchService,
    StatResultsPayload,
    UpgradeDetailPayload,
    UpgradeResultsPayload,
)
from toram_search.session import FailedQueryContext


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("deterministic search must not call Qwen")


def row(name: str, amount: float) -> core.StatRow:
    return core.StatRow(name, amount, "[]", None, None, False, 0)


class AutoOpenRepository:
    def __init__(self):
        self.altadar = core.ItemSummary(1, "Altadar", "Armor Crysta")
        self.other = core.ItemSummary(2, "Other Item", "Armor")
        self.don = core.ItemSummary(3, "Don Upgrade B", "Enhancer Crysta (Blue)")
        self.stat_results = []
        self.expression_results = []

    def list_items(self):
        return [self.altadar, self.other, self.don]

    def list_item_types(self):
        return {"Armor", "Armor Crysta", "Enhancer Crysta (Blue)"}

    def list_stat_names(self):
        return ["MaxHP", "Critical Rate", "Attack MP Recovery"]

    def exact_name_matches(self, query):
        return []

    def exact_upgrade_name_matches(self, query):
        return []

    def get_item(self, item_id):
        summary = next(item for item in self.list_items() if item.id == item_id)
        return core.ItemDetail(summary, None, None, None, None, None, None, [], [], [], [], [])

    def get_upgrade_component(self, item_id):
        item = next(item for item in self.list_items() if item.id == item_id)
        return core.UpgradeGraph(nodes={item.id: item}, edges={item.id: ()}, missing_nodes={})

    def search_by_stat(self, stat_name, item_types):
        return list(self.stat_results)

    def search_by_expression(self, expression, item_types, *, primary_sort_ascending=False):
        return list(self.expression_results)

    def count_items_total(self):
        return len(self.list_items())

    def count_items_by_types(self, item_types):
        return sum(item.item_type in item_types for item in self.list_items())

    def count_items_with_stat(self, stat_name):
        return 0


class ServiceSingleResultAutoOpenTests(unittest.TestCase):
    def setUp(self):
        self.repository = AutoOpenRepository()
        self.service = SearchService(self.repository, llm_client=MustNotCallLLM())
        self.context = FailedQueryContext(max_entries=3)

    def test_one_stat_result_opens_item_detail(self):
        self.repository.stat_results = [
            core.RankedStatItem(self.repository.other, row("MaxHP", 5000), ())
        ]
        outcome = self.service.handle_query("hp armor", self.context)
        self.assertIsInstance(outcome.payload, ItemDetailPayload)
        self.assertEqual(outcome.payload.detail.summary.id, self.repository.other.id)

    def test_two_stat_results_keep_results_payload(self):
        self.repository.stat_results = [
            core.RankedStatItem(self.repository.other, row("MaxHP", 5000), ()),
            core.RankedStatItem(self.repository.altadar, row("MaxHP", 4000), ()),
        ]
        outcome = self.service.handle_query("hp", self.context)
        self.assertIsInstance(outcome.payload, StatResultsPayload)

    def test_one_expression_result_opens_item_detail(self):
        parsed = core.parse_search_query("armor with hp and critical rate", self.repository)
        resolved = core.ResolvedStatExpression((core.ResolvedAndGroup((
            core.ResolvedClause("hp", "MaxHP", ">=", core.Decimal("1")),
            core.ResolvedClause("critical rate", "Critical Rate", ">=", core.Decimal("1")),
        )),))
        parsed = core.replace(parsed, resolved_expression=resolved)
        result = core.RankedExpressionItem(self.repository.other, (), 5000)
        self.repository.expression_results = [result]
        payload = self.service._materialize(parsed, {})
        self.assertIsInstance(payload, ItemDetailPayload)
        self.assertEqual(payload.detail.summary.id, self.repository.other.id)

    def test_one_fuzzy_item_result_opens_detail(self):
        payload = self.service._materialize(
            core.ParsedSearch("item_search", "altadr", item_query="altadr"),
            {},
        )
        self.assertIsInstance(payload, ItemDetailPayload)
        self.assertEqual(payload.detail.summary.id, self.repository.altadar.id)

    def test_one_fuzzy_upgrade_result_opens_upgrade_detail(self):
        payload = self.service._materialize(
            core.ParsedSearch("upgrade_search", "upgrade Don Upgrad B", item_query="Don Upgrad B"),
            {},
        )
        self.assertIsInstance(payload, UpgradeDetailPayload)
        self.assertEqual(payload.selected_item_id, self.repository.don.id)
```

When implementing the actual test file, import `Decimal` and `replace` from the standard library rather than reaching through `search_items`:

```python
from dataclasses import replace
from decimal import Decimal
```

and construct `ResolvedClause(..., value=Decimal("1"))`.

- [ ] **Step 2: Run service auto-open tests and confirm RED**

Run:

```bash
python -m unittest tests.test_single_result_auto_open.ServiceSingleResultAutoOpenTests -v
```

Expected: unique stat/expression/fuzzy item/fuzzy upgrade tests fail because `_materialize()` currently returns result payloads for those one-item cases; two-result control passes.

- [ ] **Step 3: Generalize `SearchService._materialize()` item and upgrade branches**

For `item_search`, materialize the final ranked list once and short-circuit after relevance filtering:

```python
            results = (
                [core.RankedItem(item=item, score=100.0, match_kind="exact") for item in exact]
                if len(exact) > 1
                else core.rank_items(query, self.all_items)
            )
            if len(results) == 1:
                return ItemDetailPayload(self.repository.get_item(results[0].item.id))
            return ItemResultsPayload(query, tuple(results))
```

Retain the existing unique-exact fast path.

For `upgrade_search`, after building `results`:

```python
            if len(results) == 1:
                selected_id = results[0].item.id
                return UpgradeDetailPayload(
                    graph=self.repository.get_upgrade_component(selected_id),
                    selected_item_id=selected_id,
                )
            return UpgradeResultsPayload(query, tuple(results))
```

Retain the existing unique-exact fast path and crysta-only relevance filtering.

- [ ] **Step 4: Short-circuit one stat and expression result in the service**

For `stat_search`:

```python
            results = self.repository.search_by_stat(
                parsed.stat.stat_name,
                parsed.filter.item_types if parsed.filter else None,
            )
            if len(results) == 1:
                return ItemDetailPayload(self.repository.get_item(results[0].item.id))
            return StatResultsPayload(parsed, tuple(results))
```

For `stat_expression` after the repository call:

```python
            results = self.repository.search_by_expression(
                resolved.resolved_expression,
                resolved.filter.item_types if resolved.filter else None,
                primary_sort_ascending=resolved.primary_sort_ascending,
            )
            if len(results) == 1:
                return ItemDetailPayload(self.repository.get_item(results[0].item.id))
            return ExpressionResultsPayload(resolved, tuple(results))
```

Do not collapse zero or multiple results.

- [ ] **Step 5: Add a Discord message-construction assertion**

Extend the test file with a focused assertion that a unique service result produces no selection view:

```python
from pathlib import Path

from discord_bot import DiscordSessionManager, build_service_outcome_message

    def test_unique_stat_service_outcome_builds_detail_without_search_selector(self):
        self.repository.stat_results = [
            core.RankedStatItem(self.repository.other, row("MaxHP", 5000), ())
        ]
        outcome = self.service.handle_query("hp armor", self.context)
        sessions = DiscordSessionManager()
        key = (1, 2, 3)
        session = sessions.start_query(key, "hp armor")
        embed, view, _file = build_service_outcome_message(
            outcome,
            bot_example_prefix="@Bot",
            sessions=sessions,
            key=key,
            generation=session.generation,
            database_path=Path("coryn_data/database/items.sqlite"),
        )
        self.assertEqual(embed.title, "Other Item")
        self.assertFalse(any(getattr(child, "placeholder", "") == "Select an item" for child in view.children))
```

Because `ItemDetailView` may still exist for image navigation, assert specifically that no item selector exists rather than requiring `view is None`.

- [ ] **Step 6: Run Task 4 and service/Discord regressions**

Run:

```bash
python -m unittest \
  tests.test_single_result_auto_open.ServiceSingleResultAutoOpenTests \
  tests.test_search_service \
  tests.test_discord_followup_regressions \
  tests.test_discord_bot -v
```

Expected: all pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add toram_search/service.py tests/test_single_result_auto_open.py
git commit -m "feat: auto-open sole service search result"
```

---

### Task 5: Auto-open sole result in the terminal interaction loops

**Files:**
- Modify: `search_items.py` in `_interactive_item_results()`, `_interactive_upgrade_results()`, `interactive_stat_results()`, and `interactive_expression_results()`
- Modify: `tests/test_single_result_auto_open.py`

**Interfaces:**
- Consumes: existing `render_item()`, `_show_upgrade_component()`, `ResultScreenOutcome("selected")`
- Produces: terminal functions return `ResultScreenOutcome("selected")` immediately when the current complete result set contains one item

- [ ] **Step 1: Add failing CLI one-result tests**

Extend `tests/test_single_result_auto_open.py` with a terminal repository and runner. The repository should override the service fixture only where terminal code needs direct result data:

```python
class CliAutoOpenRepository(AutoOpenRepository):
    def __init__(self):
        super().__init__()
        self.items = [self.altadar]

    def list_items(self):
        return list(self.items)


class CliSingleResultAutoOpenTests(unittest.TestCase):
    def run_cli(self, repository, query, *answers):
        stream = iter((query, *answers, "quit"))
        output = []
        result = core.interactive_search(
            repository,
            input_fn=lambda _prompt: next(stream),
            output_fn=output.append,
            llm_client=MustNotCallLLM(),
        )
        self.assertEqual(result, 0)
        return "\n".join(output)

    def test_one_fuzzy_item_result_opens_without_numeric_choice(self):
        repository = CliAutoOpenRepository()
        rendered = self.run_cli(repository, "altadr")
        self.assertIn("Altadar\n=======", rendered)
        self.assertNotIn("Choose 1–5", rendered)

    def test_one_stat_result_opens_without_result_commands(self):
        repository = CliAutoOpenRepository()
        repository.stat_results = [
            core.RankedStatItem(repository.altadar, row("MaxHP", 5000), ())
        ]
        rendered = self.run_cli(repository, "hp")
        self.assertIn("Altadar\n=======", rendered)
        self.assertNotIn("Commands: 1–5", rendered)

    def test_one_expression_result_opens_without_result_commands(self):
        repository = CliAutoOpenRepository()
        clause = core.ResolvedClause("hp", "MaxHP", ">=", Decimal("1"))
        repository.expression_results = [
            core.RankedExpressionItem(
                repository.altadar,
                (core.ClauseMatch(0, clause, (row("MaxHP", 5000),)),),
                5000,
            )
        ]
        rendered = self.run_cli(repository, "hp >= 1")
        self.assertIn("Altadar\n=======", rendered)
        self.assertNotIn("Commands: 1–5", rendered)

    def test_one_fuzzy_upgrade_result_opens_tree_without_choice(self):
        repository = CliAutoOpenRepository()
        repository.items = [repository.don]
        rendered = self.run_cli(repository, "upgrade Don Upgrad B")
        self.assertIn("Upgrade Tree — Don Upgrade B", rendered)
        self.assertNotIn("Choose 1–5", rendered)
```

Ensure `CliAutoOpenRepository.get_item()` uses `self.list_items()` so the selected item can be materialized after test-specific item replacement.

- [ ] **Step 2: Run CLI auto-open tests and confirm RED**

Run:

```bash
python -m unittest tests.test_single_result_auto_open.CliSingleResultAutoOpenTests -v
```

Expected: the one-result screens still ask for selection/commands and therefore fail the no-chooser assertions.

- [ ] **Step 3: Short-circuit one fuzzy item and upgrade result**

In `_interactive_item_results()` after result construction and the empty-result guard:

```python
    if len(results) == 1:
        output_fn(render_item(repository.get_item(results[0].item.id)))
        return ResultScreenOutcome("selected")
```

In `_interactive_upgrade_results()` after result construction and the empty-result guard:

```python
    if len(results) == 1:
        return _show_upgrade_component(
            repository,
            results[0].item,
            output_fn=output_fn,
        )
```

Do this before page construction or input prompts.

- [ ] **Step 4: Short-circuit one stat or expression result before pagination/input**

In `interactive_stat_results()`, immediately after `repository.search_by_stat(...)`:

```python
        if len(results) == 1:
            output_fn(render_item(repository.get_item(results[0].item.id)))
            return ResultScreenOutcome("selected")
```

In `interactive_expression_results()`, immediately after `repository.search_by_expression(...)`:

```python
        if len(results) == 1:
            output_fn(render_item(repository.get_item(results[0].item.id)))
            return ResultScreenOutcome("selected")
```

Because these checks run on every loop iteration, a user-applied filter that narrows a previously multi-result screen to one item also auto-opens that item on the next render cycle.

- [ ] **Step 5: Add multi-result control tests**

Add at least one control proving the CLI still presents choices for multiple items:

```python
    def test_two_item_results_keep_numeric_choice(self):
        repository = CliAutoOpenRepository()
        second = core.ItemSummary(9, "Altadar Replica", "Armor Crysta")
        repository.items = [repository.altadar, second]
        rendered = self.run_cli(repository, "altad", "1")
        self.assertIn("Suggestions 1–2 of 2", rendered)
```

Add analogous service multi-result assertions for upgrade/stat/expression if not already covered by Task 4.

- [ ] **Step 6: Run CLI and upgrade regressions**

Run:

```bash
python -m unittest \
  tests.test_single_result_auto_open \
  tests.test_cli_upgrade \
  tests.test_search_service -v
```

Expected: all pass; existing fuzzy multi-choice behavior remains available when more than one result survives relevance filtering.

- [ ] **Step 7: Commit Task 5**

```bash
git add search_items.py tests/test_single_result_auto_open.py
git commit -m "feat: auto-open sole terminal search result"
```

---

### Task 6: Real-database checks and final regression gate

**Files:**
- No production changes expected
- Modify tests only if a genuine uncovered regression is found; do not weaken assertions to make the gate pass

**Interfaces:**
- Verifies all interfaces introduced by Tasks 1-5 against the checked-in SQLite database and both frontends

- [ ] **Step 1: Inspect real condition vocabulary before finalizing the equipment mapping**

Run from repository root:

```bash
python - <<'PY'
import sqlite3
from pathlib import Path

path = Path("coryn_data/database/items.sqlite")
con = sqlite3.connect(path)
try:
    rows = con.execute(
        "SELECT DISTINCT condition_text FROM item_stats "
        "WHERE condition_text IS NOT NULL AND trim(condition_text) <> '' "
        "ORDER BY condition_text COLLATE NOCASE"
    ).fetchall()
    for (value,) in rows:
        print(value)
finally:
    con.close()
PY
```

Expected: review the actual equipment-only vocabulary. Add only missing verified equipment names to `_EQUIPMENT_CONDITION_NAMES`; do not generalize by rewriting every `... only` string.

If the table name differs in the checked-in schema, inspect `ItemRepository`'s stat query in `search_items.py` and use that exact table name; do not guess a migration or alter schema.

- [ ] **Step 2: Verify Altadar-like real item output in terminal and Discord formatter**

Run:

```bash
python - <<'PY'
import search_items as core
from discord_bot import build_item_detail_embed

repo = core.ItemRepository(core.DEFAULT_DATABASE)
try:
    matches = repo.exact_name_matches("Altadar")
    assert matches, "Altadar not found in checked-in database"
    detail = repo.get_item(matches[0].id)
    terminal = core.render_item(detail)
    embed = build_item_detail_embed(detail)
    stats = next(field.value for field in embed.fields if field.name == "Stats")
    print(terminal)
    print("\n--- Discord Stats ---\n")
    print(stats)
finally:
    repo.close()
PY
```

Expected presentation characteristics when those stats exist on the selected row:

```text
STR +6%
VIT +6%
Stability +11%
With Light Armor
Short Range Damage +11%
Stability -5%
With Heavy Armor
Long Range Damage +11%
Stability -5%
```

Raw `STR % +6`, `... only`, and repeated inline `Heavy Armor,Light Armor only` wording must not appear in the dedicated full-detail Stats presentation.

- [ ] **Step 3: Verify Action Speed against the real stat catalog**

Run:

```bash
python - <<'PY'
import search_items as core

repo = core.ItemRepository(core.DEFAULT_DATABASE)
try:
    stats = repo.list_stat_names()
    assert "Motion Speed %" in stats
    for typed in ("action speed", "action speed %", "action speed%", "motion"):
        resolution = core.resolve_stat_term(typed, stats, allow_fuzzy=False)
        assert resolution.candidates == ("Motion Speed %",), (typed, resolution)
    print(core.format_stat_display("Motion Speed %", 10))
finally:
    repo.close()
PY
```

Expected final line:

```text
Action Speed +10%
```

- [ ] **Step 4: Run focused feature gate**

Run:

```bash
python -m unittest \
  tests.test_game_style_stat_display \
  tests.test_action_speed_alias \
  tests.test_single_result_auto_open \
  tests.test_unavailable_stat_display \
  tests.test_search_service \
  tests.test_discord_bot \
  tests.test_discord_followup_regressions \
  tests.test_discord_source_display \
  tests.test_cli_upgrade \
  tests.test_item_search_relevance \
  tests.test_direct_structured_intent -v
```

Expected: all focused tests pass.

- [ ] **Step 5: Compile touched Python modules**

Run:

```bash
python -m py_compile search_items.py discord_bot.py toram_search/service.py toram_data/aliases.py \
  tests/test_game_style_stat_display.py tests/test_action_speed_alias.py \
  tests/test_single_result_auto_open.py
```

Expected: exit code `0`.

- [ ] **Step 6: Run full suite and compare with baseline**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests pass, or the only failure is the pre-existing structured-fallback logging assertion:

```text
test_structured_fallback.StructuredFallbackTests.test_rejected_payload_is_logged_with_reason
```

Do not change fallback logging as part of this feature.

- [ ] **Step 7: Review exact diff scope**

Run:

```bash
git diff --stat main...HEAD
git diff --name-only main...HEAD
```

Expected implementation files:

```text
discord_bot.py
search_items.py
toram_data/aliases.py
toram_search/service.py
tests/test_action_speed_alias.py
tests/test_game_style_stat_display.py
tests/test_single_result_auto_open.py
```

The already-approved spec/plan documents may also appear if the implementation branch includes them. No database file, `.env`, parser dataset, or unrelated fallback file should change.

- [ ] **Step 8: Final implementation commit if verification required any test-only adjustment**

If Step 1 required a verified extra equipment mapping or Steps 2-6 required a legitimate test-only correction, commit only those scoped adjustments:

```bash
git add search_items.py tests/test_game_style_stat_display.py tests/test_action_speed_alias.py tests/test_single_result_auto_open.py
git commit -m "test: verify game-style item presentation"
```

If no files changed after the previous task commits, do not create an empty commit.
