# Deterministic Natural-Language Search Normalization Design

## Goal

Make common conversational item/stat queries user-friendly without calling Qwen when the request can be reduced safely to the existing deterministic search grammar.

Examples that must work without an LLM:

- `can you find armor with hp` → Armor filtered by MaxHP
- `show me bow with cr` → Bow filtered by Critical Rate
- `give me armor with physical resistance` → Armor filtered by Physical Resistance %
- `which bows have critical rate` → Bow filtered by Critical Rate

## Scope

This change only normalizes simple conversational wrappers around explicit item types and explicit stats. It does not infer semantic roles or builds such as `tank xtal`, `dps gear`, or other gameplay recommendations.

Qwen remains the fallback for requests that cannot be reduced deterministically.

## Architecture

Add a small pure helper in `search_items.py` that attempts to normalize supported conversational forms into existing search text. `route_deterministically()` tries the original query first, then tries the normalized query. The normalized text is fed into the same existing parser and stat/item-type resolvers; no new search semantics or SQL path is introduced.

Supported wrapper families:

- `can you find X with Y`
- `find X with Y`
- `show me X with Y`
- `give me X with Y`
- `I want X with Y`
- `X that has Y`
- `X that have Y`
- `X having Y`
- `which X has Y`
- `which X have Y`

For these forms, `X` is treated as the item-filter phrase and `Y` as the stat expression. The helper only returns a rewrite if `X` resolves completely to a known item filter and the resulting deterministic parser resolves the search without unknown stats.

## Data Flow

```text
user query
  ↓
existing deterministic parse
  ↓ if not already supported
conversational normalizer
  ↓
validated deterministic rewrite
  ↓
existing parser/resolvers
  ↓ if supported
SQLite search
  ↓ otherwise
Qwen fallback
```

## Safety / Ambiguity

- Never guess unknown item types.
- Never fuzzy-resolve model/build concepts.
- Preserve existing stat ambiguity behavior (`crit` remains ambiguous unless the deterministic parser already has a defined choice path).
- Do not turn arbitrary prose into a search just because it contains `with`.
- A rewrite is accepted only when the item-filter side resolves completely and the rewritten query is accepted by the existing deterministic parser.

## Ranking

This spec does not add new ranking-direction semantics. Existing deterministic handling for `best`/`highest` remains unchanged.

## Testing

Add focused tests proving:

1. `can you find armor with hp` routes directly to search and resolves Armor + MaxHP.
2. `show me bow with cr` routes directly to search and resolves Bow + Critical Rate.
3. `which bows have critical rate` routes directly to search.
4. unsupported prose still routes to Qwen fallback rather than being guessed.
5. out-of-scope build language remains refused.
6. direct deterministic search syntax is unchanged.
