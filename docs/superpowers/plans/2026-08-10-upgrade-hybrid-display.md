# Hybrid Upgrade Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render upgrade graphs in both Discord and `search_items.py` as a hybrid view with every root-to-selected path plus one deterministic branching tree.

**Architecture:** Keep graph traversal and formatting decisions in one shared helper in `search_items.py`, because `UpgradeGraph`, `ItemSummary`, and the existing terminal renderer already live there and Discord already imports `search_items` as `core`. The helper returns a small frontend-neutral `UpgradeDisplay` value; the terminal joins it into plain text while Discord maps it into two embed fields. Search and graph-discovery semantics remain unchanged.

**Tech Stack:** Python 3.12, `unittest`, `discord.py >=2.7.1,<3`.

## Global Constraints

- Show every valid simple root-to-selected path; do not pick only one.
- Render the full connected component as a deterministic branching tree.
- Mark the queried crysta with `◀ selected`.
- Mark later convergent occurrences with `↩ already shown` without re-expanding their subtree.
- Mark active-path cycles with `[cycle]` and stop recursion on that branch.
- Sort roots, siblings, and selected paths case-insensitively by item name, then item ID.
- If there are no roots because of a cycle, use the selected node as the traversal root.
- If the selected node is a root, include a one-node selected path.
- If the selected node is unreachable from detected roots, fall back to a one-node selected path.
- Discord uses one embed, a normal-text `Selected paths` field, and a fenced monospace `Full tree` field; no upgrade pagination controls.
- `search_items.py` prints the same selected paths and tree as plain terminal text.
- Dedicated upgrade-tree output must not print item IDs.
- Do not alter upgrade parsing, database graph discovery, ranking, or Qwen behavior.

---

### Task 1: Shared upgrade display formatter

**Files:**
- Modify: `search_items.py`
- Create: `tests/test_upgrade_display.py`

**Interfaces:**
- Consumes: existing `UpgradeGraph`, `ItemSummary`, and graph fields `nodes`, `missing_nodes`, `edges`.
- Produces:
  - `@dataclass(frozen=True) class UpgradeDisplay:` with `selected_name: str`, `selected_paths: tuple[str, ...]`, and `tree_lines: tuple[str, ...]`.
  - `build_upgrade_display(graph: UpgradeGraph, selected_item_id: int) -> UpgradeDisplay`.

- [ ] **Step 1: Write failing formatter tests**

Create `tests/test_upgrade_display.py` with graph fixtures that cover a straight chain, branch, convergence, selected root, selected leaf, cycle, deterministic ordering, and no-edge graph. Core assertions should include examples equivalent to:

```python
display = core.build_upgrade_display(graph, selected_item_id=4)
self.assertEqual(
    display.selected_paths,
    (
        "2. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F",
        "1. Don → Don Alternative → Don Upgrade C → Don Upgrade F",
    ),
)
self.assertEqual(
    display.tree_lines,
    (
        "Don",
        "├── Don Alternative",
        "│   └── Don Upgrade C",
        "│       └── Don Upgrade F  ◀ selected",
        "└── Don Upgrade A",
        "    └── Don Upgrade C  ↩ already shown",
    ),
)
```

Add a cycle fixture and assert the rendered branch contains `[cycle]` and returns normally. Add a no-edge fixture and assert both the selected path and tree contain only the selected item, with the tree carrying `◀ selected`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python -m unittest tests.test_upgrade_display -v
```

Expected: failure because `build_upgrade_display` / `UpgradeDisplay` do not exist.

- [ ] **Step 3: Implement the frontend-neutral formatter**

Add near the existing upgrade renderer in `search_items.py`:

```python
@dataclass(frozen=True)
class UpgradeDisplay:
    selected_name: str
    selected_paths: tuple[str, ...]
    tree_lines: tuple[str, ...]


