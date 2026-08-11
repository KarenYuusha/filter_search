# Database Question Grounding Bugfix Design

Date: 2026-08-11

## Problem

The natural database question `how many bow do you have` is not recognized by `DatabaseQuestionService.match_direct()`. It falls through deterministic routing into Qwen fallback. Qwen can then emit any schema-valid database action whose referenced stat/item type exists in the database. Because validation currently checks existence/shape but not whether the value is grounded in the user's words, an unrelated real stat such as `% stronger against Dark` can be accepted and executed.

Observed bad behavior:

- Input: `how many bow do you have`
- Output: `There are 67 items with % stronger against Dark.`

## Goal

Natural item-type count questions should resolve deterministically when the item type can be safely identified, and Qwen database actions must not introduce an unrelated stat or item type that is unsupported by the current user query.

## Scope

This is a targeted bugfix. It does not change search syntax, ranking, Discord UI, Qwen model configuration, database schema, or supported database action set.

## Approaches Considered

### A. Only add more deterministic phrases

Add patterns for `how many <type> do you have`, plural forms, and similar wording.

Pros: smallest change and fast path for the reported query.

Cons: does not prevent another unsupported natural database question from reaching Qwen and producing a different unrelated but valid database value.

### B. Only add Qwen grounding validation

Keep deterministic matching unchanged but reject Qwen database actions whose referenced value cannot be related to the current input.

Pros: addresses the hallucination class.

Cons: common natural item-count questions still unnecessarily invoke Qwen and may be rejected instead of answered deterministically.

### C. Deterministic coverage plus Qwen grounding — selected

Expand deterministic count-by-type phrasing and independently ground Qwen database actions against the current input.

This fixes the reported query at the fastest layer and adds a defense against the wider hallucination class.

## Deterministic Count Parsing

`DatabaseQuestionService.match_direct()` will accept safe natural variants of item-type count questions, including:

- `how many bow do you have`
- `how many bows do you have`
- `how many bow items do you have`
- `how many bows are there`
- existing forms such as `how many Bow items are there`

The captured noun phrase is resolved using the existing canonical item-type resolver. The parser must not guess an item type when resolution fails.

Plural handling should be minimal and item-type-oriented: try the captured phrase as-is first, then a conservative singularized form only when necessary (for example `bows` -> `bow`). Existing aliases/resolution remain authoritative.

## Qwen Database Grounding

Qwen remains allowed to choose only the existing database actions. Structural validation and canonical database existence checks remain unchanged.

For argument-bearing database actions, add an additional grounding check using the current user input:

- `count_items_by_type` / `item_type_exists`: the requested item type must be resolvable from a meaningful phrase present in the current query.
- `count_items_with_stat` / `stat_exists`: the requested stat must be resolvable from a meaningful phrase present in the current query.

Grounding is based on the same canonical resolver used by deterministic parsing, not raw substring equality. This permits aliases and normal user wording while rejecting unrelated canonical values invented by Qwen.

No-argument metadata actions (`list_stats`, `list_item_types`, `count_items_total`) keep their existing validation because they do not introduce an entity value.

If a Qwen database action fails grounding, the fallback outcome is `failed`; it must not execute the database action. Existing fallback/refusal behavior after rejection is preserved.

## Data Flow

For the reported query:

1. `route_deterministically()` asks `DatabaseQuestionService.match_direct()`.
2. The natural count pattern captures `bow`.
3. Existing item-type resolution maps it to the canonical Bow type/filter grouping.
4. The service executes `count_items_by_type` directly.
5. Qwen is never called.

For an unsupported assistant-style database question that reaches Qwen:

1. Qwen returns a schema-valid database action.
2. Existing shape/existence validation runs.
3. New grounding validation compares the action argument with what can be resolved from the current input.
4. An unrelated stat/item type is rejected before database execution.

## Tests

Add regression coverage proving:

1. `how many bow do you have` routes deterministically to `count_items_by_type` and never calls Qwen.
2. `how many bows do you have` and `how many bows are there` resolve to the same item-type action.
3. Existing supported database-question forms continue to work.
4. A fake Qwen response that returns `count_items_with_stat` with `% stronger against Dark` for `how many bow do you have` is rejected and never executed.
5. A Qwen database action with an entity actually grounded in the input remains accepted.
6. No-argument database actions remain unaffected.
7. Full repository test suite remains green.

## Non-goals

- General natural-language question answering.
- Semantic build concepts such as tank/DPS.
- New database action types.
- Fuzzy guessing of arbitrary unknown item types/stats.
- Changes to Discord rendering or session behavior.
