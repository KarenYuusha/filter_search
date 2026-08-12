# Skill Hybrid Search Design

Date: 2026-08-12
Status: Approved design, pending written-spec review

## 1. Purpose

Add a skill knowledge/search subsystem to the Toram Discord bot using the new `raw_skills/` corpus. The subsystem must preserve the project's existing philosophy: deterministic data access first, local LLM use only when it adds value, and no unsupported game facts invented from model knowledge.

The skill subsystem is separate from the existing item subsystem. Item search keeps its current behavior and Qwen integration. Skill search uses `gemma4:e4b` through Ollama for skill-specific natural-language interpretation and grounded explanations when deterministic handling is insufficient.

The target end state is hybrid skill retrieval: structured filters + lexical full-text search + semantic retrieval, fused into ranked skill results. Retrieved canonical skill records remain authoritative; search indexes and LLM output are secondary layers.

## 2. Source Data and Constraints

The source corpus lives under:

- `raw_skills/assist_skills/`
- `raw_skills/other_skill_trees/`
- `raw_skills/sub_weapon_skills/`
- `raw_skills/weapon_class_skills/`

The files are intentionally treated as source material, not as a uniform schema. Some trees, such as Battle, Alchemy, and much of Magic, expose regular labels for tier, required level, MP cost, damage type, formulas, ailments, and bonuses. Other trees, such as Assassin and Minstrel, contain freer prose, repeated labels, informal notes, source commentary, uncertain formulas, and tree-wide rules.

The importer must therefore be lossless with respect to source text: failure to normalize a fact must never cause that fact to disappear. Unrecognized or uncertain content is preserved in named text sections and/or the original raw skill block.

No LLM is used to generate the canonical database from `raw_skills/`. Import must be deterministic and reproducible.

## 3. Scope

### In scope

- deterministic import of the complete `raw_skills/` corpus
- canonical skill and skill-tree records
- a separate `skills.sqlite` database
- import validation and warning reporting
- exact skill-name and alias lookup
- skill-tree browsing
- structured skill filtering
- SQLite FTS5 lexical search
- local semantic embeddings and vector similarity
- deterministic result fusion
- skill query routing
- `gemma4:e4b` for skill-query interpretation when deterministic parsing is insufficient
- grounded `gemma4:e4b` explanations over retrieved skill data
- Discord skill detail, browsing, discovery, and follow-up flows
- deterministic formula display
- a later formula-evaluation layer built on explicit supported formulas
- retrieval and grounding tests, including Ollama-unavailable behavior

### Out of scope for the first skill system

- changing existing item-search semantics or its Qwen model
- general Toram knowledge outside imported item/skill data
- build recommendations such as "best mage build", "best tank skill", or "what should I take"
- letting Gemma use its own Toram knowledge as an authority
- letting Gemma directly execute database queries without validation
- letting Gemma authoritatively calculate arbitrary formulas
- requiring every raw note or mechanic to be normalized into fixed columns
- a hosted vector service such as Pinecone
- a general conversation assistant

## 4. High-Level Architecture

```text
Discord
   |
Query Router
   |
   +--------------------+
   |                    |
 Items                Skills
   |                    |
existing system      Skill Router
                        |
           +------------+-------------+
           |            |             |
      Structured       FTS5        Semantic
        Search         Search        Search
           |            |             |
           +------------+-------------+
                        |
                   Rank Fusion
                        |
                 Skill Repository
                        |
              +---------+----------+
              |                    |
        Direct Answer         gemma4:e4b
                              grounded only
              |                    |
              +---------+----------+
                        |
                     Discord
```

The canonical repository is the source of truth. FTS and vector indexes exist only to retrieve candidate canonical records and can be regenerated from the database.

## 5. Canonical Data Model

### 5.1 SkillTreeRecord

Each source file maps to a skill tree record containing at least:

- stable tree identifier
- canonical tree name
- normalized tree name
- tree group: assist / other / sub-weapon / weapon-class
- source file path
- tier requirements where deterministically extractable
- tree-wide weapon restrictions where deterministically extractable
- tree-wide general rules and notes
- original general-information text
- parse warnings

Tree-wide rules are not duplicated into every skill unless a searchable derived property is intentionally materialized. A skill answer may combine its skill record with its parent tree record when tree-wide restrictions or mechanics are relevant.

### 5.2 SkillRecord

Each skill record contains a stable identifier plus these categories.

Identity:
- canonical name
- normalized name
- aliases
- tree identifier
- source file
- source order

Progression/type:
- tier
- required level
- skill type when reliably extractable

