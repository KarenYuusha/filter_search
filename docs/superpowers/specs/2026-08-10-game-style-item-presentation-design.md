# Game-Style Item Presentation Design

## Goal

Make item and stat presentation closer to Toram Online's in-game wording while preserving the current database schema, canonical stat names, search semantics, and deterministic search-first architecture.

This change covers four related presentation behaviors:

1. group conditional stats in full item details;
2. render percentage-valued stats with `%` attached to the value instead of the stat name;
3. use game-style visible wording for selected stat and equipment-condition names;
4. skip result selection when a supported search produces exactly one item.

The database remains the source of truth. Display aliases must never rewrite stored item/stat/condition data.

## Shared presentation layer

Discord and `search_items.py` should use shared frontend-neutral formatting helpers for stat names, stat values, condition labels, and full-item stat grouping.

The existing shared stat formatting used for unavailable/presence-style effects should be extended rather than duplicated in Discord.

Frontend code may choose Discord-specific emphasis or terminal-specific punctuation, but it must not independently decide:

- whether a stat is percentage-valued;
- the visible stat alias;
- the visible equipment-condition wording;
- which condition groups a stat belongs to;
- whether a multi-equipment condition expands into more than one full-detail group.

This keeps terminal and Discord output semantically identical.

## Database and query invariants

No database migration is part of this change.

Canonical examples remain unchanged internally:

```text
STR %
Motion Speed %
Light Armor only
Heavy Armor only
1 Handed Sword only
2 Handed Sword only
```

Search, filtering, ranking, expression evaluation, and SQLite queries continue using those canonical values.

Only user-facing presentation changes.

## Percentage stat presentation

For canonical stat names whose percentage marker is a trailing `%` unit, move the percentage marker from the visible stat name to the rendered numeric value.

Examples:

```text
Database/canonical          Visible
STR % +6                    STR +6%
VIT % +6                    VIT +6%
Stability % +11             Stability +11%
Stability % -5              Stability -5%
Short Range Damage % +11    Short Range Damage +11%
Motion Speed % +10          Action Speed +10%
```

The signed-number behavior is unchanged: positive values still receive `+`, negative values keep `-`, and zero remains `0`.

Presence-style unavailable stats continue using the already-approved behavior:

```text
Tumble Unavailable +1  -> Tumble Unavailable
Flinch Unavailable +1 -> Flinch Unavailable
Stun Unavailable +1   -> Stun Unavailable
```

A non-presence stored value such as `Tumble Unavailable = 0` must remain explicit.

This specification only requires moving a trailing canonical `%` marker. It does not introduce a general rename policy for every stat containing `%` in another position.

## Motion Speed display alias

`Motion Speed %` remains the canonical database stat.

Its visible label becomes:

```text
Action Speed
```

Combined with percentage-value formatting:

```text
Motion Speed % +10 -> Action Speed +10%
```

This rename is display-only.

### Search aliases

Users must be able to search the in-game wording while resolving to the existing canonical stat.

Add aliases so normalized forms of:

```text
action speed
action speed %
action speed%
```

resolve to:

```text
motion speed %
```

Existing `motion`, `motion %`, `motion%`, and normal `Motion Speed` searches continue to work.

No LLM call is required for these aliases.

## Equipment-condition display wording

Recognized equipment-only conditions should be shown with `With ...` wording instead of `... only`.

Use full equipment names rather than abbreviations.

Required examples:

```text
Canonical condition          Visible condition
Light Armor only             With Light Armor
Heavy Armor only             With Heavy Armor
1 Handed Sword only          With 1-Handed Sword
2 Handed Sword only          With 2-Handed Sword
```

Other recognized equipment-only conditions should follow the same rule with their full item/equipment name, for example `With Bow`, `With Bowgun`, `With Katana`, `With Staff`, `With Magic Device`, `With Knuckles`, or `With Halberd` when those canonical conditions occur.

Use an explicit known-equipment mapping rather than blindly rewriting every string ending in `only`. Unknown or non-equipment conditions must be preserved instead of being incorrectly transformed.

The condition rewrite is display-only. Stored condition text/JSON remains unchanged.

## Full item-detail stat grouping

Full item details should prioritize readability over compactness.

Unconditional stats appear first under the existing `Stats` section. Conditional stats then appear in named condition groups.

Example for Altadar:

```text
Stats
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

### Alternative equipment conditions

When one stat row applies to multiple alternative recognized equipment conditions, repeat that stat under every applicable full-detail group.

For example, a canonical row equivalent to:

```text
Stability % -5 [Heavy Armor, Light Armor only]
```

must be rendered in full details as:

```text
With Light Armor
Stability -5%

With Heavy Armor
Stability -5%
```

This duplication is intentional because it matches how the effect is understood in-game.

Do not duplicate arbitrary non-equipment multi-part conditions. Expansion into multiple groups is specifically for recognized alternative equipment conditions.

### Ordering

Preserve source/database stat order.

- Unconditional stats keep their original order.
- Condition-group order is determined by the first occurrence of that condition while scanning stats in source order.
- Stats inside a condition group retain their source order.
- A stat duplicated into two recognized equipment groups occupies the corresponding source-order position in each group.

Do not alphabetically sort stats or condition groups.

### Discord layout

Keep the full grouped presentation inside the existing `Stats` embed field instead of creating one embed field per condition. This avoids unnecessary field-count pressure on items with many conditions.

Use visually distinct condition headings, preferably bold Discord text:

```text
STR +6%
VIT +6%
Stability +11%

**With Light Armor**
Short Range Damage +11%
Stability -5%

