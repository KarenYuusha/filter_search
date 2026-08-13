# Skill Bot Integration Design

## Goal

Integrate the existing provider-agnostic skill retrieval engine into the Discord bot behind an explicit `skill ...` command while preserving all existing item/stat/help/database/Qwen behavior.

This milestone is intentionally retrieval-only:

- users must explicitly start the query with `skill`;
- skill queries are free-text only;
- skill queries return deterministic search results or canonical skill details;
- skill queries never invoke Qwen;
- structured skill-filter parsing, grounded skill Q&A, formula evaluation, and build-role interpretation remain out of scope.

## User-Facing Command Contract

The Discord mention is stripped as it is today. The remaining query is routed as follows:

- `skill` -> skill-search help;
- `skill <query>` -> skill search;
- every other query -> the existing item/stat/help/database/Qwen flow unchanged.

The prefix match is case-insensitive and token-bounded. `skills ...`, `skillful ...`, and unrelated text must not enter the skill system.

Examples:

```text
skill magic finale
skill attack while moving
skill inflict tumble
skill increase critical rate
```

### Empty skill query

`skill` with no text after the prefix returns short deterministic usage help with example skill searches. It does not use Qwen and does not touch item-search failure context.

### Exact match

The skill service first performs canonical/alias exact resolution through `SkillRepository.resolve_skill_name()`.

- One exact match -> return canonical skill detail immediately.
- Multiple exact matches -> return a result list and let the user choose; never guess.
- No exact match -> continue to free-text retrieval.

Exact resolution must occur before provider initialization so an exact lookup does not load Sentence Transformers or execute semantic search.

### Free-text retrieval

For non-exact queries, use the existing `HybridSkillSearcher` with the committed benchmark-selected defaults:

- provider: `sentence-transformers`;
- model: `sentence-transformers/all-MiniLM-L6-v2`;
- config: `symmetric-encode-v1`;
- fusion: `rrf_k=20`, lexical weight `1.5`, semantic weight `1.0`.

The hybrid searcher remains authoritative for ranking. Search scores and channel details are internal diagnostics and are not shown to normal Discord users.

### No results

If retrieval yields no skill hits, return a deterministic no-match response plus a few example queries. Do not send the query to Qwen.

This milestone does not invent a semantic confidence threshold. When the semantic channel is available, nearest-neighbor results are shown as ranked matches even for weak queries. The no-match response is used only when the active retrieval channels actually return no hits, such as lexical-only fallback with no lexical match.

## Architecture

Use a separate frontend-neutral skill-search integration layer rather than extending the existing item `SearchService`.

```text
Discord message
    |
    v
explicit `skill` prefix?
    | yes                     | no
    v                         v
SkillSearchService        existing SearchService
    |                         |
    v                         v
SkillRepository           item/stat/help/database/Qwen
    |
    v
HybridSkillSearcher
  | exact -> canonical detail
  | lexical
  | semantic MiniLM
  ` hybrid fusion
```

Proposed new package:

```text
toram_skill_search/
    __init__.py
    models.py
    service.py
