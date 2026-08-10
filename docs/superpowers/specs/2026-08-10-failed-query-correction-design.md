# Hybrid Query Interpretation and Safe Reconstruction Design

Date: 2026-08-10
Status: Approved design, pending user review

## Goal

Users should be able to search naturally without memorizing a special query syntax, while simple searches remain fast and all database behavior remains deterministic and validated.

The system should treat the existing compact syntax as an internal/fast-path language, not as the language users are expected to learn.

The core rule is:

**LLM interprets meaning. Python validates. The database answers.**

Qwen must never directly answer item facts from memory, choose database rows, or write arbitrary SQL.

## Long-Term Architecture

Use four stages in order:

1. existing deterministic parser;
2. deterministic reconstruction of known entities in flexible order;
3. one constrained Qwen interpretation call when deterministic understanding is not confident;
4. deterministic validation, clarification, or safe guidance.

Conceptually:

```text
User query
   |
   v
Normalization
   |
   v
Existing deterministic parser
   | success
   +------------------------------> validate/materialize -> database
   |
   v
Deterministic reconstruction
   | high confidence
   +------------------------------> validate/materialize -> database
   |
   v
Qwen constrained interpreter
   |
   v
Strict deterministic validator
   | valid                    | ambiguous             | invalid
   v                          v                       v
database                  ask user             safe suggestion/help
```

This preserves the speed advantage of deterministic parsing without requiring the deterministic grammar to understand every possible English sentence.

## Design Principles

### 1. Users do not need to learn syntax

All of these should be capable of expressing the same search intent over time:

- `cr wp xtal`
- `weapon xtal cr`
- `xtall cr weapon`
- `show me weapon crysta with critical rate`
- `what weapon xtals give crit rate?`
- `I need cr on weapon xtal`

They should compile into the same validated internal representation.

The compact syntax remains useful for power users and as a deterministic intermediate form, but documentation and error handling should not imply that memorizing it is required.

### 2. Keep the exact parser as the fastest path

Queries already understood by the existing deterministic parser should continue to bypass Qwen entirely.

Examples:

- `cr bow`
- `hp armor`
- `hp > 5000 armor`

Do not route every query through Qwen merely because natural language support exists.

### 3. Add deterministic reconstruction before Qwen

Introduce a frontend-neutral deterministic reconstruction component that recognizes known search entities even when their order differs from the compact grammar.

Its responsibility is approximately:

`try_reconstruct_search(raw_query, repository) -> ReconstructionResult`

A reconstruction result should distinguish at least:

- `success`: one fully validated, executable interpretation;
- `ambiguous`: known terms were found but deterministic clarification is required;
- `no_match`: not enough information is understood safely;
- `unsafe`: unknown/conflicting meaningful tokens prevent reconstruction.

The implementation should reuse parser-owned sources of truth rather than copying aliases or item filters into a second catalog.

Existing sources include:

- `ITEM_WORD_ALIASES`;
- `STAT_ALIASES`;
- ambiguous stat groups such as `crit`;
- existing item-filter candidates such as `wp xtal` / `weapon xtal`;
- repository stat and item-type catalogs.

For the first version, reconstruction should focus on a common simple-search shape:

- exactly one resolvable stat;
- exactly one resolvable item filter;
- optional conservative filler words;
- arbitrary order of recognized stat/filter tokens.

Do not initially add comparison, AND/OR, ranking, or multi-stat reconstruction unless the existing parser already accepts the reconstructed canonical query. Those capabilities can be added incrementally behind tests as real failed-query examples appear.

### 4. High-confidence reconstruction may execute directly

If every meaningful part of the user query is consumed by one unambiguous interpretation and the reconstructed request passes deterministic validation, execute it without Qwen.

Example:

- input: `xtall cr weapon`;
- `xtall -> xtal` through the existing explicit word alias;
- `cr -> Critical Rate` through an existing unambiguous stat alias;
- `weapon + xtal -> Weapon Crysta + Red Enhancer` through the existing item-filter catalog;
- all meaningful input is consumed;
- the reconstructed request is re-parsed/re-validated;
- execute the search directly.

This means the target example should normally stop involving Qwen after this feature is implemented.

### 5. Ambiguity remains deterministic

Qwen must not override known database/parser ambiguity.

Example:

`crit weapon xtal`

If `crit` is intentionally ambiguous between Critical Rate and Critical Damage, the system should ask the existing deterministic clarification question rather than trusting Qwen to choose one meaning.

Whenever Qwen emits a stat/filter term, pass it through the same deterministic resolution rules used by ordinary parsing.

### 6. Qwen is a constrained intent + slot interpreter, not the executor

When deterministic parsing and reconstruction cannot confidently understand the query, call Qwen once.

Qwen should classify the request and emit constrained structured data such as:

```json
{
  "intent": "search",
  "candidates": [
    {
      "item_filter": "weapon xtal",
      "stats": [
        {"name": "Critical Rate"}
      ],
      "match": "all",
      "sort_stat": "Critical Rate"
    }
  ]
}
```

Supported high-level intents remain narrow:

- item/stat search;
- allowed database metadata/count action;
- help;
- unsupported/refuse.