def build_upgrade_display(graph: UpgradeGraph, selected_item_id: int) -> UpgradeDisplay:
    all_nodes = {**graph.missing_nodes, **graph.nodes}
    selected = all_nodes.get(
        selected_item_id,
        ItemSummary(selected_item_id, "Unknown item", "Unknown"),
    )

    def sort_key(node_id: int) -> tuple[str, int]:
        node = all_nodes.get(node_id, ItemSummary(node_id, "Unknown item", "Unknown"))
        return node.name.casefold(), node_id

    incoming = {
        target_id
        for target_ids in graph.edges.values()
        for target_id in target_ids
    }
    roots = sorted(
        (node_id for node_id in all_nodes if node_id not in incoming),
        key=sort_key,
    )
    traversal_roots = roots or [selected_item_id]

    path_ids: list[tuple[int, ...]] = []

    def collect_paths(node_id: int, active: tuple[int, ...]) -> None:
        if node_id in active:
            return
        current = active + (node_id,)
        if node_id == selected_item_id:
            path_ids.append(current)
            return
        for child_id in sorted(graph.edges.get(node_id, ()), key=sort_key):
            collect_paths(child_id, current)

    for root_id in traversal_roots:
        collect_paths(root_id, ())
    if not path_ids:
        path_ids = [(selected_item_id,)]

    path_ids.sort(
        key=lambda path: tuple((sort_key(node_id)[0], node_id) for node_id in path)
    )
    selected_paths = tuple(
        f"{index}. " + " → ".join(all_nodes.get(
            node_id,
            ItemSummary(node_id, "Unknown item", "Unknown"),
        ).name for node_id in path)
        for index, path in enumerate(path_ids, start=1)
    )

    tree_lines: list[str] = []
    expanded: set[int] = set()

    def render_node(
        node_id: int,
        *,
        prefix: str,
        is_last: bool,
        is_root: bool,
        active_path: frozenset[int],
    ) -> None:
        node = all_nodes.get(node_id, ItemSummary(node_id, "Unknown item", "Unknown"))
        connector = "" if is_root else ("└── " if is_last else "├── ")
        label = node.name + ("  ◀ selected" if node_id == selected_item_id else "")
        if node_id in active_path:
            tree_lines.append(prefix + connector + label + " [cycle]")
            return
        if node_id in expanded:
            tree_lines.append(prefix + connector + label + "  ↩ already shown")
            return
        tree_lines.append(prefix + connector + label)
        expanded.add(node_id)
        children = sorted(graph.edges.get(node_id, ()), key=sort_key)
        child_prefix = prefix if is_root else prefix + ("    " if is_last else "│   ")
        next_active = active_path | {node_id}
        for index, child_id in enumerate(children):
            render_node(
                child_id,
                prefix=child_prefix,
                is_last=index == len(children) - 1,
                is_root=False,
                active_path=next_active,
            )

    for root_index, root_id in enumerate(traversal_roots):
        if root_index:
            tree_lines.append("")
        render_node(
            root_id,
            prefix="",
            is_last=True,
            is_root=True,
            active_path=frozenset(),
        )
    for node_id in sorted(all_nodes, key=sort_key):
        if node_id not in expanded:
            if tree_lines:
                tree_lines.append("")
            render_node(
                node_id,
                prefix="",
                is_last=True,
                is_root=True,
                active_path=frozenset(),
            )

    return UpgradeDisplay(selected.name, selected_paths, tuple(tree_lines))
```

Preserve the exact marker spacing required by tests; if the initial implementation creates duplicate spaces around a marker, normalize the labels before proceeding.

- [ ] **Step 4: Run formatter tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_upgrade_display -v
```

Expected: all formatter tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add search_items.py tests/test_upgrade_display.py
git commit -m "feat: add shared hybrid upgrade formatter"
```

---

### Task 2: Terminal hybrid upgrade presentation

**Files:**
- Modify: `search_items.py`
- Modify: `tests/test_cli_upgrade.py`

**Interfaces:**
- Consumes: `build_upgrade_display(graph, selected_item_id)` from Task 1.
- Produces: `render_upgrade_terminal()` as the terminal wrapper over the shared model; no duplicate traversal logic remains there.

- [ ] **Step 1: Update CLI tests to require the hybrid layout**

Extend `tests/test_cli_upgrade.py` assertions so a selected leaf produces:

```text
Upgrade Tree — Don Upgrade B

Selected paths
1. Don → Don Upgrade A → Don Upgrade B

Full tree
Don
└── Don Upgrade A
    └── Don Upgrade B  ◀ selected
```

Add a branching/converging CLI fixture assertion that includes all selected paths and `↩ already shown`. Assert the dedicated upgrade output does not include `ID:` or `— ID `.

- [ ] **Step 2: Run CLI tests and verify RED**

Run:

```bash
python -m unittest tests.test_cli_upgrade -v
```

Expected: failures because the old terminal renderer only prints `Upgrade chain for ...` plus the tree.

- [ ] **Step 3: Replace terminal traversal with shared display wrapping**

Rewrite `render_upgrade_terminal()` to only format the Task 1 model:

```python
def render_upgrade_terminal(graph: UpgradeGraph, selected_item_id: int) -> str:
    display = build_upgrade_display(graph, selected_item_id)
    lines = [
        "",
        f"Upgrade Tree — {display.selected_name}",
        "",
        "Selected paths",
        *display.selected_paths,
        "",
        "Full tree",
        *display.tree_lines,
    ]
    return "\n".join(lines)
