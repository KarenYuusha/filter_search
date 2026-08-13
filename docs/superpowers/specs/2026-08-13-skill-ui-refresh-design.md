# Clean Skill UI Refresh Design

## Goal

Replace the current text-heavy skill search/tree/detail presentation with a clean, minimal Discord-native card layout, using the checked-in `coryn_skill_icons` assets without changing skill search semantics, tree resolution, pagination rules, or item/stat behavior.

This design is based on the approved clean/minimal mockup rather than an RPG-heavy visual theme.

## Scope

In scope:

- Restyle ordinary `skill <words>` result pages.
- Restyle `skill tree <tree name>` result pages.
- Restyle direct and selected skill detail pages.
- Display the selected/visible skills' icons from `coryn_skill_icons` when a deterministic icon match exists.
- Preserve existing result pagination, select menus, Back navigation, owner checks, and generation checks.
- Preserve every non-empty detail field/section while reorganizing it for readability.
- Gracefully fall back to iconless embeds when an asset is missing.

Out of scope:

- Changing skill search/ranking behavior.
- Changing tree-name resolution or fuzzy thresholds.
- Changing skill parser/database/schema/raw skill data.
- Generating, resizing, editing, or recoloring icon assets.
- Adding tree-family color themes or RPG badge systems.
- Changing item/stat/Qwen behavior.

## Visual Direction

The presentation should look like a polished Discord bot rather than a pasted text block.

### Result pages

A result page uses a stack of embeds:

1. One compact header embed.
2. Up to five compact skill-card embeds, matching the existing `PAGE_SIZE = 5`.
3. Existing select menu and Previous/Next controls below the embeds.

A tree-list header contains:

- canonical tree name as title;
- total skill count;
- footer `Page X / Y` when the tree spans more than one page.

An ordinary search header contains:

- `Skill search: <query>` as title;
- `Closest matches first` plus the visible range/total count;
- footer `Page X / Y` when needed.

Each skill card contains:

- title: `<absolute result number>. <canonical skill name>`;
- one compact metadata line;
- one short preview when available;
- the matched skill icon as a small thumbnail when available.

Tree-list metadata omits the tree name because the header already scopes the page. Ordinary search metadata includes the tree name because results can come from different trees.

No decorative emoji, tier colors, damage-type colors, or per-tree color system is added in this phase.

### Detail pages

A detail page remains one embed per logical detail page, with the current Previous/Next and Back controls.

Header:

- title: canonical skill name;
- description metadata: tree name, Tier, Required Lv when present;
- selected skill icon as thumbnail when available.

The fixed structured information is split into short inline fields rather than multiline `Overview` and `Range / Timing` text blocks.

Inline field order:

1. Type
2. MP Cost
3. Damage
4. Element
5. Cast Range
6. Hit Range
7. Cast Time
8. Hit Count

Missing values are omitted.

Full-width sections follow in this order when present:

1. Ailments
2. Weapon requirements
3. Weapon restrictions
4. Description
5. Game description
6. Parsed source sections in original source order

Long values continue to use the existing lossless pagination/splitting system. The redesign must never discard text to make a page look cleaner.

Footer behavior remains `Page X / Y` only when a skill detail spans multiple pages.

## Icon Assets

The icon source of truth is the checked-in `coryn_skill_icons` directory.

Observed organization:

```text
coryn_skill_icons/
  Shield/
    Shield Bash.png
    Shield Cannon.png
    Shield Mastery.png
    ...
  Magic/
    Magic_ Finale.png
    Magic_ Burst.png
    ...
  Blade/
  Support/
  ...
```

The icon system must not depend on exact punctuation or spacing. For example:

```text
skill name: MAGIC: FINALE
icon stem:  Magic_ Finale
```

Both normalize to the same comparison key.

### Normalization

Use one deterministic normalization for icon folder names and skill filename stems:

- Unicode NFKC normalization;
- case-fold;
- keep only alphanumeric characters.

Examples:

```text
Magic_ Finale  -> magicfinale
MAGIC: FINALE  -> magicfinale
Dark Power     -> darkpower
DarkPower      -> darkpower
Dual Sword     -> dualsword
DualSword      -> dualsword
```

No fuzzy matching is used for icons.

### Tree-folder resolution

For a canonical tree name:

1. Remove a trailing `Skills` token.
2. Normalize the remaining name.
3. Match against normalized directory names under `coryn_skill_icons`.
4. Apply a small explicit alias table only for true naming mismatches that normalization cannot solve.

Initial explicit aliases:

```text
Magic Warrior -> MagicBlade
Blacksmith    -> Smith
```

Additional aliases may be added only when an actual canonical tree cannot resolve through normalization.

### Skill-icon resolution

Resolution order:

1. Resolve the tree folder and find an exact normalized filename-stem match inside it.
2. If the tree folder cannot resolve or has no matching skill, consult a global normalized skill-stem index.
3. Use the global match only when exactly one icon file has that normalized stem.
4. Otherwise return no icon.

Missing or ambiguous icons are not errors and never block the Discord response.

### Caching