```

Responsibilities:

- `toram_skills`: canonical skill data and retrieval primitives only;
- `toram_skill_embeddings`: provider adapters only;
- `toram_skill_search`: application-level skill command orchestration and frontend-neutral payloads;
- `toram_discord`: prefix routing, async/thread boundary, rendering, and interactions.

Do not import Discord into `toram_skills` or `toram_skill_search`.

## Frontend-Neutral Skill Payloads

The integration layer should expose immutable payloads sufficient for Discord without coupling to Discord objects.

Recommended shapes:

- `SkillDetailPayload(skill, tree)`;
- `SkillResultItem(skill, tree, snippet)`;
- `SkillResultsPayload(query, results)`;
- `SkillHelpPayload(text)`;
- `SkillUnavailablePayload(text)`.

A result item resolves every `SkillSearchHit.skill_id` back to canonical repository data before leaving the service. Similarity output is never treated as canonical fact.

## Skill Detail Presentation

A canonical detail embed may show the fields already stored in `SkillDraft` and `SkillTreeDraft`:

- skill name;
- skill tree;
- tier;
- required level;
- skill type;
- MP cost;
- damage type;
- element;
- cast range;
- hit range;
- cast time;
- hit count;
- ailments;
- weapon requirements;
- weapon restrictions;
- description;
- game description;
- section label/body content.

Fields with no value are omitted rather than rendered as `None`, empty tuples, or placeholders.

Long section content must respect Discord embed limits using the existing truncation utilities. Canonical source data is not modified for presentation.

## Search Result Presentation

Search results use the existing Discord pagination and interaction style where practical.

Each row shows:

- canonical skill name;
- tree name;
- one short deterministic descriptive line derived from canonical fields, preferring `description`, then `game_description`, otherwise concise typed metadata.

Selecting a result opens the full canonical skill detail. Search scores, embedding similarity, RRF values, and channel names are hidden from normal users.

## Runtime and Lazy Semantic Initialization

Skill search uses the generated canonical database at:

```text
coryn_data/database/skills.sqlite
```

Discord configuration gains an independent `skill_database_path`, defaulting to `PROJECT_ROOT / "coryn_data/database/skills.sqlite"`. Item and skill databases remain independently configurable.

The selected MiniLM provider and `SemanticSkillIndex` are initialized lazily and cached at process scope.

Runtime order for a skill query:

1. open `SkillRepository`;
2. run exact name/alias resolution;
3. if exact resolution succeeds, return without provider initialization;
4. otherwise obtain the lazily cached semantic runtime;
5. construct/run `HybridSkillSearcher` with the benchmarked fusion config;
6. resolve returned IDs to canonical skill/tree records;
7. close the per-request repository handle.

The cached semantic runtime must not retain a live request-scoped SQLite connection. Cached state may contain the provider and an immutable in-memory semantic index built from canonical stored vectors.

Concurrent first-use initialization must be safe: at most one successful provider/index initialization should be installed in the cache, while concurrent callers either share it or independently fall back safely without corrupting state.

## Graceful Degradation

### Provider or semantic runtime unavailable

If Sentence Transformers cannot load, provider construction fails, or semantic query embedding is unavailable, return lexical retrieval rather than failing the whole skill command.

### Invalid or stale semantic index

If `SemanticSkillIndex.from_repository()` rejects index provenance, provider identity, dimensions, vector coverage, or document manifest state, degrade to lexical search for that request.

A failed semantic initialization must not permanently disable exact or lexical skill search. Later requests may retry initialization unless a bounded retry/backoff mechanism is added during implementation; such a mechanism must not change user-visible semantics.

### Skill database missing or corrupt

If `skills.sqlite` is missing, unreadable, or fails schema verification, return `SkillUnavailablePayload` with a clear user-facing message. Do not fall through to Qwen and do not affect the item database path.

### Unexpected skill-service errors

The Discord boundary may log the exception, but the user should receive a skill-specific unavailable/error response rather than the generic item-search failure path whenever the request was explicitly a `skill` command.

## Isolation From Existing Item/Qwen Flow

Skill searches must not modify `FailedQueryContext`.

Therefore:

- failed skill queries are not appended to Qwen fallback context;
- successful skill queries do not clear item-search failure history;
- skill queries never call the existing item fallback service;
- non-skill messages never open `skills.sqlite` merely because skill integration exists;
- non-skill messages never initialize or import the heavy Sentence Transformers runtime through request execution.

The existing item `SearchService` remains behaviorally unchanged for this milestone.

## Command Routing Boundary

Prefix recognition belongs above both domain services, at the Discord/application routing layer.

Recommended parser contract:

```python
parse_skill_command(query: str) -> str | None
```

- returns `None` when the first normalized token is not exactly `skill`;
- returns `""` for bare `skill`;
- returns the normalized remainder for `skill <query>` while preserving user text content except surrounding/collapsed whitespace as appropriate for existing command handling.

This parser is deterministic and must not use fuzzy matching or the LLM.

## Discord Integration

`process_tagged_query()` keeps the existing mention/guild/session checks.

After mention extraction:

1. test the explicit skill prefix;
2. if present, run the skill sync helper inside `asyncio.to_thread(...)`;
3. render the returned skill payload with skill-specific embed/view builders;
4. if absent, execute the existing `run_query_sync()` item path unchanged.

Recommended additions include skill equivalents of the existing frontend helpers, for example:

- `run_skill_query_sync()`;
- `run_skill_detail_sync()`;
- `build_skill_help_embed()`;
- `build_skill_results_embed()`;
- `build_skill_detail_embed()`;
- a session-bound skill result view/select for opening detail.

Skill result interaction must respect the same owner/current-generation checks as existing result views.

## Out of Scope

This milestone does not add:

- automatic item-vs-skill intent detection;
- unprefixed skill fallback;
- Qwen or any LLM for skill commands;
- grounded skill Q&A;
- natural-language answer synthesis;
- formal `SkillFilters` syntax such as `tier 4`, `mp<=200`, or weapon filters;
- build labels such as tank, DPS, mage, support, or best skill;
- formula evaluation;
- recommendations or optimization;
- edits to canonical raw skill content.

Text that happens to contain words like `tier`, `sword`, or `mp` after `skill` is treated as ordinary retrieval text in this milestone, not parsed as structured filters.

## Testing Strategy

Add focused tests before implementation changes, following the repository's existing `unittest` style.

### Command routing

- bare `skill` routes to skill help;
- `skill <query>` routes to the skill service;
- case-insensitive `SKILL <query>` works;
- `skills`, `skillful`, and unrelated messages stay on the item path;
- non-skill routing does not open the skill database.

### Service behavior

- one exact canonical name returns detail;
- one exact alias returns detail;
- multiple exact matches return a result list, never an arbitrary detail;
- exact resolution does not initialize the embedding provider;
- free-text query returns canonical ranked results;
- no-hit query returns deterministic no-match behavior when all active channels are empty;
- search hits are resolved back to canonical records.

### Semantic degradation

- provider construction failure -> lexical results;
- `EmbeddingUnavailable` during query -> lexical results;
- stale/invalid semantic-index metadata -> lexical results;
- semantic initialization failure does not break later exact lookup.

### Database isolation

- missing skill DB -> skill unavailable;
- corrupt/schema-invalid skill DB -> skill unavailable;
- item query still works when skill DB is unavailable;
- skill query does not mutate `FailedQueryContext`;
- normal item/stat query does not initialize Sentence Transformers.

### Discord rendering/interactions

- skill help embed;
- compact paginated result embed;
- result selection opens canonical detail;
- empty optional fields are omitted;
- long detail sections are safely truncated;
- stale/foreign-user interaction protections remain enforced.

### Regression

Run focused skill-integration tests, then the complete repository suite:

```bash
python -m unittest discover -s tests -v
```

No implementation is accepted if existing item/stat/help/database/Qwen tests regress.

## Expected File Scope

Likely new files:

```text
toram_skill_search/__init__.py
toram_skill_search/models.py
toram_skill_search/service.py
tests/test_skill_search_service.py
tests/test_discord_skill_search.py
```

Likely modified files:

```text
toram_discord/app.py
toram_discord/config.py
toram_discord/render.py
toram_discord/views.py
.env.example
```

Core retrieval modules under `toram_skills` and provider adapters under `toram_skill_embeddings` should only change if a small integration-safe API adjustment is proven necessary by tests. Do not refactor those modules merely for stylistic consistency.

## Acceptance Criteria

The milestone is complete when all of the following are true:

1. `skill ...` is the only entry into skill search.
2. Bare `skill` returns deterministic usage help.
3. Exact skill/alias lookup returns canonical detail without initializing MiniLM.
4. Non-exact free-text skill queries use the existing benchmark-selected hybrid retrieval configuration when semantic runtime is available.
5. Semantic/provider/index failures degrade to lexical retrieval.
6. Skill search never invokes Qwen or mutates item failed-query context.
7. Missing/corrupt `skills.sqlite` does not affect item/stat search.
8. Ranked Discord results can open canonical skill detail.
9. Existing item/stat/help/database/Qwen behavior remains unchanged for non-skill messages.
10. Focused integration tests and the full repository test suite pass.
