# Upgrade Query Normalization, Natural Multi-Stat Search, and Discord Display-Name Examples

Date: 2026-08-09

## Goal

Keep Discord and `search_items.py` aligned on three deterministic behaviors:

1. Natural upgrade wording resolves to the complete connected crysta upgrade chain, regardless of which item in the chain is queried.
2. Plain natural multi-stat wording such as `bow has cr and ampr` is parsed by the existing deterministic stat-expression engine instead of falling through to Qwen/failed-search handling.
3. Discord-generated examples show the bot's visible guild display name instead of a raw Discord mention ID.

The database remains authoritative. Qwen is not used when one of the deterministic forms below is recognized.

## Scope

This change corrects the previously documented upgrade semantics and extends the existing natural stat grammar.

It does not change:

- the SQLite upgrade relationship storage;
- numeric/stat search semantics;
- Qwen schemas or prompts;
- build-role semantics such as tank/DPS recommendations;
- the requirement that Discord users actually mention the bot when starting a query.

## Upgrade Query Semantics

All supported upgrade forms mean:

> Identify the named crysta, then return the complete connected upgrade component containing it, including predecessors, successors, branches, and the selected crysta itself.

The same chain must be returned whether the user queries the first, middle, or last crysta in that connected component.

For example, if the stored relationships are:

```text
A → B → C → D
```

then all of these queries show the same full chain:

```text
upgrade A
upgrade B
upgrade C
upgrade D
```

The selected item remains visible in the result title/context so the user knows which crysta they asked about.

### Supported forms

The following forms are supported case-insensitively and with simple trailing punctuation ignored:

- `upgrade Don`
- `upgrade for Don`
- `upgrades for Don`
- `show upgrades for Don`
- `find upgrades for Don`
- `what upgrades from Don`
- `what can upgrade Don`
- `what comes after Don`
- `next xtal after Don`

Each natural form extracts only the crysta-name portion and routes to the same deterministic upgrade-chain lookup as `upgrade <crysta name>`.

### Fuzzy upgrade-name behavior

If the extracted name is not an exact crysta match, use the existing fuzzy crysta-name selection flow only to identify the intended base item.

After the user selects a crysta candidate, show that selected crysta's complete connected upgrade chain. Do not open normal item detail and do not limit the result to direct successors.

Example:

```text
show upgrades for Dno
→ fuzzy candidate: Don
→ user selects Don
→ complete upgrade chain containing Don
```

### Last-item behavior

A crysta with no successors is not a "no upgrades" case when it has predecessors. Querying the final item in a chain must still show the complete chain discovered through predecessor traversal.

Only an isolated crysta with no predecessor or successor relationship may render as a one-node chain.

### Subjective forms remain unsupported

Do not normalize subjective/build-dependent queries such as:

- `best upgrade for Don`
- `strongest upgrade for Don`
- `better xtal after Don`
- equivalent wording asking which upgrade is best for a role/build

These remain outside the explicit-search scope.

## Upgrade Architecture

The repository already exposes `get_upgrade_component(item_id)`, which traverses both predecessor and successor relationships. That graph is the authoritative result for an exact or selected upgrade lookup.

Shared service behavior should materialize an exact upgrade lookup as an upgrade-chain payload based on `get_upgrade_component()` rather than a direct-successor result list.

Frontend responsibilities:

- `search_items.py` renders the full upgrade graph/tree for the selected crysta.
- Discord uses the existing upgrade-chain/detail payload and embed renderer.
- fuzzy upgrade search still uses a candidate list first; selecting a candidate transitions to the full graph view.

No second graph traversal implementation should be added.

## Natural Multi-Stat Search

### Required natural forms

Plain item-type-first grammar must support:

```text
bow has cr and ampr
bows have cr and ampr
armor has hp and physical resistance
xtal has cr and ampr
```

The same grammar should support more than two stats when joined by the existing Boolean operators, for example:

```text
bow has cr and ampr and stability
```

`and` keeps the existing AND semantics: each returned item must satisfy all clauses in the same AND group.

The existing `or` semantics remain unchanged where supported by the stat-expression parser.

### Normalization

This should be an extension of the shared natural-stat normalizer in `toram_data/stat_query.py`, not a Discord-only or CLI-only rewrite.

Recognize the additional shapes:

```text
<item type> has <stat expression>
<plural item type> have <stat expression>
```

Normalize them into the existing stat-expression format by moving the item filter to the expression edge, just like the already-supported forms such as `which bow has ...`, `bow having ...`, and `find bow with ...`.

Examples:

```text
bow has cr and ampr
→ cr and ampr bow
→ existing stat-expression parser

bows have cr and ampr
→ cr and ampr bow
→ existing stat-expression parser
```

