# Direct Structured Intent Execution Design

## Goal

Remove the Qwen fallback's structured-intent -> text rewrite -> deterministic reparse loop. Keep Qwen as an interpretation-only component, but carry validated `SearchIntentRequest` objects directly into the existing deterministic search execution layer.

## Architecture

The deterministic router remains the first stage and is unchanged for normal item/stat queries. When Qwen is needed, `QwenFallbackService` parses the model JSON into one to three `SearchIntentRequest` objects and validates their shape. It returns those typed requests in `FallbackOutcome` instead of rendering query strings.

`search_items.py` owns repository-aware validation and conversion because `ParsedSearch`, `StatResolution`, filters, and expression execution currently live there. A new `parse_structured_search_request()` function converts a `SearchIntentRequest` directly into a fully resolved `ParsedSearch`:

- one bare stat becomes `stat_search`;
- multiple stats, OR semantics, or explicit comparisons become `stat_expression` with a ready `ResolvedStatExpression`;
- a bare stat inside an expression uses the existing parser semantic `>= 1`;
- `match="all"` becomes one `ResolvedAndGroup` containing all clauses;
- `match="any"` becomes one single-clause group per stat;
- `sort_stat` is moved to the first clause so the existing expression result ordering keeps the requested ranking stat primary;
- item filters are resolved through the existing deterministic filter catalog and must consume the complete structured filter value;
- stats resolve only through exact/canonical/known alias matching with fuzzy matching disabled.

The fallback service receives a typed `validate_search_request` callback. Model candidates that cannot be converted into valid deterministic searches are discarded before reaching the UI.

## User interaction

The existing confirmation behavior is preserved. One valid interpretation is shown as a single "Did you mean" choice; multiple valid interpretations are shown as numbered choices. The displayed label is generated only for presentation and is never reparsed or used for execution.

Selecting an interpretation carries the selected `SearchIntentRequest` directly back to the main loop, converts it to `ParsedSearch`, and executes that parsed object. Typing a completely new query still returns to the normal routing pipeline as text.

## Error handling

Invalid structured stats, filters, operators, sort references, or ambiguous aliases are rejected deterministically. If all Qwen candidates are invalid, the existing unsupported-search message is shown. No SQL is generated or accepted.

A `ParsedSearch` that already contains a `resolved_expression` bypasses interactive stat-resolution, because all structured fields were already validated before selection.

## Compatibility

The public query syntax, database schema, repository search methods, help/database actions, and deterministic routing order do not change. The existing `best`/`highest` quick path remains in place.

## Testing

Tests must prove:

1. fallback returns typed `SearchIntentRequest` objects rather than query strings;
2. repository-aware conversion creates direct `stat_search` for one bare stat;
3. AND/OR/comparison requests create the correct resolved expression without calling `parse_search_query`;
4. `sort_stat` becomes the primary resolved clause;
5. invalid/ambiguous stats and partial/invalid filters are rejected;
6. the confirmation UI returns the selected typed request and never depends on a rewritten query for execution;
7. already-resolved structured expressions bypass interactive resolution;
8. existing deterministic and Ollama adapter tests continue to pass.