Common structured mechanics, when reliably extractable:
- MP cost or MP-cost expression
- damage type
- element
- cast/activation range
- hit range
- cast time
- hit count
- ailments
- weapon requirements/limitations
- weapon bonuses/penalties

Text sections:
- description
- game description
- effects/mechanics
- formulas
- conditions
- bonuses/penalties
- notes
- related information
- source details
- other named sections that do not fit the normalized schema

Provenance/safety:
- original raw skill block
- parse warnings
- uncertainty/source-commentary markers when the source explicitly indicates uncertainty

Fields are nullable. Missing structured data does not mean the fact is absent from the source; the raw/text sections remain available for lexical/semantic retrieval and display.

## 6. Import Pipeline

The importer runs deterministically:

```text
raw file
  -> extract tree-level prefix/general information
  -> split skill blocks
  -> identify skill name
  -> parse common labeled fields
  -> preserve named sections
  -> normalize safe structured values
  -> validate
  -> write canonical records
```

### Parsing principles

1. Prefer explicit source labels over inference.
2. Preserve original wording for formulas and uncertain mechanics.
3. Normalize known variants conservatively, for example `MP cost` and `MP Cost`.
4. Do not infer a damage type, ailment, weapon restriction, or tier from general game knowledge.
5. A parser failure for one field must not drop the skill.
6. A block without a safely identifiable skill name is an import error and must be reported rather than silently guessed.
7. Explicit source uncertainty remains uncertainty in the canonical record.

### Import report

Every import produces machine-readable and human-readable validation results including:

- files discovered/parsed
- trees created
- skills created
- duplicate normalized names
- blocks without safely identifiable names
- missing commonly expected fields by tree
- unknown/unparsed section labels
- malformed numeric fields
- parse warnings

The import milestone is not accepted until the report is reviewed and every source skill block is either represented by a canonical record or explicitly listed as an import error.

## 7. Storage

Create a separate database:

`coryn_data/database/skills.sqlite`

It remains separate from `items.sqlite` because the schemas, import lifecycle, retrieval indexes, and future evolution differ.

The database contains canonical skill/tree tables, structured child tables as appropriate for multi-valued properties, FTS5 indexes, and metadata needed to rebuild semantic indexes.

The raw source files remain the editable source of truth for re-import. Generated search data is reproducible and disposable.

## 8. Retrieval Channels

### 8.1 Exact-name fast path

Exact/canonical/alias resolution runs before general hybrid search. Queries such as `magic finale`, `assassin stab`, or `critical up` should resolve without Gemma and without semantic retrieval when unambiguous.

Reasonable typo handling may reuse the project's confirmation-first fuzzy-search philosophy: fuzzy name matches are suggestions/choices, not silent identity changes.

### 8.2 Structured retrieval

Structured retrieval is authoritative for fields such as:

- tree
- tree group
- tier
- required level
- skill type
- MP cost when represented numerically
- damage type
- ailment
- weapon restriction/requirement

Examples:

- `tier 4 magic skills`
- `passive battle skills`
- `magic skills that tumble`
- `dagger skills`
- `skills under 300 mp`

The deterministic query parser handles common forms before invoking Gemma.

### 8.3 Lexical retrieval

SQLite FTS5 indexes normalized searchable text including:

- name and aliases
- tree name
- descriptions
- game descriptions
- mechanics/effects
- formulas
- conditions
- notes
- weapon bonuses/penalties

Lexical retrieval is important for exact Toram terminology and abbreviations such as skill names, `AMPR`, `Tumble`, `Venom Stack`, `Physical Pierce`, and similar terms.

### 8.4 Semantic retrieval

Semantic retrieval covers concept-based discovery that is not reliably represented by one structured field, such as:

- `skills that work with evasion`
- `skills that help recover mp while fighting`
- `skills related to poison stacking`
- `skills that reduce damage while doing something else`

Embeddings are generated locally using a dedicated embedding model, not `gemma4:e4b`.

The embedding provider is configurable behind a small interface. Before freezing the default model, the retrieval milestone benchmarks a small shortlist of current local Ollama embedding models on the golden skill-query set. The selected default must be the best-performing candidate that satisfies both of these constraints:

- top-5 semantic retrieval quality is not worse than the other candidates on the benchmark set
- embedding generation and in-memory query latency are acceptable for local bot deployment

The selected model name is then committed to configuration and regression tests; changing it later requires rerunning the retrieval benchmark.

Because the corpus is small, initial vector storage is local. Precomputed vectors are loaded into memory and searched by cosine similarity. No external vector database is required.

