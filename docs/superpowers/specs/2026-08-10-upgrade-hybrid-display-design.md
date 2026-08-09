# Hybrid Upgrade Display Design

## Goal

Improve upgrade-chain presentation in both Discord and `search_items.py` without changing upgrade lookup semantics. Resolved upgrade queries already return the full connected upgrade component; this change only improves how that graph is rendered.

The display must make two things obvious at once:

1. every valid path from a graph root to the selected crysta;
2. the complete branching upgrade tree containing that crysta.

No pagination is required for upgrade paths or the tree because the expected graphs are small enough to fit in one result.

## Shared presentation model

Both frontends should derive their output from the same graph-formatting helper rather than maintaining separate graph traversal logic.

The shared formatter accepts:

- an `UpgradeGraph`;
- the selected item ID.

It produces a frontend-neutral representation containing:

- selected item name;
- all root-to-selected paths, in deterministic order;
- a tree rendered as ordered text lines;
- markers for selected, repeated, and cyclic nodes.

Discord and the terminal may wrap these strings differently, but they must not independently decide graph traversal, branch ordering, path enumeration, or duplicate handling.

## Root detection

A root is any node in the connected upgrade component with no incoming edge.

If one or more roots exist, enumerate all of them in deterministic name/ID order.

If no roots exist because the graph contains a cycle, fall back to the selected node as the traversal root so rendering always produces useful output and never loops forever.

## Selected paths

The first section lists every valid simple path from any root to the selected crysta.

Example:

```text
Selected paths
2. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F
1. Don → Don Alternative → Don Upgrade C → Don Upgrade F
```

Requirements:

- include every root-to-selected path;
- never silently choose only one path;
- paths must be simple paths: if traversal encounters a node already in the active path, stop that branch so cycles cannot recurse forever;
- order paths deterministically by the ordered sequence of node names, with IDs as tie-breakers;
- if the selected crysta itself is a root, show a one-node path containing only the selected name;
- if malformed/cyclic data makes the selected node unreachable from detected roots, fall back to a one-node selected path rather than omitting the section.

No pagination or truncation logic specific to path count is required. Discord's existing general text-length safeguards may still apply as a final platform-safety measure.

## Full tree

The second section renders the entire connected component as an ASCII/Unicode branching tree.

Example:

```text
Full tree
Don
├── Don Alternative
│   └── Don Upgrade C
│       └── Don Upgrade F  ◀ selected
└── Don Upgrade A
    └── Don Upgrade C  ↩ already shown
```

### Selected marker

The queried/selected node must be marked:

```text
◀ selected
```

Only the selected node receives this marker.

### Shared-node handling

Upgrade graphs may converge: the same crysta may be reachable through more than one predecessor.

The tree should fully expand the first deterministic occurrence of a node. Later occurrences should render only the node label plus:

```text
↩ already shown
```

Do not duplicate that node's entire subtree on subsequent appearances.

The `Selected paths` section still enumerates every valid route to the selected node, even when the tree itself collapses repeated subtrees.

### Cycle handling

If the active traversal path reaches a node already present in that same active path, render the node with:

```text
[cycle]
```

and stop recursion on that branch.

A cycle marker takes precedence over `already shown` because it describes the immediate malformed traversal condition.

### Branch ordering

Sibling nodes and roots must be sorted deterministically by:

1. case-insensitive item name;
2. item ID as tie-breaker.

This ensures Discord and terminal output remain stable across runs.

## Discord layout

Discord should replace the current flat edge list with the hybrid display.

Embed title:

```text
Upgrade Tree — <selected item name>
```

Embed body should have two visually distinct sections.

### Selected paths field

Use a normal embed field named:

```text
Selected paths
```

Each path is one numbered line using arrows:

```text
2. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F
1. Don → Don Alternative → Don Upgrade C → Don Upgrade F
```

Do not place this field in a code block; normal proportional text makes the paths easier to read and wrap.

### Full tree field

Use a second embed field named:

```text
Full tree
```

Render the tree in a fenced text code block so branch characters and indentation stay aligned:

```text
Don
├── Don Alternative
│   └── Don Upgrade C
│       └── Don Upgrade F  ◀ selected
└── Don Upgrade A
    └── Don Upgrade C  ↩ already shown
```

The entire upgrade display remains one embed with no Previous/Next controls.

If the graph contains no relationships, still render the selected item as both the sole selected path and sole tree node.

## `search_items.py` terminal layout

The terminal should expose the same information and ordering without Discord-specific framing.

Example:

```text
Upgrade Tree — Don Upgrade F

Selected paths
2. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F
1. Don → Don Alternative → Don Upgrade C → Don Upgrade F

Full tree
Don
├── Don Alternative
│   └── Don Upgrade C
│       └── Don Upgrade F  ◀ selected
└── Don Upgrade A
    └── Don Upgrade C  ↩ already shown
```

Do not show IDs in this upgrade-tree presentation.

The existing normal item-detail screen may continue to show whatever fields it already owns; this design only governs the dedicated upgrade-tree output.

## Query semantics

This display change must not alter search behavior.

All already-supported upgrade forms continue to resolve the same complete connected component, including queries against the first, middle, or final crysta and fuzzy-selected upgrade candidates.

Only the presentation changes from the old flat/direct representation to the hybrid selected-paths + full-tree representation.

## Error and malformed-data behavior

The renderer should be total: it should return readable output even when graph data is incomplete.

- Missing referenced nodes should use the graph's existing missing-node placeholders.
- Cycles are visibly marked and traversal stops on that branch.
- Repeated convergent nodes are marked `↩ already shown` after their first expansion.
- A component with no edges still displays the selected node.
- If selected-node metadata cannot be resolved, use the existing `Unknown item` fallback rather than raising during rendering.

## Testing

Add shared formatter tests covering:

1. a straight chain;
2. a branching tree;
3. two distinct paths that converge on the selected node;
4. selected item as root;
5. selected item as final leaf;
6. repeated/shared node marker;
7. cycle marker without infinite recursion;
8. deterministic sibling/path ordering;
9. graph with no edges.

Add Discord tests proving:

- title contains the selected item name;
- `Selected paths` contains every expected path;
- `Full tree` is rendered in a monospace code block;
- the old flat one-edge-per-line layout is no longer the primary representation;
- no pagination controls are created for an upgrade detail payload.

Add terminal tests proving:

- the same selected paths and tree structure appear;
- selected/repeated/cycle markers match the shared formatter;
- no item IDs are printed in dedicated upgrade-tree output.

## Non-goals

This change does not:

- alter upgrade graph discovery or database relationships;
- change natural-language upgrade parsing;
- add image/graph rendering libraries;
- generate graphical images of the upgrade tree;
- add pagination for upgrade paths or upgrade trees;
- rank or recommend a 'best' upgrade path;
- change normal item-detail formatting outside the dedicated upgrade-tree presentation.
