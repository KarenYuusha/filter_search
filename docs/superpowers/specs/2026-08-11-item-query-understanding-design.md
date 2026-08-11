# Item Query Understanding and Safe Clarification Design

Date: 2026-08-11

## Status

Approved design for the next item-search improvement. This design builds on the existing hybrid query interpretation flow and keeps the current deterministic parser as the fast path and source of truth.

This document is intentionally limited to item search. Boss, skill, map, NPC, build-role, and other future domains are out of scope.

## Goals

Improve item-search failures and ambiguous input without making the bot more willing to guess.

The feature should:

- keep exact valid searches fast and frictionless;
- explain what the bot safely understood when a query cannot execute;
- use structured choices for known semantic ambiguity;
- use fuzzy matching only as confirmation guidance, never as silent execution;
- distinguish harmless wording problems from uncertainty that can change search results;
- require a final confirmation when several independent corrections were needed;
- delay Qwen until deterministic understanding cannot safely resolve or explain the request;
- validate every non-direct interpretation deterministically before database execution;
- preserve the existing rule that Qwen does not invent game data, choose result rows, or write arbitrary SQL.

## Non-goals

This iteration does not add:

- boss or skill search;
- `tank`, `dps`, `mage build`, or similar semantic build-role interpretation;
- new multi-stat Boolean/relation syntax beyond what the current parser already supports;
- numeric confidence scoring;
- fuzzy auto-execution;
- LLM-generated database facts;
- arbitrary SQL generation;
- a major Discord UI redesign unrelated to clarification and confirmation;
- persistent analytics or telemetry.

## Core Safety Principle

Only fully resolved deterministic meaning may execute.

If uncertainty can change the search result, the uncertainty must be visible to the user and resolved before execution.

The system therefore distinguishes four understanding states for recognized query parts:

1. `RESOLVED` — exact match or intentional alias; safe to use directly.
2. `AMBIGUOUS` — two or more valid meanings; requires structured clarification.
3. `FUZZY` — one likely typo or near-match; requires explicit confirmation.
4. `UNKNOWN` — cannot be mapped safely; must remain visible and must not be silently discarded.

Intentional aliases such as `cr`, `hp`, and configured item-word aliases remain deterministic behavior and do not count as fuzzy corrections.

## Architecture

The existing deterministic parser remains the first stage.

```text
raw query
   |
   v
existing deterministic parser
   |
   +-- valid exact/alias query -----------------> execute
   |
   v
item query understanding
   |
   +-- fully resolved --------------------------> execute
   +-- semantic ambiguity ----------------------> clarify
   +-- fuzzy correction ------------------------> confirm
   +-- partial understanding -------------------> explain / safe suggestion
   +-- unresolved structure --------------------> Qwen interpretation
                                                    |
                                                    v
                                           deterministic validation
                                                    |
                                +-------------------+-------------------+
                                |                   |                   |
                              clarify             confirm              fail
```

The new understanding layer is frontend-neutral. It describes what is known and what remains uncertain; it does not contain Discord-specific presentation text.

## `ItemQueryUnderstanding`

Introduce a dedicated representation for deterministic understanding of an item query.

Conceptually:

```text
ItemQueryUnderstanding
├── resolved_parts
│   ├── stats
│   └── item_filter
├── uncertainties
│   ├── ambiguity
│   └── fuzzy_match
├── unresolved_tokens
├── canonical_query
├── source
└── decision
    ├── execute
    ├── clarify
    ├── confirm
    ├── suggest
    └── fallback
```

The exact Python names may be adjusted during implementation to follow existing repository conventions, but the responsibilities must remain separate from Discord rendering.

### Resolved parts

Resolved parts are entities whose meaning is deterministic. Examples:

- `cr` -> `Critical Rate` through an intentional alias;
- `weapon xtal` -> the configured Weapon Crysta filter;
- exact stat and item-type names.

### Uncertainties

Uncertainties contain only cases where user input needs a decision before execution.

Supported uncertainty classes in this iteration are:

- known semantic ambiguity, such as `crit` -> `Critical Rate` or `Critical Damage`;
- strong fuzzy match, such as `crtical rate` -> likely `Critical Rate`.