Build the folder and skill indexes lazily and cache them for the process lifetime. Result rendering must not recursively rescan hundreds of files for each message.

## Discord Attachment Strategy

A Discord embed can have one thumbnail, so per-result icons require separate compact embeds rather than multiple icon-bearing fields in a single embed.

With `PAGE_SIZE = 5`, a list message uses at most:

- 6 embeds: 1 header + 5 skill cards;
- 5 icon file attachments.

A detail message uses:

- 1 embed;
- at most 1 icon attachment.

The render layer creates fresh `discord.File` instances per send/edit operation. Attachment filenames are safe deterministic names rather than raw skill names, e.g. `skill-a1b2c3d4e5f6.png`.

Embeds refer to local icons with:

```text
attachment://skill-a1b2c3d4e5f6.png
```

When a component interaction changes the result page or switches between result/detail views, the edit operation replaces both the embed list and attachment list so stale icon attachments are removed.

## Render Message Contract

The current skill renderer returns a single `(embed, view)` pair. The redesign needs a richer internal message contract.

Add a render model conceptually equivalent to:

```python
@dataclass
class SkillRenderedMessage:
    embeds: tuple[discord.Embed, ...]
    files: tuple[discord.File, ...]
    view: discord.ui.View | None
```

`build_skill_payload_message()` returns this object.

Initial bot replies use:

```text
embeds=<rendered embeds>
files=<rendered files>
view=<rendered view>
```

Component edits use:

```text
embeds=<rendered embeds>
attachments=<fresh rendered files>
view=<rendered view>
```

A single helper should own interaction editing so pagination, selection, Back, confirmation, and choice flows do not duplicate attachment replacement logic.

Help/not-found/confirmation/choice payloads remain simple one-embed messages with no icon attachments unless a selected canonical skill is actually being displayed.

## Component Behavior

Existing behavior stays unchanged:

- five results per page;
- Previous and Next preserve session page state;
- result dropdown values remain trusted indexes, not raw skill IDs;
- selecting a skill opens page 1 of its detail view;
- Back returns to the exact previous result page;
- direct skill detail has no Back button;
- owner and generation protections remain active;
- tree confirmation/choice actions continue using trusted canonical tree IDs.

Only rendering and attachment transport change.

## Error Handling

Icon failures are presentation failures, not search failures.

The renderer must silently omit the thumbnail when:

- the icon root does not exist;
- a tree folder does not resolve;
- the expected file is missing;
- a global filename match is ambiguous;
- a file disappears between lookup and render.

The skill payload itself still renders normally.

Actual Discord send/edit exceptions continue through the current skill-specific error boundary.

## Testing

### Icon resolver tests

Cover:

- `Shield Skills` + `Shield Bash` -> `Shield/Shield Bash.png`;
- punctuation normalization: `MAGIC: FINALE` -> `Magic/Magic_ Finale.png`;
- space/case normalization;
- `Magic Warrior Skills` -> `MagicBlade` alias;
- `Blacksmith Skills` -> `Smith` alias;
- globally unique fallback;
- globally ambiguous stem -> no icon;
- missing root/file -> no icon;
- cache/index reuse.

### Result rendering tests

Cover both ordinary search and tree lists:

- one header + maximum five skill-card embeds;
- five results per page remains unchanged;
- card title, metadata, and preview hierarchy;
- tree name omitted from tree cards but present on ordinary search cards;
- icon thumbnail present when matched;
- no thumbnail and no error when missing;
- safe unique attachment filenames;
- no literal `None`;
- all embed/file counts remain below Discord limits.

### Detail rendering tests

Cover:

- thumbnail icon attached for a matching skill;
- header contains tree/Tier/Required Lv;
- fixed metadata is rendered as inline fields in the approved order;
- missing fixed values are omitted;
- long text fields remain full-width and losslessly paginated;
- Description, Game description, and late parsed sections remain present;
- footer behavior remains correct;
- direct detail versus result-selected Back behavior remains unchanged.

### Interaction tests

Cover:

- Previous/Next replace embeds and attachments together;
- selecting a result replaces list icons with the selected detail icon;
- Back replaces the detail icon with icons for the correct prior result page;
- fuzzy tree confirmation and choice flows still open the correct tree list;
- owner/generation protections remain unchanged.

### Regression tests

Run the existing focused skill tests plus the full repository suite. No item/stat or parser/database behavior should change.

## Acceptance Criteria

The refresh is complete when:

1. Skill result/tree pages no longer look like a pasted numbered text block.
2. A page displays one header and up to five compact skill cards.
3. Visible skills use their local icons when deterministically resolvable.
4. Skill detail pages use the selected skill icon and clean inline metadata fields.
5. Missing icons never prevent a result or detail page from rendering.
6. Existing five-per-page navigation, selection, Back, owner, and generation behavior is preserved.
7. All non-empty detail text remains accessible with no truncation loss beyond existing safe chunking.
8. No search, parser, database, raw-skill, item/stat, Qwen, or semantic-retrieval behavior changes.
9. Focused tests and the full repository test suite pass.
