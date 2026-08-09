# Upgrade Query Normalization and Discord Display-Name Examples

Date: 2026-08-09

## Goal

Make natural-language upgrade queries deterministic in both `search_items.py` and Discord, while changing Discord examples to show the bot's visible server display name instead of a raw Discord mention ID.

## Scope

This change has two related behaviors:

1. Normalize a small whitelist of natural upgrade phrasings into the existing deterministic `upgrade <crysta name>` intent.
2. Render Discord help/error examples with the bot member's guild `display_name` as plain text, for example `@Toram Search upgrade Don`, instead of `<@123456789...>`.

No database schema, upgrade relationship semantics, Qwen prompt/schema, or build-role semantics change.

## Upgrade Query Semantics

All supported normalized forms mean:

> Find direct crystas whose stored upgrade relationship points to the named base crysta.

The existing direct-successor behavior remains authoritative. The normalized query should reach the same parser/repository path as `upgrade Don` and must not require Qwen.

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

Each natural form extracts only the crysta-name portion and converts it to the existing deterministic upgrade intent. The extracted name then uses the current exact/fuzzy crysta resolution behavior.

Examples:

```text
what upgrades from Don
→ deterministic upgrade lookup for Don

show upgrades for Dno
→ deterministic fuzzy crysta matching for Dno
→ user selects Don if needed
→ direct upgrades from Don
```

### Explicitly unsupported semantic forms

Subjective/build-dependent queries must not be normalized into a deterministic upgrade lookup, including:

- `best upgrade for Don`
- `strongest upgrade for Don`
- `better xtal after Don`
- equivalent wording asking which upgrade is best for a build or role

These remain outside the current explicit-search scope and should follow the existing unsupported/fallback behavior rather than silently treating `best` or `strongest` as irrelevant words.

## Architecture

### Shared normalization

Normalization belongs in shared search parsing/routing code used by both frontends, not in Discord-specific or CLI-specific adapters.

The shared normalizer should:

1. Trim whitespace and simple ending punctuation for pattern matching.
2. Match only the approved phrase shapes.
3. Extract a non-empty crysta-name remainder.
4. Return the equivalent deterministic upgrade query/intent.
5. Leave all other queries unchanged.

This keeps `search_items.py` and `toram_search.service.SearchService` behavior aligned and ensures the supported natural forms bypass Qwen in both frontends.

### Frontend behavior

`search_items.py` continues to render the terminal direct-upgrade results already introduced for `upgrade <name>`.

Discord continues to use `SearchService` and the existing `UpgradeResultsPayload`/pagination UI. The only input change is that the shared parser recognizes the additional natural phrasings.

## Discord Example Name Rendering

Discord-generated examples must use the bot member's visible name in the current guild.

### Source of the name

Use the bot's guild member `display_name` when processing a guild message. This means:

- if the bot has a server nickname, use that nickname;
- otherwise, Discord's `display_name` falls back to the bot account name.

### Rendering format

Examples are presentation-only plain text with a leading `@`, for example:

```text
@Toram Search hp armor
@Toram Search hp > 5000 and cr bow
@Toram Search upgrade Don
@Toram Search what upgrades from Don
```

Do not render raw mention syntax such as `<@123456789012345678>` and do not create a clickable/pinging mention. Existing `AllowedMentions.none()` safety remains unchanged.

### Surfaces to update

Any Discord-generated example or suggestion that currently receives a raw bot mention string should receive the display-name prefix instead, including:

- help examples;
- unsupported-request examples;
- failed-search examples;
- interaction-driven edits that rebuild those messages.

The core help/database service remains Discord-agnostic. Discord performs only presentation substitution; CLI help stays plain and does not gain Discord naming.

## Error and Edge Cases

- Natural upgrade phrase with no crysta name, such as `show upgrades for`, must not fabricate a target. It should use the existing invalid/failed-search behavior.
- If the extracted crysta name has no exact match, existing fuzzy upgrade-name suggestions remain available.
- If a valid base crysta has no direct successors, return the existing no-direct-upgrades result/message.
- If the guild member/display name cannot be obtained for an interaction edit, fall back to the bot account display/name available from the client; never fall back to exposing a raw numeric mention ID in example text.
- The normalization must not strip words such as `best` or `strongest` in order to force a match.

## Testing

Add regression tests proving:

### Shared upgrade normalization

- every approved natural phrase resolves to the same deterministic upgrade intent as `upgrade Don`;
- capitalization and trailing `?`/`.` do not change the result;
- `MustNotCallLLM` is never invoked for supported natural upgrade forms;
- extracted names still use existing exact/fuzzy upgrade resolution;
- subjective forms such as `best upgrade for Don` are not normalized.

### CLI

- `interactive_search()` accepts at least representative natural forms such as `what upgrades from Don` and `show upgrades for Don`;
- terminal output uses the same `Upgrades from Don` direct-successor screen;
- Qwen is not called.

### Discord

- the same representative natural form returns the existing direct-upgrade result payload/UI without Qwen;
- help/error example text contains `@<guild display name>`;
- example text contains no raw `<@...>` mention ID;
- a guild nickname is preferred over the global bot username;
- fallback to the account name works when there is no guild nickname/member-specific name.

## Non-Goals

- Ranking upgrade crystas by usefulness, strength, tank/DPS role, or build suitability.
- Traversing the full upgrade chain from natural-language wording.
- Changing upgrade relationship storage.
- Adding more Qwen responsibility.
- Making display-name example text into a real Discord mention.