If a query would require new Boolean/relation interpretation between multiple stats, it is not added by this feature. It remains governed by current parser behavior or falls through to the existing safe fallback/refusal path.

### Unresolved tokens

Unknown meaningful input must stay visible in `unresolved_tokens`.

Known harmless syntax/filler may be accepted according to an explicit allowlist, but the system must not treat arbitrary unknown words as disposable filler.

This prevents a query from becoming executable merely because an unrecognized meaningful word was silently removed.

### Canonical query

When the deterministic understanding is complete enough to form one parser-supported query, it may include a canonical query string such as:

```text
cr wp xtal
```

The canonical query is secondary UI information. Human-readable labels remain the primary presentation.

### Source

Interpretations should retain their origin so policy can distinguish deterministic understanding from LLM inference. Expected source categories include deterministic/reconstructed and LLM.

An interpretation inferred by Qwen is never eligible for direct execution merely because all returned names are valid.

## Decision Rules

### Execute

Execute immediately only when every meaningful part is deterministically resolved through exact matching, intentional aliases, or the existing safe deterministic reconstruction rules.

Examples:

```text
cr weapon xtal
```

and the already-supported reconstruction:

```text
xtall cr weapon
```

may execute if they uniquely map to the same semantics as the canonical parser query.

A valid exact query must not be second-guessed because another related filter could hypothetically have been intended. For example, if `cr weapon` is already a valid exact search for main weapons, the system executes that meaning rather than asking whether the user meant weapon crysta.

### Clarify

Use structured choices when multiple known meanings can change results.

Example:

```text
crit weapon xtal
```

The bot may already resolve Weapon Crysta, but `crit` remains ambiguous between Critical Rate and Critical Damage. It must ask the user to choose rather than guess.

Known ambiguity should prefer enumerated options/buttons when the safe choices are known. Open-ended clarification is only a fallback when safe choices cannot be enumerated.

### Confirm fuzzy corrections

A strong fuzzy match is guidance only.

Example:

```text
crtical rate weapon xtal
```

may produce:

```text
"crtical rate" -> Critical Rate (FUZZY)
Weapon Crysta -> RESOLVED
```

The bot asks the user to confirm `Critical Rate` before execution.

No fuzzy match may silently execute, even when there is only one high-scoring candidate.

### Partial understanding and safe suggestions

When the system safely recognizes part of a query but unknown meaningful input prevents execution, it should expose both sides.

Example:

```text
cr weapon xtal blah
```

may yield:

```text
Understood:
- Critical Rate
- Weapon Crysta

Not safely understood:
- blah
```

If removing or correcting the unresolved wording produces exactly one parser-supported canonical query through deterministic logic, the bot may additionally offer one safe suggestion.

The suggestion must be validated by the existing parser/router before presentation and is never auto-executed.

Human-readable meaning appears first, for example:

```text
Suggested search: Critical Rate + Weapon Crysta
Search form: cr wp xtal
```

### Fallback

Qwen is used only after:

1. the existing deterministic parser fails;
2. deterministic item-query understanding cannot safely execute, clarify, confirm, or explain the query with a validated suggestion;
3. the remaining unresolved structure genuinely requires interpretation.

The goal is to avoid spending an LLM call merely to produce nicer prose for a deterministic failure.

Qwen receives the existing constrained context and returns structured interpretation candidates only.

Every LLM interpretation is routed back through deterministic domain validation. Qwen may interpret user intent and terms, but it does not establish database truth.

An LLM-derived interpretation requires user confirmation before execution, even when every returned field validates against the database.

If Qwen fails validation, the bot refuses or reports the remaining uncertainty rather than guessing.

## Clarification Policy

The chosen policy is hybrid:

- semantic uncertainty is resolved one meaningful decision at a time;
- harmless wording problems and purely unrecognized wording are summarized together;
- if several independent corrections or interpretations were required, show one final reconstructed-search confirmation before execution.

### Single semantic clarification

For:

```text
crit weapon xtal
```

Discord may show:

```text
Weapon Crysta

What does `crit` mean?
[Critical Rate] [Critical Damage]
```