The normalized output is an internal representation only; user-facing output may continue to show the resolved canonical stats and filter.

### Alias and ambiguity behavior

Existing stat alias resolution remains authoritative.

Examples:

```text
cr
→ Critical Rate

ampr
→ Attack MP Recovery

crt
→ ambiguous: Critical Rate or Critical Damage
```

Therefore:

```text
bow has cr and ampr
```

must execute deterministically without Qwen, while:

```text
bow has crt and ampr
```

must produce the existing Critical Rate / Critical Damage clarification and then continue the same AND search after the user chooses.

The system must not guess which meaning of `crt` the user intended.

### Failure behavior

A recognized item-type-first `has/have` query must not fall through to `I couldn't interpret that search` solely because of the grammar shape.

If a stat term itself is unknown, use the existing stat-resolution/failed-query behavior. Do not invent a stat meaning.

## Shared Routing

Both upgrade normalization and natural multi-stat normalization belong in shared parsing layers consumed by both frontends.

The intended flow is:

```text
user query
→ shared deterministic normalization/parsing
→ exact/ambiguous stat or upgrade resolution
→ deterministic SQLite query/graph traversal
→ frontend rendering
```

Qwen is reached only when deterministic routing genuinely cannot interpret the request.

## Discord Display-Name Example Rendering

Discord-generated examples must use the bot member's visible name in the current guild.

### Source of the name

Use the bot's guild member `display_name` when processing a guild message:

- if the bot has a server nickname, use that nickname;
- otherwise fall back to the bot account display/name.

### Rendering format

Examples are presentation-only plain text with a leading `@`, for example:

```text
@Toram Search hp armor
@Toram Search hp > 5000 and cr bow
@Toram Search upgrade Don
@Toram Search what upgrades from Don
```

Do not render raw mention syntax such as `<@123456789012345678>` and do not create a clickable/pinging mention. Existing allowed-mentions safety remains unchanged.

### Surfaces to update

Any Discord-generated example or suggestion that currently receives a raw bot mention string should receive the display-name prefix instead, including:

- help examples;
- unsupported-request examples;
- failed-search examples;
- interaction-driven edits that rebuild those messages.

The core help/database service remains Discord-agnostic.

## Error and Edge Cases

- Natural upgrade phrase with no crysta name must not fabricate a target.
- Exact upgrade lookup of the first, middle, or last node returns the same connected component.
- A branching upgrade component includes every reachable predecessor/successor branch.
- Cycles, if malformed data ever contains them, must not loop forever; existing visited-node graph traversal remains authoritative.
- Missing referenced upgrade nodes remain represented through the existing `missing_nodes` graph behavior.
- Fuzzy upgrade candidate selection transitions to the whole chain for the selected candidate.
- `bow has crt and ampr` asks for `crt` clarification and preserves the `ampr` clause plus Bow filter.
- `bow has cr and ampr` executes without Qwen.
- Unknown stat terms are not guessed.
- If a guild display name cannot be obtained, fall back to the bot account name, never a raw numeric mention ID in example text.

## Testing

### Upgrade regression tests

Add tests proving:

- `upgrade <first node>` returns the complete component;
- `upgrade <middle node>` returns the same complete component;
- `upgrade <last node>` still returns the complete component even with no successors;
- the selected item ID/name is retained for title/context;
- every approved natural upgrade phrase reaches the same chain behavior without Qwen;
- fuzzy natural upgrade lookup → candidate selection → complete chain;
- an isolated crysta yields a valid one-node chain rather than a false direct-successor failure;
- subjective forms such as `best upgrade for Don` are not normalized.

### Natural multi-stat regression tests

Add tests proving:

- `bow has cr and ampr` parses as one AND group with Bow filter;
- `bows have cr and ampr` behaves identically;
- three or more `and` stats preserve all clauses;
- unambiguous aliases bypass Qwen;
- `bow has crt and ampr` returns the existing ambiguity clarification for `crt` while retaining the other clause/filter;
- after choosing Critical Rate or Critical Damage, execution returns items satisfying both selected `crt` meaning and AMPR;
- both CLI and Discord consume the same shared parsing behavior;
- these recognized forms do not render `I couldn't interpret that search`.

### Discord display-name tests

Keep tests proving:

- examples contain `@<guild display name>`;
- a guild nickname is preferred over the account username;
- account-name fallback works;
- example text contains no raw `<@...>` mention ID.

## Non-Goals

- Ranking crysta upgrades by usefulness, strength, tank/DPS role, or build suitability.
- Adding semantic build interpretation.
- Changing upgrade relationship storage.
- Adding another upgrade graph implementation.
- Adding more Qwen responsibility.
- Guessing ambiguous stats such as `crt`.
- Making display-name example text into a real Discord mention.