Qwen must not return prose answers for database facts and must not invent unsupported build concepts.

### 7. Strict validation after Qwen

A Qwen interpretation is only a proposal.

Python must verify, as applicable:

- intent is allowed;
- payload schema is exact;
- stat names/aliases resolve through the repository/parser;
- item filters are supported;
- operators and numeric values are valid;
- requested sort stat belongs to the request;
- ambiguous parser terms trigger clarification instead of silent selection;
- unsupported concepts are refused;
- the final request can be materialized through the deterministic search engine.

Only after this validation should the database be searched.

## Normalization and Reconstruction Rules

### Explicit normalization only

Normalize case, whitespace, punctuation, and existing intentional word aliases first.

Example:

`xtall` may become `xtal` because `xtall` is already an explicit alias.

Do not use unconstrained fuzzy semantic guessing in the executable reconstruction path.

### Stat recognition

For automatic deterministic reconstruction:

- canonical stat name -> accepted;
- exact unambiguous stat alias such as `cr` -> accepted;
- ambiguous alias such as `crit` -> clarification, not guessing;
- fuzzy stat similarity -> not auto-executed;
- unknown stat-like token -> reconstruction fails closed.

### Item-filter recognition

Use the existing filter catalog.

For reconstruction only, a known multi-token filter may be recognized as an unordered token group when doing so yields one exact filter interpretation.

Example:

- `weapon xtal` and `xtal weapon` may resolve to the same existing filter.

If multiple filter candidates match the same consumed token set, do not choose one automatically.

### Meaningful-token consumption

Automatic reconstruction is high-confidence only when every meaningful token is accounted for by:

- a resolved stat phrase;
- a resolved item-filter phrase;
- syntax/operator tokens explicitly supported by that reconstruction mode; or
- a conservative set of harmless natural-language filler words.

A small filler vocabulary may include words already present in supported natural grammar, for example `find`, `show`, `me`, `with`, `has`, `have`, `give`, `gives`, `which`, and articles where appropriate.

The filler set must not absorb arbitrary unknown words.

Examples:

- `xtall cr weapon` -> direct deterministic search;
- `weapon xtall cr` -> direct deterministic search;
- `find xtall cr weapon` -> direct deterministic search if all tokens are consumed;
- `xtall blah weapon` -> no deterministic reconstruction because `blah` is unresolved;
- `crit xtal weapon` -> deterministic clarification because `crit` is ambiguous.

## Service Data Flow

Update `SearchService.handle_query` conceptually as follows:

1. Run existing `route_deterministically`.
2. If it returns search/help/database/refuse, preserve current behavior.
3. Before recording a failure or invoking Qwen, run deterministic reconstruction.
4. If reconstruction returns one validated search:
   - materialize and return it as an ordinary `search` outcome;
   - do not call Qwen;
   - do not add a failed-query history entry.
5. If reconstruction identifies deterministic ambiguity:
   - return the existing clarification payload;
   - do not call Qwen merely to resolve that known ambiguity.
6. Otherwise record the failed input as today and call Qwen exactly once.
7. Validate the Qwen payload deterministically.
8. If valid, keep the existing confirmation/search flow.
9. If `refuse`, preserve the unsupported-request flow.
10. If `unavailable`, preserve the infrastructure-unavailable flow.
11. If Qwen interpretation is invalid/failed, try to produce safe query-specific guidance as described below; otherwise use generic help.

## Post-Qwen Failure Guidance

The original requirement remains useful: when a query truly reaches Qwen and Qwen still cannot produce a valid interpretation, prefer a query-specific example over generic syntax examples when it can be generated safely.

However, this is now a **guidance path**, not the main way to repair simple reordered queries. Cases such as `xtall cr weapon` should already have been handled by deterministic reconstruction before Qwen.

Create a helper approximately equivalent to:

`try_suggest_query(raw_query, repository) -> str | None`

It should reuse the same normalization/entity-recognition primitives as deterministic reconstruction, but it may return guidance in cases that are safe to explain yet not eligible for automatic execution under the current reconstruction feature set.

A suggestion may be shown only when:

- it resolves to exactly one parser-supported canonical query;
- all meaningful user tokens are accounted for;
- no ambiguous stat/filter choice is hidden;
- the generated query re-parses successfully through the deterministic parser;
- the parsed result matches the entities recognized from the original input.

If these conditions are not met, return no specific suggestion.

Do not make a second Qwen call to generate corrections.

## Failed-Query Context

Continue using `FailedQueryContext`.

Ordering should be:

- deterministic parser/reconstruction successes do not enter failed history;
- a query is recorded when it actually needs the Qwen fallback;
- Qwen sees prior failed inputs and prior safe suggestions;
- if Qwen fails and deterministic guidance produces a suggestion, save it with `context.set_latest_suggestion(...)`;
- a later Qwen call may use that previous suggestion as context.

No new session store is required.

## Discord Presentation

### Successful reconstruction

A high-confidence reconstructed query behaves like any other successful search. Do not display a correction warning merely because the user used different word order or an explicit known typo alias.

### Failed Qwen with safe suggestion