After the user chooses one option, the completed request is revalidated. Because only one semantic ambiguity was resolved, the search executes immediately if nothing else remains uncertain.

### Single fuzzy correction

For:

```text
crtical rate weapon xtal
```

Discord may show:

```text
Did you mean Critical Rate?
[Yes] [No]
```

A single confirmed correction may execute immediately after deterministic revalidation.

### Multiple independent corrections

For a query requiring more than one independent correction, the system accumulates the confirmed decisions and then presents the completed meaning once.

Example presentation:

```text
Ready to search

Stat: Critical Rate
Item type: Weapon Crysta

Search form: cr wp xtal

[Search] [Edit]
```

The final confirmation is required because multiple independent interpretation decisions contributed to the completed query.

### Multiple uncertainty types

When semantic ambiguity and fuzzy correction coexist, resolve semantic ambiguity first, then the remaining fuzzy correction if it is still relevant. If multiple independent decisions were required in total, require the final Search/Edit confirmation.

This ordering applies only to uncertainty types already supported by the item parser/understanding layer; it does not add new multi-stat relation semantics.

## Pending Clarification State

Use minimal structured state rather than a conversational transcript.

Conceptually:

```text
PendingItemSearch
├── original_query
├── resolved_parts
├── pending_clarification
├── confirmed_corrections
└── canonical_query_if_complete
```

This state exists only while an item query is unresolved.

It should support deterministic continuation after the user chooses a clarification or confirms a correction.

Clear pending state when:

- the search executes successfully;
- the user explicitly cancels;
- the user sends a new raw item-search query instead of responding through the pending clarification/confirmation interaction;
- deterministic revalidation shows that the pending interpretation is no longer valid.

A new raw item-search query supersedes the old pending query. The bot does not try to infer that two independent raw queries belong to the same clarification sequence.

The existing failed-query context remains separate and can continue to provide short context to Qwen. Deterministic clarification, fuzzy confirmation, and safe-suggestion paths do not create failed-query history merely because they required user interaction. The failed query is recorded when the service actually enters Qwen fallback, matching the existing hybrid-search intent.

The clarification state should not be replaced by a long transcript of user interactions.

## Human-Readable Presentation

Discord should present semantic labels first and canonical syntax second.

Preferred form:

```text
I understood
- Critical Rate
- Weapon Crysta

I couldn't safely interpret
- blah

Suggested search: Critical Rate + Weapon Crysta
Search form: cr wp xtal
```

Internal parser terms, confidence numbers, raw debug traces, and reason-code names are not user-facing.

## Internal Reason Codes

Use explicit categorical reasons for tests, logs, and presentation mapping instead of hidden numeric confidence thresholds.

Expected reason categories include:

- `EXACT_MATCH`
- `ALIAS_MATCH`
- `AMBIGUOUS_STAT`
- `FUZZY_STAT`
- `FUZZY_FILTER`
- `UNKNOWN_TOKEN`
- `MULTIPLE_CORRECTIONS`
- `LLM_INTERPRETATION`
- `LLM_REJECTED`

Names may be adjusted to fit code conventions, but behavior must remain categorical and inspectable.

## Fuzzy Matching Constraints

Fuzzy matching exists only to generate confirmation candidates.

A fuzzy candidate must:

- map to an entity that exists in the current deterministic catalog;
- be sufficiently strong under a conservative deterministic threshold;
- be unique enough that presenting one candidate is defensible;
- never execute until confirmed;
- fail closed when multiple plausible candidates remain.

Exact aliases continue to take precedence over fuzzy logic.

The implementation plan should define the actual matching algorithm and thresholds only after inspecting the existing alias/fuzzy helpers, so the new behavior reuses repository conventions rather than introducing a second unrelated fuzzy system.

## Database Boundary

The database is reached only by validated deterministic requests.

The understanding layer, fuzzy matcher, Qwen, and Discord UI must not directly choose result rows.

LLM output cannot provide trusted item facts. It may only propose structured intent that is checked against the real stat/filter catalogs and existing search-request validation.

## Failure Behavior

Failure responses should be specific when deterministic information exists.

Preferred distinctions include:

