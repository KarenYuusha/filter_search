from __future__ import annotations

import unittest

import search_items as core


def item(item_id: int, name: str) -> core.ItemSummary:
    return core.ItemSummary(item_id, name, "Enhancer Crysta (Blue)")


def graph(nodes: list[core.ItemSummary], edges: dict[int, tuple[int, ...]]) -> core.UpgradeGraph:
    return core.UpgradeGraph(
        nodes={node.id: node for node in nodes},
        edges=edges,
        missing_nodes={},
    )


class UpgradeDisplayTests(unittest.TestCase):
    def test_straight_chain_lists_selected_path_and_marks_selected_leaf(self):
        value = graph(
            [item(1, "Don"), item(2, "Don Upgrade A"), item(3, "Don Upgrade B")],
            {1: (2,), 2: (3,), 3: ()},
        )

        display = core.build_upgrade_display(value, 3)

        self.assertEqual(display.selected_name, "Don Upgrade B")
        self.assertEqual(
            display.selected_paths,
            ("1. Don → Don Upgrade A → Don Upgrade B",),
        )
        self.assertEqual(
            display.tree_lines,
            (
                "Don",
                "└── Don Upgrade A",
                "    └── Don Upgrade B  ◀ selected",
            ),
        )

    def test_branching_convergence_lists_every_path_and_collapses_repeated_subtree(self):
        value = graph(
            [
                item(1, "Don"),
                item(2, "Don Upgrade A"),
                item(3, "Don Alternative"),
                item(4, "Don Upgrade C"),
                item(5, "Don Upgrade F"),
            ],
            {1: (2, 3), 2: (4,), 3: (4,), 4: (5,), 5: ()},
        )

        display = core.build_upgrade_display(value, 5)

        self.assertEqual(
            display.selected_paths,
            (
                "1. Don → Don Alternative → Don Upgrade C → Don Upgrade F",
                "2. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F",
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

    def test_selected_root_has_one_node_path_and_selected_marker(self):
        value = graph(
            [item(1, "Base"), item(2, "Upgrade")],
            {1: (2,), 2: ()},
        )

        display = core.build_upgrade_display(value, 1)

        self.assertEqual(display.selected_paths, ("1. Base",))
        self.assertEqual(display.tree_lines[0], "Base  ◀ selected")
        self.assertIn("└── Upgrade", display.tree_lines)

    def test_cycle_is_marked_without_recursing_forever(self):
        value = graph(
            [item(1, "Alpha"), item(2, "Beta"), item(3, "Gamma")],
            {1: (2,), 2: (3,), 3: (1,)},
        )

        display = core.build_upgrade_display(value, 2)

        self.assertEqual(display.selected_paths, ("1. Beta",))
        self.assertIn("Beta  ◀ selected", display.tree_lines[0])
        self.assertTrue(any("[cycle]" in line for line in display.tree_lines))

    def test_no_edge_graph_still_displays_selected_item(self):
        value = graph([item(7, "Solo")], {7: ()})

        display = core.build_upgrade_display(value, 7)

        self.assertEqual(display.selected_name, "Solo")
        self.assertEqual(display.selected_paths, ("1. Solo",))
        self.assertEqual(display.tree_lines, ("Solo  ◀ selected",))

    def test_roots_and_siblings_are_sorted_by_name_then_id(self):
        value = graph(
            [
                item(1, "Root Z"),
                item(2, "Root A"),
                item(3, "Child B"),
                item(4, "Child A"),
            ],
            {1: (), 2: (3, 4), 3: (), 4: ()},
        )

        display = core.build_upgrade_display(value, 3)

        self.assertEqual(display.tree_lines[0], "Root A")
        self.assertEqual(display.tree_lines[1], "├── Child A")
        self.assertEqual(display.tree_lines[2], "└── Child B  ◀ selected")
        self.assertEqual(display.tree_lines[4], "Root Z")


if __name__ == "__main__":
    unittest.main()