When `ServiceOutcome.kind == "failed"` contains a safe `suggested_query`, show a query-specific message such as:

`Did you mean: @Bot <suggested query>`

Do not execute that suggestion automatically from the failure response.

### Failed Qwen without safe suggestion

Preserve the existing generic examples.

This keeps error handling useful without pretending the user must memorize those examples as the required syntax.

## Why Qwen Is Not the Sole Router

Routing every query through Qwen would simplify the apparent control flow but has undesirable properties for this project:

- common searches become slower;
- availability of Ollama becomes a dependency for syntax the program already knows;
- deterministic aliases and ambiguity rules become easier for Qwen to accidentally override;
- more LLM calls increase variability and make failures harder to reproduce;
- the database still requires deterministic validation afterward.

The preferred split is therefore:

- deterministic code for fast exact/high-confidence cases;
- Qwen for semantic interpretation of genuinely natural or unusual wording;
- deterministic code again for validation and execution.

## Extensibility

Do not grow one giant natural-language regex grammar.

When new user-language cases appear, classify them into one of three categories:

1. **Known entities in a new order or with an explicit alias** -> extend deterministic reconstruction.
2. **A genuinely new natural-language phrasing with the same supported semantics** -> rely on or improve the Qwen structured interpreter, adding regression examples rather than many phrase-specific regexes.
3. **A genuinely new search capability** -> first extend the internal structured search model and deterministic executor/validator, then teach both deterministic reconstruction and Qwen about that capability.

This keeps language understanding separate from search capability.

Future features such as build-oriented queries should not be enabled merely by adding Qwen prompt wording. They require a deliberate new deterministic capability/model first.

## Error Handling Rules

The system fails closed when:

- unresolved meaningful tokens remain in executable reconstruction;
- a stat/filter is ambiguous;
- multiple deterministic reconstructions are equally valid;
- Qwen payload validation fails;
- a generated suggestion cannot be re-parsed into the intended search;
- the query asks for unsupported concepts.

Never silently drop unknown meaningful words to force a match.

## Testing

### Deterministic reconstruction tests

Positive cases:

- `xtall cr weapon` -> successful search without Qwen;
- `weapon xtall cr` -> same search without Qwen;
- `find xtall cr weapon` -> same search without Qwen;
- standard exact queries remain unchanged;
- returned reconstructed requests pass the real deterministic parser/materializer.

Negative/clarification cases:

- `crit xtal weapon` -> existing Critical Rate / Critical Damage clarification without Qwen choosing one;
- `xtall blah weapon` -> reconstruction refuses to consume `blah`;
- conflicting or multiple filter matches -> no automatic reconstruction;
- unsupported complex expressions are not partially reconstructed.

### SearchService tests

Verify:

- a high-confidence reconstructed query does not call Qwen at all;
- a normal deterministic query still does not call Qwen;
- a genuinely natural unresolved query calls Qwen once;
- valid Qwen structured output is validated and preserved;
- ambiguous deterministic terms are clarified rather than delegated to Qwen;
- `refuse` and `unavailable` behavior remains unchanged;
- failed Qwen interpretation may attach a safe suggestion;
- failed Qwen interpretation with no safe suggestion falls back to generic help;
- failed-query context is only recorded when the query actually reaches fallback.

### Qwen contract tests

Verify that Qwen is constrained to the allowed schema and cannot:

- return SQL;
- return arbitrary database facts in prose;
- introduce unsupported intent types;
- use invalid operators/values;
- silently bypass stat/filter validation.

### Discord tests

Verify:

- pre-Qwen reconstruction renders ordinary search results;
- failed outcome with a safe suggestion renders the specific `Did you mean` query;
- failed outcome without a suggestion retains generic examples;
- no automatic execution is attached to a post-failure suggestion.

## Non-Goals for the Initial Implementation

The initial implementation does not attempt to:

- make Qwen the only router;
- route every search through an LLM;
- add a second LLM correction call;
- infer tank/DPS/build concepts;
- use broad fuzzy semantic matching for automatic execution;
- implement every English construction in regex;
- add new search semantics such as build scoring merely through prompting.

## Acceptance Criteria

The design is complete in implementation when all of the following are true:

1. Users are not required to use canonical token order for simple known stat + item-filter searches.
2. `xtall cr weapon` executes as the intended Critical Rate weapon-crysta search without calling Qwen.
3. `crit xtal weapon` never silently chooses Critical Rate or Critical Damage.
4. Unknown meaningful tokens prevent automatic deterministic reconstruction.
5. Simple existing deterministic searches remain on the fast path.
6. Natural queries not understood deterministically receive at most one Qwen interpretation call.
7. Every Qwen search proposal passes strict deterministic validation before database execution.
8. Qwen cannot directly answer item facts from memory or execute arbitrary SQL.
9. Safe failed-query suggestions are parser-validated before display and never auto-executed from the failure message.
10. Failed-query history is recorded only for inputs that actually enter fallback.
11. Existing help/database/refuse behavior remains constrained to the supported capabilities.
12. The architecture can add future search capabilities by extending the structured model/executor first, then language interpretation, rather than by growing an unbounded regex grammar.
