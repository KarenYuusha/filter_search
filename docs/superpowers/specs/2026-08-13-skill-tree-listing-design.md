# Skill Tree Listing Design

## Goal

Add a deterministic Discord skill-tree listing command without changing existing free-text skill search behavior.

Primary syntax:

```text
skill tree <tree name>
```

Examples:

```text
skill tree Shield Skills
skill tree shield
skill tree sheild
```

The command lists every skill in the resolved tree in source order and reuses the existing 5-results-per-page skill navigation/detail UI.

## Scope

In scope:

- Explicit `skill tree ...` routing inside the skill path.
- Exact case-insensitive tree-name resolution.
- Deterministic unique shorthand such as `shield` -> `Shield Skills`.
- Fuzzy typo handling with confirmation.
- Ambiguous tree-name choices.
- Missing/unknown tree-name responses.
- Five skills per page.
- Selecting a listed skill opens the existing skill detail pages.
- Owner/generation protection remains active.
- No Qwen or semantic embedding search for tree resolution.

Out of scope:

- Interpreting ordinary `skill <words>` as a tree listing.
- Build labels such as tank/dps.
- Semantic tree matching.
- Changing item/stat search behavior.
- Changing canonical skill-tree data.

## Approaches Considered

### A. Explicit deterministic `skill tree` command — selected

Resolve only against the canonical tree-name catalog and deterministic shorthand/fuzzy matching.

Advantages:

- Fast and predictable.
- No LLM or embedding dependency.
- Does not alter existing `skill <words>` free-text semantics.
- Easy to test exhaustively because there are only a few dozen tree names.

### B. Auto-detect tree intent from ordinary `skill <words>`

For example, interpret `skill shield skills` as a tree listing.

Rejected because it changes the meaning of existing free-text skill search and creates ambiguity between a skill search and tree listing.

### C. Semantic/LLM tree resolution

Use embeddings or Qwen to infer intended tree names.

Rejected because tree names are a small fixed catalog. The extra latency, dependency, and nondeterminism provide no meaningful benefit.

## Command Parsing

Existing `parse_skill_command()` remains responsible only for stripping the leading `skill` token.

Inside `SkillSearchService.handle()`, detect the token-bounded prefix `tree` before normal skill-name/free-text handling.

Examples:

```text
skill tree shield
```

becomes skill remainder:

```text
tree shield
```

and enters tree-list handling.

The following must remain ordinary skill search, not tree commands:

```text
skill treehouse
skill skill tree attack
```

`tree` matching is case-insensitive and token-bounded.

## Tree Name Resolution

Tree resolution uses the repository tree catalog only.

### Normalization

Normalize input and canonical tree names with the same case-folding/whitespace normalization already used by the skill repository.

Each canonical tree also gets a deterministic shorthand by removing a trailing `Skills` token when present.

Examples:

- `Shield Skills` -> `shield`
- `Magic Skills` -> `magic`
- `Magic Warrior Skills` -> `magic warrior`

No manually maintained alias table is required initially unless real corpus names expose an exception that cannot be represented by this rule.

### Resolution order

1. Exact canonical tree name.
2. Exact unique shorthand.
3. Fuzzy candidates from canonical names and shorthands.

Exact canonical/shorthand matches execute immediately.

### Fuzzy matching

Use RapidFuzz `WRatio` against both the normalized canonical name and normalized shorthand. A tree's fuzzy score is the maximum of those two scores. Do not use embeddings or Qwen.

A typo never executes silently.

Deterministic thresholds:

- Minimum actionable fuzzy score: `80`.
- Candidates scoring below `80` are guidance-only and cannot be executed from the response.
- If the top actionable candidate is at least `10` points above the second actionable candidate, return a single confirmation for the top candidate.
- If two or more actionable candidates are within `10` points of the top score, return an ordered choice payload containing up to five candidates.
- Candidate order is score descending, then canonical tree name case-insensitively, then tree ID for a stable tie-break.

Example single confirmation:

```text
Did you mean Shield Skills?
```

Actions:

```text
[Show skills] [Cancel]
```

Example ambiguous choice:

```text
Which skill tree did you mean?

1. Magic Skills
2. Magic Warrior Skills
```

The UI should use buttons/select options when practical rather than requiring the user to type a number.

If no candidate reaches `80`, return a not-found payload. It may display up to three nearest tree names as non-interactive guidance, ordered by the same deterministic score/tie-break rules.

## Missing Tree Name

`skill tree` with no name does not run free-text search.

Return tree-list help containing:

```text
Use `skill tree <tree name>`.
Example: `skill tree shield`
```

Also show the available canonical tree names. If the complete catalog is too large for one Discord embed, paginate or split within Discord limits. This help path should not initialize semantic search.

## Payload Model

Add explicit tree-listing payloads rather than overloading free-text `SkillResultsPayload` semantics.

Recommended application-layer payloads:

- `SkillTreeResultsPayload`
  - resolved `SkillTreeDraft`
  - all `SkillResultItem` entries in source order
- `SkillTreeConfirmationPayload`
  - original input
  - one suggested `SkillTreeDraft`
- `SkillTreeChoicesPayload`
  - original input
  - ordered candidate `SkillTreeDraft` values
- `SkillTreeNotFoundPayload`
  - original input
  - up to three closest tree names
- `SkillTreeHelpPayload`
  - canonical tree-name catalog or help text

The current `SkillPayload` union expands to include these payloads.

## Application Data Flow

For `tree <name>`:

1. Resolve exact canonical name.
2. Otherwise resolve exact unique shorthand.
3. Otherwise evaluate deterministic fuzzy candidates.
4. Exact result -> `repository.list_skills_in_tree(tree.id)`.
5. Convert every skill to `SkillResultItem` with the resolved tree.
6. Return `SkillTreeResultsPayload`.
7. Fuzzy single -> confirmation payload.
8. Fuzzy ambiguous -> choices payload.
9. No usable match -> not-found payload.

The tree list must preserve `list_skills_in_tree()` source order and must not rank skills with the hybrid searcher.

## Discord Display

Tree listing reuses the existing result interaction pattern but has tree-specific copy.

Example:

```text
Shield Skills
13 skills
Showing 1–5 of 13

1. Shield Mastery
   Tier 1

2. Shield Bash
   Tier 2 • MP 100 • Physical
...
```

Rules:

- Five skills per page using existing `PAGE_SIZE`.
- No `Closest matches first` text because tree results are source ordered, not ranked.
- Embed title is the canonical tree name.
- Show total skill count.
- Result metadata uses the same compact metadata formatting already used by skill search, but avoid redundantly repeating the tree name on every row because the whole page is already scoped to one tree.
- Skill preview may be shown if it remains readable; preserving the existing compact preview behavior is preferred.
- A select menu opens the existing paginated skill detail view.
- `Back to Results` returns to the same tree-list page.
- Previous/Next behavior and owner/generation checks reuse current skill result/detail interaction protections.

## Confirmation and Choice UI

Fuzzy correction confirmation is session-bound and generation-bound.

`Show skills` resolves the already-selected canonical tree ID directly. It must not run fuzzy resolution again.

`Cancel` replaces or disables the interactive view with a clear cancellation response and performs no search.

For multiple candidates, a select menu contains canonical tree names. Selecting one directly opens that tree listing. Candidate values should use indexes or trusted tree IDs generated by the bot, never user-provided raw values.

## Help Text

General `skill` help should add one tree-list example while retaining existing search examples:

```text
skill tree shield
```

Do not make tree listing the primary skill command; ordinary skill search remains unchanged.

## Failure Isolation

- Tree handling never calls Qwen.
- Tree handling never builds or queries the semantic skill index.
- Tree interaction exceptions use the existing skill-specific error boundary.
- A failed/cancelled tree command does not mutate item failed-query context.
- Ordinary item queries do not initialize tree/skill search beyond existing behavior.

## Testing

### Service tests

Cover:

- `tree Shield Skills` exact canonical match.
- case-insensitive exact match.
- `tree shield` exact unique shorthand.
- `tree sheild` returns confirmation, not results.
- ambiguous shorthand/fuzzy input returns choices.
- low-confidence junk returns not-found.
- bare `tree` returns tree help.
- `treehouse` remains ordinary free-text skill search.
- tree listing preserves repository source order.
- tree listing returns every skill, not only 20.
- tree path never requests semantic runtime.
- fuzzy threshold `80`, ambiguity margin `10`, and deterministic tie-break behavior.

### Discord rendering/interaction tests

Cover:

- five skills per page.
- tree title and total count.
- tree name is not redundantly repeated on every result row.
- Previous/Next boundaries.
- selecting a skill opens existing detail pages.
- Back returns to the same tree page.
- fuzzy confirmation Show/Cancel actions.
- ambiguous candidate selection.
- owner and generation protection.
- no `None` text and Discord size limits.

### Regression tests

- Existing exact skill lookup remains unchanged.
- Existing free-text hybrid search remains unchanged.
- Existing item/stat behavior remains unchanged.
- Full repository test suite passes.

## Acceptance Criteria

The feature is complete when:

1. `skill tree <canonical tree>` lists every skill in source order.
2. Unique shorthand such as `skill tree shield` runs immediately.
3. Misspellings such as `skill tree sheild` require explicit confirmation.
4. Ambiguous input never silently chooses a tree.
5. Missing/unknown tree names produce useful deterministic guidance.
6. Tree listing uses five items per page and existing skill detail navigation.
7. No LLM or semantic retrieval is used for tree resolution/listing.
8. Existing `skill <words>` and item/stat behavior are unchanged.
9. Focused tests and the full repository suite pass.