### 8.5 Semantic document granularity

Each skill has a whole-skill summary document plus section-level semantic documents for long mechanics. Every semantic document contains `skill_id` and section identity so retrieval always resolves back to canonical records.

Do not split raw files into arbitrary fixed token windows. Chunking follows skill and section boundaries.

## 9. Hybrid Fusion and Ranking

Hybrid search runs only the channels useful for a query. Exact name resolution can bypass it entirely.

When multiple channels participate, each returns canonical skill IDs plus channel scores. Result fusion is deterministic. The first implementation uses rank-based fusion rather than asking Gemma to rerank results.

Structured matches receive explicit boosts when they correspond to extracted user constraints. Exact lexical name matches outrank semantic similarity. Semantic similarity is mainly used to improve recall for conceptual queries.

Weights/fusion constants are tuned against a golden retrieval set rather than selected by intuition. Acceptance metrics include top-1, top-3, and top-5 relevance for representative query classes.

## 10. Skill Query Routing

The skill router recognizes these classes:

1. exact skill lookup
2. tree browse/list
3. structured skill search
4. hybrid/concept discovery
5. question about a resolved skill
6. unsupported recommendation/general request

Deterministic routing and parsing run first.

Examples:

- `magic finale` -> exact lookup
- `magic skills` -> tree browse
- `tier 4 magic skills` -> structured search
- `skills that work with evasion` -> hybrid discovery
- `how does shadow walk work?` -> resolved-skill Q&A
- `make me the best mage build` -> unsupported/refuse for this phase

If deterministic interpretation is incomplete but the request is still within the skill-search domain, `gemma4:e4b` may produce a typed skill-search intent for validation.

## 11. Gemma Usage

### 11.1 Model ownership

`gemma4:e4b` is the skill subsystem's language model through Ollama.

It is used only for:

- interpreting in-domain skill queries that deterministic parsing cannot safely resolve
- explaining/summarizing already retrieved skill information
- selecting which retrieved sections are relevant to an in-domain question when this cannot be done deterministically

It is not used for:

- building the canonical skill database
- authoritative retrieval ranking
- inventing missing skill facts
- answering general Toram questions from model knowledge
- build recommendations in this phase
- authoritative arbitrary formula calculation

### 11.2 Typed interpretation

Gemma returns a constrained skill-intent structure rather than directly writing SQL or an answer. The structure can contain only supported concepts such as:

- intent kind
- candidate skill/tree identity
- validated structured filters
- free-text semantic concepts
- requested answer section/question

All referenced entities and filters are validated against the canonical skill repository.

### 11.3 Grounding

The database-question grounding lesson applies here from the start.

Argument-bearing Gemma interpretations must be grounded in the current user input or explicit active skill context. Gemma cannot introduce a tree, ailment, weapon, skill name, tier, or other restrictive filter merely because it exists in the database.

Grounding uses the same canonical resolvers and alias rules used by deterministic parsing. A schema-valid but ungrounded interpretation is rejected before retrieval/execution.

### 11.4 Grounded explanations

For resolved-skill Q&A:

```text
question
 -> resolve active/referenced skill
 -> retrieve canonical skill + relevant tree rules/sections
 -> deterministic answer if direct field is sufficient
 -> otherwise send only retrieved evidence to Gemma
 -> grounded explanation
```

The model prompt requires answers to use only supplied skill data. If the supplied data does not support the requested fact, the bot says the skill database does not contain enough information.

Direct fields such as MP cost, tier, required level, and explicitly stored ailments are answered without Gemma.

## 12. Formula Handling

Phase 1 preserves and displays formulas as source-backed text.

Formula evaluation is a later, separate capability. Only formulas that have an explicit supported deterministic evaluator are calculated. Inputs such as skill level, player level, INT, or other variables must be explicit or safely resolved from the current request/context.

Gemma may explain a formula but is not the authoritative calculator. Deterministic code produces evaluated values.

Unsupported or ambiguous formulas remain display-only rather than being guessed.

## 13. Discord Experience

### Exact skill lookup

A compact detail card shows high-value structured fields and a short description. Long information is separated into views such as:

- Mechanics
- Formula
- Bonuses / Restrictions
- Notes

The UI must not dump the entire raw source block by default.

### Tree browsing

A tree query groups skills by tier and allows a user to select a skill for detail.

### Discovery results

Hybrid queries return a ranked skill list with short evidence-based match summaries. Selecting a result opens the canonical detail view.

### Follow-up context

After a user resolves/selects a skill, the Discord session stores the active `skill_id`. Follow-ups such as `what about staff?`, `show formula`, or `what level unlocks it?` resolve against that active skill.