**With Heavy Armor**
Long Range Damage +11%
Stability -5%
```

Existing safe field truncation/platform limits remain the final safety mechanism.

### Terminal layout

The terminal should show the same groups and order using plain text. A colon after a condition heading is acceptable for terminal readability, but the wording itself must match the shared display label.

## Compact stat-search presentation

Normal stat-search and multi-stat/expression result lists stay compact. Do not expand each result into full condition sections.

Example:

```text
Altadar — Armor
Stability +11%
Also: Stability -5% [With Light Armor / Heavy Armor]
```

For multiple recognized alternative equipment conditions, collapse the condition into one compact suffix rather than repeating the stat line.

Use one `With` prefix followed by the full alternative equipment names separated by ` / `:

```text
[With Light Armor / Heavy Armor]
[With 1-Handed Sword / 2-Handed Sword]
```

Single recognized equipment conditions use the normal full label:

```text
[With Light Armor]
[With 1-Handed Sword]
```

Non-equipment conditions retain their existing compact representation except for any already-established safe display formatting.

The percentage and stat-name display aliases apply here too, because they are global presentation rules.

## Single-result auto-open behavior

Supported searches should not force the user through a chooser when the complete result set contains exactly one item.

Evaluate the total result count after deterministic search/relevance filtering and before pagination or selection controls are constructed.

If the result set contains exactly one item, immediately open the same detail destination that selecting that sole result manually would have opened.

This applies to:

1. item-name searches;
2. upgrade-name searches;
3. single-stat searches;
4. multi-stat/expression searches.

### Destination semantics

Auto-open must preserve the meaning of the originating search.

- Item-name result -> normal item detail.
- Single-stat result -> normal item detail for that item.
- Multi-stat/expression result -> normal item detail for that item.
- Upgrade-name result -> the existing upgrade detail/tree destination that manual selection would open.

The feature removes only the unnecessary selection step; it does not replace one result type with another.

### Multiple and zero results

- Zero results keep the existing no-result/failure/suggestion behavior.
- Two or more results keep the existing chooser/pagination behavior.
- Duplicate exact item names remain multiple results if they correspond to multiple database rows; they must not be auto-opened as though they were one item.

This behavior should be consistent in Discord and the terminal.

## Search and LLM behavior

These changes do not broaden supported semantic search scope.

The deterministic parser remains responsible for item names, explicit stats, aliases, and supported stat expressions.

`action speed` is a deterministic alias for the existing `Motion Speed %` stat and must not invoke Qwen when it can be resolved normally.

No new build-role language, recommendation semantics, or subjective interpretation is introduced.

## Error and fallback behavior

Presentation helpers should be conservative when they encounter unexpected data.

- Unknown stat names render using their stored name.
- Invalid/non-numeric amounts keep the existing safe numeric/text fallback.
- Unknown conditions render using their existing stored/display text.
- Only recognized equipment alternatives are split into repeated full-detail groups.
- `needs_condition_review` information must not be silently discarded.
- Existing Discord truncation behavior remains active for platform limits.

A display alias failure must never prevent the underlying item from being shown.

## Testing

### Shared stat formatting tests

Add tests proving:

1. `STR % +6` renders as `STR +6%`;
2. negative percentage values render as `-N%`;
3. zero percentage values render as `0%`;
4. non-percentage numeric stats retain the existing formatting;
5. unavailable/presence stats retain their current label-only behavior for value `1`;
6. unavailable value `0` remains visible;
7. `Motion Speed %` renders as `Action Speed` without changing the canonical stored name.

### Alias tests

Add deterministic parsing/resolution tests proving:

- `action speed` resolves to `Motion Speed %`;
- `action speed %` resolves to `Motion Speed %`;
- existing motion aliases still resolve identically;
- recognized action-speed queries do not call Qwen.

### Condition display tests

Add tests for:

- `Light Armor only` -> `With Light Armor`;
- `Heavy Armor only` -> `With Heavy Armor`;
- `1 Handed Sword only` -> `With 1-Handed Sword`;
- `2 Handed Sword only` -> `With 2-Handed Sword`;
- unknown/non-equipment `... only` text is not blindly rewritten.

### Full item-detail grouping tests

Use an Altadar-like fixture containing unconditional, Light Armor, Heavy Armor, and shared Light/Heavy stats.

Prove in both terminal and Discord:

- unconditional stats appear first;
- Light and Heavy groups are separated;
- shared `Stability -5%` appears in both equipment groups;
- each group preserves source stat order;
- the shared stat is not shown as one combined `Heavy Armor, Light Armor only` full-detail line;
- Discord uses one grouped Stats field rather than one field per condition.

### Compact result tests

Prove stat and expression result views remain compact:

```text
Stability -5% [With Light Armor / Heavy Armor]
```

and do not expand into separate full-detail condition sections.

Also prove global game-style stat aliases/percentage formatting appear in compact results.

### Single-result auto-open tests

For Discord/service and terminal flows, cover:

1. one item-name result auto-opens detail;
2. two item-name results keep the chooser;
3. one single-stat result auto-opens item detail;
4. two stat results keep the chooser;
5. one expression result auto-opens item detail;
6. two expression results keep the chooser;
7. one upgrade-name result skips the candidate chooser and opens the upgrade detail/tree;
8. multiple upgrade candidates retain selection;
9. zero-result behavior is unchanged;
10. duplicate exact-name rows are not collapsed into one result.

Where possible, assert that auto-open happens before pagination/selection UI construction rather than testing only the final text.

## Non-goals

This change does not:

- rename database stat rows;
- migrate `Motion Speed %` to `Action Speed %` in SQLite;
- rewrite stored condition strings/JSON;
- alter stat amounts;
- change stat ranking or expression comparison semantics;
- add semantic build/role searches;
- add LLM interpretation for action speed;
- group compact search-result stats into large condition sections;
- alphabetically reorder item stats;
- auto-open when two or more actual item rows remain after filtering.
