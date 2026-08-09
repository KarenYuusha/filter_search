# Deterministic Natural-Language Search Normalization Design

## Goal

Make common conversational item/stat queries user-friendly without calling Qwen when the request can be reduced safely to the existing deterministic search grammar.

Examples that must work without an LLM:

- `can you find armor with hp` → Armor filtered by MaxHP
- `show me bow with cr` → Bow filtered by Critical Rate
- `give me armor with physical resistance` → Armor filtered by Physical Resistance %
- `which bows have critical rate` → Bow filtered by Critical Rate
- `which bow has the highest critical rate` → Bow filtered by Critical Rate, using the existing highest-first ordering

## Scope

This change only normalizes simple conversational wrappers around explicit item types and explicit stats. It does not infer semantic roles or builds such as `tank xtal`, `dps gear`, or other gameplay recommendations.

Qwen remains the fallback for requests that cannot be reduced deterministically.

## Architecture

Put the normalization inside `toram_data/stat_query.py`, which is already the shared deterministic parser used by `search_items.py` before Qwen routing. `looks_like_stat_expression()` first checks whether the input matches a supported conversational shell; `parse_stat_expression()` applies the same normalization before its existing item-filter/stat parsing.

No new search semantics are introduced. The normalizer only rearranges a validated conversational shell into the existing grammar, then the existing aliases, ambiguity handling, item-filter extraction, and stat resolution remain authoritative.

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

For these forms, `X` is treated as the item-filter phrase and `Y` as the stat expression. The rewrite is only produced if `X` resolves completely to a known item filter. A single-word plural item phrase may be singularized only when the singular form then resolves completely to a known item filter, e.g. `bows` → `bow`.

Inside a supported conversational shell, a leading `the highest`, `highest`, `best`, or `most` on `Y` is removed because the existing bare stat search already sorts highest-first. This does not add a new ranking direction or scoring rule.

## Data Flow

```text
user query
  ↓
parse_search_query()
  ↓
looks_like_stat_expression()
  ↓
conversational shell normalization
  ↓
existing stat/item filter parser
  ↓ if supported
normal deterministic search route
  ↓ otherwise
Qwen fallback
```

## Safety / Ambiguity

- Never guess unknown item types.
- Never strip arbitrary extra words from the item side.
- Never infer model/build concepts.
- Preserve existing stat ambiguity behavior (`crit` remains ambiguous unless the deterministic parser already has a defined choice path).
- Do not turn arbitrary prose into a search just because it contains `with`.
- A rewrite is accepted only when the item-filter side resolves completely.
- `tank armor with hp` must not normalize to plain Armor + MaxHP because `tank armor` is not a complete item-filter phrase; existing refusal logic remains in control.
- `lowest` / `minimum` are not normalized because the existing bare stat search is highest-first and this change does not add ascending sort semantics.

## Ranking

Natural `highest` / `best` / `most` wording is treated as a conversational wrapper for the existing highest-first stat search. No new ranking direction is added, and `lowest` remains unsupported by this normalization layer.

## Testing

Add focused tests proving:

1. `can you find armor with hp` routes directly to a deterministic stat expression and resolves Armor + MaxHP.
2. `show me bow with cr` routes directly and resolves Bow + Critical Rate.
3. `which bow has the highest critical rate` routes directly using existing highest-first ordering.
4. `which bows have critical rate` routes directly using safe singularization.
5. `armor having hp` routes directly.
6. unsupported prose still routes to Qwen fallback rather than being guessed.
7. out-of-scope build language remains refused.
8. direct deterministic search syntax is unchanged.