Item failure/search context and skill context remain separate so the two domains do not contaminate each other.

## 14. Failure and Availability Behavior

- Exact, structured, lexical, and already-built semantic retrieval continue to function when Ollama/Gemma is unavailable.
- Queries that require Gemma interpretation return a clear unavailable/clarification response rather than guessing.
- Direct database facts remain available without Gemma.
- If embeddings are unavailable at runtime but lexical/structured search is available, retrieval degrades to those channels and does not fail the whole skill subsystem.
- Import errors fail visibly through the validation report; they do not silently produce invented records.
- Unsupported build/general advice is refused even if Gemma could answer from pretrained knowledge.

## 15. Testing Strategy

### Import tests

- representative structured tree (Battle/Alchemy)
- structured-but-rich tree (Magic)
- free-form tree (Assassin)
- heavily tree-rule-driven/free-form tree (Minstrel)
- all source blocks accounted for
- unknown labels preserved
- uncertain source notes preserved as uncertainty
- malformed blocks reported without fabricated data

### Repository/structured search tests

- exact names and aliases
- tree listing
- tier/type/MP/ailment/weapon filters
- duplicate/ambiguous names
- fuzzy names require confirmation where appropriate

### Lexical tests

- exact terminology and abbreviations
- formula/mechanic phrases
- no irrelevant result leakage above configured thresholds

### Semantic/hybrid tests

Maintain a golden set of roughly 50-100 representative queries spanning exact, typo, structured, lexical, conceptual, ambiguous, misleading, and unsupported requests.

Measure top-1/top-3/top-5 relevance. Benchmark embedding candidates and tune fusion with this set.

### Gemma tests

Use fake/deterministic model responses to prove:

- valid grounded interpretation accepted
- schema-valid but ungrounded filters rejected
- invented skill/tree/entity rejected
- direct facts bypass Gemma
- explanation receives only retrieved evidence
- unsupported fact leads to insufficient-data response
- build/general request refused
- Ollama unavailable does not break deterministic skill search

### Discord tests

- exact skill card
- tree browse
- discovery list
- selecting a skill
- detail tabs/buttons
- active-skill follow-ups
- stale controls/session isolation
- item and skill contexts remain independent

## 16. Delivery Milestones

### Milestone 1: Canonical skill data

Deliver:
- corpus inventory/audit
- canonical models
- deterministic importer
- `skills.sqlite`
- validation report
- import tests

Exit condition: every source skill block is represented or explicitly reported as an import error, and no information is silently discarded when normalization fails.

### Milestone 2: Hybrid retrieval

Deliver:
- exact-name resolver
- structured search
- FTS5 search
- embedding benchmark and selected local embedding model
- semantic index
- deterministic rank fusion
- golden retrieval benchmark

Exit condition: retrieval quality meets the agreed golden-query expectations without Gemma being required for normal exact/structured queries.

### Milestone 3: Gemma interpretation and grounded Q&A

Deliver:
- skill-specific `gemma4:e4b` client/configuration
- typed intent schema
- grounding validator
- grounded explanation path
- refusal/unavailable behavior

Exit condition: Gemma cannot introduce ungrounded database entities or answer unsupported skill facts from pretrained knowledge.

### Milestone 4: Discord integration

Deliver:
- skill cards
- tree browsing
- discovery results
- active-skill follow-ups
- session isolation
- Discord smoke-test checklist

Exit condition: real Discord smoke testing passes with Gemma available and unavailable.

### Milestone 5: Deterministic formula evaluation

Deliver only after retrieval/Q&A is stable:
- explicitly supported formula AST/evaluator or equivalent deterministic representation
- variable validation
- evaluated-result formatting
- comparison support for formulas that are safely comparable

This milestone does not expand into build recommendation logic.

## 17. Success Criteria

The finished subsystem should satisfy these principles:

1. `Magic: Finale` and other known skills resolve quickly without an LLM.
2. Structured questions use structured data rather than semantic guessing.
3. Exact Toram terminology benefits from lexical search.
4. Conceptual discovery benefits from semantic retrieval.
5. Hybrid fusion is deterministic and benchmarked.
6. `gemma4:e4b` is used only when natural-language reasoning/explanation adds value.
7. Gemma answers only from retrieved canonical data.
8. Raw source information and explicit uncertainty are preserved.
9. Ollama failure does not take down deterministic skill search.
10. Item search remains behaviorally independent.
11. Build recommendations remain outside this phase.
12. Formula calculation, when added, is deterministic rather than model-authored.