- unknown stat or term;
- unknown item filter;
- known ambiguous stat;
- fuzzy stat candidate awaiting confirmation;
- fuzzy filter candidate awaiting confirmation;
- unsupported query shape;
- unresolved meaningful tokens;
- Qwen unavailable;
- Qwen interpretation rejected by validation.

If no safe specific correction can be produced, show generic parser-supported examples rather than an invented suggestion.

## Testing Strategy

### Core invariants

Tests must enforce these guarantees:

1. Exact valid queries never require confirmation.
2. Intentional aliases may execute directly.
3. Known semantic ambiguity never auto-executes.
4. Fuzzy corrections never auto-execute before confirmation.
5. Unknown meaningful tokens never silently disappear on a path that executes.
6. Qwen interpretations never directly reach database execution.
7. Multiple independent corrections require final confirmation.
8. Successful completed searches clear pending clarification state.
9. A parser-validated suggestion is guidance only and is not executed automatically.
10. An oversized or computationally unsafe reconstruction still fails closed under the existing reconstruction budget/guard behavior.
11. Deterministic clarification/confirmation paths do not pollute failed-query history; entering Qwen fallback does.

### Representative cases

At minimum cover:

```text
cr weapon xtal
-> execute

crit weapon xtal
-> clarify Critical Rate / Critical Damage

crtical rate weapon xtal
-> fuzzy confirmation

cr weapon xtal blah
-> partial understanding + unresolved `blah`

crtical wepon xtal
-> resolve corrections -> final Search confirmation

crit wepon xtal
-> semantic ambiguity first
-> fuzzy correction if still required
-> final confirmation because multiple decisions were needed
```

### Differential equivalence tests

Any reconstructed or confirmed query that becomes executable must produce the same parsed search semantics as its canonical query.

For example:

```text
xtall cr weapon
```

must be semantically equivalent to:

```text
cr wp xtal
```

before execution is allowed.

### Fuzz/property-style safety tests

Add generated or table-driven adversarial cases verifying that unknown meaningful words cannot be inserted into an otherwise valid query and then disappear on an auto-execution path.

Also test combinations of exact aliases, known ambiguous terms, repeated tokens, typo candidates, and the existing reconstruction-size guard.

## Compatibility with Current Hybrid Search

This design is an evolution of the current hybrid flow, not a replacement.

The existing order remains conceptually:

1. deterministic parser;
2. deterministic understanding/reconstruction;
3. one constrained Qwen fallback when still needed;
4. deterministic validation;
5. database execution only for resolved requests;
6. safe failure/suggestion behavior.

The main change is that deterministic understanding becomes explicit and structured enough to support helpful explanations and multi-turn confirmation, instead of collapsing every non-executable case into a generic failure or immediately handing it to Qwen.

## Future Extensibility

The model is intentionally item-specific for this iteration.

A future boss or skill domain may reuse the high-level pattern—deterministic fast path, structured understanding, clarification, constrained LLM interpretation, deterministic validation—without reusing item-specific request fields or item-specific catalogs.

No generic multi-domain abstraction should be introduced in this implementation unless the item feature itself requires it.

## Acceptance Criteria

The design is successfully implemented when:

- current exact item searches remain behaviorally unchanged and fast;
- `crit`-style known ambiguity is surfaced through structured choices;
- fuzzy stat/filter corrections require confirmation and never silently execute;
- partially understood queries report recognized and unresolved meaning in human-readable form;
- canonical query syntax is available as secondary guidance;
- one resolved ambiguity/correction can execute after revalidation;
- multiple independent corrections require one final Search/Edit confirmation;
- Qwen is skipped when deterministic understanding can already safely handle or explain the query;
- any Qwen-derived search requires deterministic validation and user confirmation;
- unknown meaningful input cannot disappear on an auto-execution path;
- pending clarification state is minimal, deterministic, and cleared after completion/cancellation/supersession/invalidation;
- deterministic clarification/confirmation does not create failed-query history unless Qwen fallback is entered;
- regression tests demonstrate semantic equivalence between reconstructed/confirmed requests and canonical parser requests;
- no new multi-stat relation semantics are introduced by this feature;
- the existing known unrelated baseline test failure is not treated as a regression caused by this feature.
