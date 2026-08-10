# High-Confidence Failed-Query Correction Design

Date: 2026-08-10
Status: Approved design, pending user review

## Goal

When the normal deterministic parser cannot handle a query and the Qwen fallback also fails to produce a valid interpretation, try one final deterministic correction pass. If every meaningful part of the failed query can be resolved unambiguously using existing aliases and item-filter definitions, return one corrected example query. Otherwise keep the existing generic examples.

This correction is guidance only. It must never execute automatically.

Example:

- Input: `xtall cr weapon`
- Existing deterministic knowledge: `xtall -> xtal`, `cr -> Critical Rate`, and `weapon + xtal -> Weapon Crysta + Red Enhancer`
- Suggested query: `cr wp xtal`

Ambiguous example:

- Input: `crit xtal weapon`
- `crit` can mean Critical Rate or Critical Damage
- Result: no single correction; show the existing generic help instead

## Constraints

- Do not make a second LLM call.
- Do not use fuzzy semantic guessing for stats or filters.
- Reuse the existing stat aliases, item-word aliases, and item-filter catalog.
- Only return a suggestion if all meaningful input is consumed by one unambiguous interpretation.
- Unknown or ambiguous meaningful tokens make the correction fail closed.
- Keep build concepts such as tank/DPS out of scope.
- Do not change successful deterministic searches or valid Qwen interpretations.

## Architecture

### 1. Deterministic correction helper

Add a small frontend-neutral correction component under the search layer. Its public responsibility is equivalent to:

`try_suggest_query(raw_query, repository) -> str | None`

The helper should use parser-owned normalization data rather than maintaining a second alias list. Existing sources of truth include:

- `ITEM_WORD_ALIASES`
- `STAT_ALIASES`
- ambiguous stat groups such as `crit`
- the existing item-filter candidates such as `wp xtal` / `weapon xtal`

The implementation may expose a narrowly-scoped public helper from `toram_data.stat_query` if needed so the search-layer correction component does not copy private filter definitions.

### 2. Strict recognition model

The first implementation should support the common simple-search shape:

- exactly one resolvable stat
- exactly one resolvable item filter
- optional harmless search filler words
- arbitrary order of the recognized stat and filter tokens

The correction pass should not initially reconstruct comparison expressions, AND/OR expressions, rankings, or multiple stats. Those remain generic-fallback cases unless the normal parser or Qwen already handles them.

This narrow scope keeps the confidence rule understandable and minimizes false suggestions.

### 3. Token normalization

Normalize case, whitespace, punctuation, and existing explicit word aliases first. For example `xtall` becomes `xtal` because that typo is already an intentional alias.

Stat recognition is strict:

- canonical stat name -> accepted
- exact stat alias such as `cr` -> accepted
- ambiguous alias such as `crit` -> rejected for single-suggestion correction
- fuzzy stat similarity -> rejected
- unknown stat-like token -> rejected

Item-filter recognition is also strict. The helper should match against the existing filter catalog, but it may treat the filter phrase as an unordered token group solely for reconstruction. This allows `xtal weapon` to resolve to the same filter as `weapon xtal` while still requiring an exact set of known filter tokens.

If more than one filter candidate matches the same remaining token set, correction fails rather than choosing one.

### 4. Meaningful-token consumption

A suggestion is high-confidence only when every meaningful token is accounted for by:

- the resolved stat phrase,
- the resolved item-filter phrase, or
- a small explicit set of harmless search filler words already used by natural-query grammar, such as `find`, `show`, `me`, `with`, `has`, `have`, `give`, `gives`, `which`, and articles where appropriate.

The filler set must stay conservative. It must not absorb arbitrary unknown words.

Examples:

- `xtall cr weapon` -> all meaningful tokens resolve -> suggest `cr wp xtal`
- `weapon xtall cr` -> all meaningful tokens resolve -> suggest `cr wp xtal`
- `find xtall cr weapon` -> `find` is harmless filler -> suggest `cr wp xtal`
- `xtall blah weapon` -> `blah` is unresolved -> no suggestion
- `crit xtal weapon` -> stat is ambiguous -> no suggestion

### 5. Canonical suggestion rendering

Return one stable, parseable query string rather than echoing the user's token order.

For the initial version:

- prefer the exact stat alias the user supplied when it is already a valid unambiguous alias (`cr`, `cd`, `hp`, etc.)
- otherwise use a canonical parser-supported stat phrase
- render the matched item filter using a preferred parser-supported compact phrase

For the target example the stable output is:

`cr wp xtal`

The helper returns the query without the Discord mention prefix. Frontends add their own prefix when displaying it.

## Service Data Flow

The current flow is:

1. `SearchService.handle_query` runs deterministic routing.
2. If needed, it records the failed input in `FailedQueryContext`.
3. Qwen fallback interprets the query.
4. A valid structured interpretation returns `confirm_search`; otherwise the service eventually returns `failed`.