```

Delete the old duplicate root detection/tree recursion from `render_upgrade_terminal()`.

- [ ] **Step 4: Run CLI + formatter tests**

Run:

```bash
python -m unittest tests.test_upgrade_display tests.test_cli_upgrade -v
```

Expected: all pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add search_items.py tests/test_cli_upgrade.py
git commit -m "feat: show hybrid upgrade display in terminal"
```

---

### Task 3: Discord hybrid upgrade embed

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: `core.build_upgrade_display(payload.graph, payload.selected_item_id)`.
- Produces: `build_upgrade_detail_embed(payload)` with exactly two logical sections: normal-text selected paths and fenced-code full tree.

- [ ] **Step 1: Write Discord RED tests**

Extend `tests/test_discord_bot.py` with an `UpgradeDetailPayload` fixture and assert:

```python
embed = discord_bot.build_upgrade_detail_embed(payload)
self.assertEqual(embed.title, "Upgrade Tree — Don Upgrade F")
fields = {field.name: field.value for field in embed.fields}
self.assertIn("2. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F", fields["Selected paths"])
self.assertIn("1. Don → Don Alternative → Don Upgrade C → Don Upgrade F", fields["Selected paths"])
self.assertTrue(fields["Full tree"].startswith("```text\n"))
self.assertTrue(fields["Full tree"].endswith("\n```"))
self.assertIn("Don Upgrade F  ◀ selected", fields["Full tree"])
self.assertIn("Don Upgrade C  ↩ already shown", fields["Full tree"])
```

Also assert the embed description no longer uses the old primary flat-edge list and that `build_service_outcome_message()` returns `view is None` for `UpgradeDetailPayload`.

- [ ] **Step 2: Run Discord tests and verify RED**

Run:

```bash
python -m unittest tests.test_discord_bot -v
```

Expected: upgrade-detail formatting assertions fail against the current flat edge list.

- [ ] **Step 3: Implement the Discord wrapper**

Replace the body of `build_upgrade_detail_embed()` with shared model usage:

```python
def build_upgrade_detail_embed(payload: UpgradeDetailPayload) -> discord.Embed:
    display = core.build_upgrade_display(payload.graph, payload.selected_item_id)
    embed = discord.Embed(
        title=truncate_discord_text(f"Upgrade Tree — {display.selected_name}", 256)
    )
    _safe_field(
        embed,
        "Selected paths",
        "\n".join(display.selected_paths),
    )
    _safe_field(
        embed,
        "Full tree",
        "```text\n" + "\n".join(display.tree_lines) + "\n```",
    )
    return embed
```

Do not add a `View`; existing `UpgradeDetailPayload` handling should continue returning `(embed, None, None)`.

- [ ] **Step 4: Run Discord + shared formatter tests**

Run:

```bash
python -m unittest tests.test_upgrade_display tests.test_discord_bot tests.test_cli_upgrade -v
```

Expected: all pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add discord_bot.py tests/test_discord_bot.py
git commit -m "feat: show hybrid upgrade display in Discord"
```

---

### Task 4: Regression and repository verification

**Files:**
- No production changes expected.
- Modify tests only if verification reveals a real uncovered requirement from the approved spec.

**Interfaces:**
- Consumes: completed Tasks 1–3.
- Produces: verification evidence for the final PR.

- [ ] **Step 1: Compile changed modules**

Run:

```bash
python -m py_compile search_items.py discord_bot.py toram_search/service.py
```

Expected: success.

- [ ] **Step 2: Run focused regression suite**

Run:

```bash
python -m unittest \
  tests.test_upgrade_display \
  tests.test_cli_upgrade \
  tests.test_discord_bot \
  tests.test_discord_followup_regressions \
  tests.test_search_service \
  tests.test_direct_structured_intent \
  tests.test_llm -v
```

Expected: all focused tests pass.

- [ ] **Step 3: Run full repository suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected baseline: all new hybrid-display tests pass. If the existing structured-fallback assertion still expects `missing or invalid search candidates` while production logs `search payload has unexpected fields`, record it as the same pre-existing unrelated failure and do not modify fallback code in this feature.

- [ ] **Step 4: Inspect final diff**

Confirm the branch contains only:

```text
docs/superpowers/specs/2026-08-10-upgrade-hybrid-display-design.md
docs/superpowers/plans/2026-08-10-upgrade-hybrid-display.md
search_items.py
discord_bot.py
tests/test_upgrade_display.py
tests/test_cli_upgrade.py
tests/test_discord_bot.py
```

No `.env`, token, guild ID, query-semantics change, temporary workflow, or unrelated fallback code should be present.

- [ ] **Step 5: Prepare the PR for review**

Update the PR description with RED/GREEN evidence and final test counts. Do not merge without explicit user instruction.
