# Item and Upgrade Search Relevance Filtering Design

## Problem

Item-name and upgrade-name searches currently rank every candidate in their source set, but `rank_items()` never removes irrelevant candidates. As a result, a normal item-name search can eventually page through the whole item database, while an upgrade-name search can eventually page through every crysta.

The problem is in shared candidate ranking, not in upgrade graph traversal. Once an upgrade candidate is selected, `get_upgrade_component()` should continue showing that selected crysta's complete connected upgrade graph.

## Goal

Name-based searches should return only candidates that are plausibly relevant to the typed name while preserving exact matches, partial-name matches, and useful typo tolerance.

This applies equally to normal item-name search and upgrade/crysta name search in CLI and Discord because both consume the same ranked candidate lists. Stat searches and upgrade graph contents are outside this change.

## Relevance Rule

Keep the current scoring and sorting logic, but add a relevance filter before returning ranked candidates.

A candidate is relevant when either:

1. it has a strong lexical match kind: `exact`, `prefix`, `substring`, or `all_tokens`; or
2. it is fuzzy-only and its final score is at least `70.0`.

Candidates below that relevance rule are removed completely rather than retained for later pages. The threshold is shared and deterministic. No Qwen call is introduced for candidate filtering.

## Expected Behavior

For a query such as `Don`, results may include `Don` and other genuinely matching `Don`-named items, but unrelated database rows must not appear simply because the user pages far enough.

For `upgrade Don`, candidate discovery is restricted first to crysta item types as it is today, then the same relevance filter is applied. Once the user selects one relevant crysta, the existing complete connected upgrade tree remains unchanged.

For a reasonable typo such as `Don Upgrad B`, a sufficiently similar fuzzy candidate can still appear when its score is at least `70.0`.

For unrelated input such as `asdfgh`, if no item clears the lexical/fuzzy relevance rule, the result list is empty. Existing frontend no-match/unclear-query handling should be used rather than filling the page with unrelated names.

## Architecture

`rank_items(query, items)` remains the single shared ranking entry point. It will normalize and reject too-short queries as today, score each supplied candidate with `_score_item()` as today, discard candidates that fail the relevance rule, sort the retained candidates using the existing ordering, and return only those retained candidates.

No separate filtering implementation should be added to `SearchService`, Discord, upgrade lookup, or terminal rendering. This keeps normal item search and upgrade search behavior consistent automatically.

## Scope Boundaries

This change does not cap results to an arbitrary top-N, change page size, change exact-name resolution, change item-type/stat parsing, change Qwen/fallback behavior, change upgrade graph discovery or hybrid tree display, or change stat-result filtering/ranking.

## Tests

Add focused regression coverage proving strong lexical matches remain, fuzzy typo matches at or above the relevance threshold remain, fuzzy-only candidates below the threshold are removed, normal item-name search does not expose unrelated database items, upgrade-name search does not expose unrelated crystas, a completely unrelated name query returns an empty candidate list, and existing exact item/exact upgrade resolution remains unchanged.

Use both direct `rank_items()` tests and `SearchService` integration tests so the shared algorithm and the two affected search flows are covered.

## Success Criteria

A user can no longer page through the whole database from an arbitrary item-name or upgrade-name query. Results contain only lexical matches and sufficiently strong fuzzy matches, while useful typo tolerance and complete selected upgrade graphs remain intact.