The new flow changes only the final failure branch:

1. Run deterministic routing as today.
2. Run Qwen fallback as today.
3. If Qwen succeeds, preserve current behavior.
4. If Qwen returns `failed`, call the deterministic correction helper with the original query.
5. If a high-confidence correction is produced:
   - store it with `context.set_latest_suggestion(...)`;
   - return `ServiceOutcome(kind="failed", suggested_query=<query>)`.
6. If no correction is produced, return the ordinary failed outcome with no suggestion.

Add an explicit optional `suggested_query` field to `ServiceOutcome` instead of overloading `text`. This keeps frontend rendering separate from service semantics and allows future non-Discord clients to use the same correction.

The correction pass should run for Qwen `failed`, not for `refuse`. `refuse` represents an understood but unsupported request. For `unavailable`, keep the existing unavailable response because the failure is infrastructure-related rather than an invalid interpretation.

## Failed-Query Context

`FailedQueryContext` already stores `suggested_query` on the latest failed attempt. Reuse that mechanism.

The important ordering is:

- the current failed query is recorded before Qwen is called;
- Qwen sees only suggestions from previous attempts;
- if Qwen fails and the deterministic correction succeeds, store the new suggestion afterward;
- if the user retries and still fails, the next Qwen call can see the previous correction in history.

No new session state is required.

## Discord Presentation

When `ServiceOutcome.kind == "failed"` and `suggested_query` is present, replace the generic three examples with a query-specific correction.

Example:

Title:

`I couldn't interpret that search`

Description:

`Did you mean: @Bot cr wp xtal`

Do not auto-run it and do not add a Search button in the first version. The user explicitly retries the suggested query, which keeps correction behavior safe and obvious.

When `suggested_query` is absent, preserve the existing generic response exactly:

- `@bot hp armor`
- `@bot cr bow`
- `@bot hp > 5000 and cr bow`

## Error Handling and Safety Rules

Correction must fail closed. Return no suggestion when any of these is true:

- an unresolved meaningful token remains;
- the stat resolves ambiguously;
- multiple item filters match;
- more than one stat is detected in this initial simple-search version;
- the query contains comparison or boolean-expression structure that the correction helper does not explicitly support;
- reconstructing the candidate does not parse back into the expected simple stat + item-filter search.

As a final validation step, the generated suggestion should be fed through the deterministic parser. Only expose it if the parser accepts it as the intended stat search with the expected stat and filter. This makes the system prove that the example it shows is actually valid.

## Testing

### Correction helper unit tests

Positive cases:

- `xtall cr weapon` -> `cr wp xtal`
- `weapon xtall cr` -> `cr wp xtal`
- `find xtall cr weapon` -> `cr wp xtal`
- another existing exact stat alias plus a crysta filter

Negative cases:

- `crit xtal weapon` -> `None` because `crit` is ambiguous
- `xtall blah weapon` -> `None` because `blah` is unresolved
- query with two recognized stats -> `None` in v1
- comparison/AND/OR query -> `None` in v1
- a case where filter reconstruction would match more than one candidate -> `None`

Each positive test should also verify that the returned query parses successfully through the real deterministic parser.

### SearchService tests

Use a fake Qwen client that returns a rejected/invalid interpretation and verify:

- Qwen is called once only;
- `xtall cr weapon` returns `ServiceOutcome("failed")` with `suggested_query == "cr wp xtal"`;
- the latest failed-context entry stores the same suggestion;
- an ambiguous query returns failed with no suggestion;
- valid Qwen search requests are unchanged and do not invoke correction;
- `refuse` and `unavailable` behavior remains unchanged.

### Discord tests

Verify:

- failed outcome with suggestion renders `Did you mean: @Bot cr wp xtal`;
- failed outcome without suggestion retains the current generic examples;
- no automatic execution or confirmation controls are added.

## Non-Goals

This feature does not attempt to:

- make Qwen more capable;
- retry Qwen;
- infer tank/DPS/build concepts;
- correct arbitrary spelling using fuzzy similarity;
- generate several possible corrected queries;
- execute a correction automatically;
- handle multi-stat, comparison, ranking, or boolean-expression correction in the initial version.

Those can be considered later only if real failed-query data shows a need.

## Acceptance Criteria

The design is complete when all of the following are true in implementation:

1. `xtall cr weapon` reaches Qwen only if normal deterministic routing failed.
2. If Qwen fails, the deterministic correction pass suggests exactly `cr wp xtal` without another LLM call.
3. `crit xtal weapon` never guesses Critical Rate or Critical Damage.
4. Unknown meaningful tokens prevent a specific correction.
5. Every emitted suggestion is re-validated by the deterministic parser before display.
6. The suggestion is saved in the existing failed-query context.
7. Discord shows the specific suggestion when available and the existing generic examples otherwise.
8. Successful deterministic and Qwen-assisted search behavior is unchanged.
