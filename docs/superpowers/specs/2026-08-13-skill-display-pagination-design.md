# Skill Display Pagination Design

## Goal

Improve Discord skill search readability and prevent skill detail text from being silently truncated.

The change is display-only. Skill retrieval, ranking, parsing, database structure, and Qwen behavior remain unchanged.

## Current Problems

1. Search result snippets are too long and can dominate the result list.
2. Detail embeds enforce Discord's total embed size limit, so later fields/sections can be omitted once the budget is exhausted.
3. A long individual section can exceed a single Discord field/page budget and must be split without losing text.

## Search Results

Search results remain paginated at 5 results per page.

Each result is compact and ordered as:

- skill name
- compact metadata line
- one short preview line when available

Example:

```text
1. Magic: Finale
   Magic Skills • Tier 4 • MP 1600 • Magic
   High-power magic attack...
```

Metadata is assembled from available values only. Missing values are omitted instead of rendering placeholders such as `None`.

The compact metadata may include, in order when present:

1. skill tree
2. tier
3. MP cost
4. skill type or damage type, preferring the value that is most useful without making the line noisy

The preview is intentionally short and may be truncated with an ellipsis. Full text is available only in the detail view.

Selecting a result opens the skill detail view.

## Detail Header

Every detail page uses a consistent header:

- title: canonical skill name
- description/header metadata: tree name plus tier and required level when present
- footer: `Page X / Y` when more than one page exists

Example:

```text
Magic: Finale
Magic Skills • Tier 4 • Required Lv 205

Page 1 / 3
```

Missing metadata is omitted.

## Detail Content Order

Detail content keeps the existing logical order:

1. Overview
2. Range / Timing
3. Ailments
4. Weapon requirements
5. Weapon restrictions
6. Description
7. Game description
8. Parsed skill sections in source order

The display layer must not reorder source sections beyond this existing grouping.

## Automatic Detail Pagination

The renderer first converts the skill into ordered logical sections, then packs those sections into immutable detail pages under Discord limits.

Rules:

1. Keep a section intact when it fits on the current page.
2. If it does not fit but would fit on an empty page, move the whole section to the next page.
3. If a section is itself too large for one page/field, split its body into multiple chunks.
4. Continuation chunks use the label `<Section name> (continued)`.
5. Splitting should prefer natural boundaries in this order where practical: paragraph/newline, sentence boundary, whitespace, then hard character boundary as a last resort.
6. No stored non-empty detail text may be silently dropped because of Discord field, description, field-count, or 6000-character embed limits.
7. Every generated page must independently satisfy Discord limits.

The paginator should pre-build the logical pages once for a detail view rather than recomputing a different split on every button click.

## Navigation

For a skill opened from search results:

```text
[Previous] [Next] [Back to Results]
```

- Previous is disabled on the first detail page.
- Next is disabled on the last detail page.
- Back to Results returns to the original results page and preserves the existing result-page position.

For an exact-name search that opens directly to a detail payload, navigation contains only Previous/Next when multiple detail pages exist. There is no Back to Results button because no results list exists.

Single-page details require no pagination controls unless Back to Results is needed for a detail opened from a result list.

Existing owner and generation protections remain unchanged for all buttons/selects.

## State

Detail pagination state is separate from result pagination state.

The view needs enough state to know:

- current result-list page
- selected result index when applicable
- current detail page
- immutable generated detail pages

Returning to the result list must not overwrite the result-list page with the detail page index.

## Discord Limit Safety

The page builder must respect all currently relevant embed constraints, including:

- title limit
- description limit
- field-name limit
- field-value limit
- maximum field count
- 6000 total embed characters

Safety margins may be used so footer/page labels do not push an otherwise valid page over the total limit.

Truncation remains acceptable only for intentionally compact UI text such as search-result previews, select labels, and select descriptions. It is not acceptable for full detail content.

## Future Variant Compatibility

This change does not implement skill variants or alter the skill database model.

However, the detail paginator must treat any future variant content as ordinary ordered sections. This lets a future `Multiple Hunt -> Wolf Sniper` section use the same pagination system without another Discord display redesign.

## Error Handling

If page construction receives an empty optional field, omit it.

If a non-empty section cannot be represented normally because of an unexpected edge case, fall back to safe chunking rather than dropping the section.

Existing skill-search unavailable/error behavior remains unchanged.

## Tests

Add focused tests covering:

1. compact search result metadata and short preview
2. missing metadata omitted cleanly
3. normal single-page detail
4. multi-page detail with no lost text
5. section moved whole to the next page when appropriate
6. oversized section split across pages with `(continued)` labels
7. every generated embed stays within Discord limits
8. Previous/Next disabled state at page boundaries
9. Back to Results preserves the original search-results page
10. exact direct detail has no Back to Results button
11. owner/current-generation interaction protections remain intact
12. existing item-search and skill-search routing behavior remains unchanged

A regression assertion should reconstruct the displayed detail text from generated pages and verify that all source detail content appears in order, allowing only display labels such as `(continued)` to be added.

## Out of Scope

- changing skill retrieval/ranking
- changing semantic embeddings
- changing parser/database schema
- implementing Multiple Hunt variants
- Qwen integration
- item-search display changes
