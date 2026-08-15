from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from toram_data import ConditionDraft, ItemDraft, ItemRepository, StatDraft
from toram_gui.stats_tab import StatsTab


class _RepositoryStub:
    def list_stat_names(self) -> list[str]:
        return ["ATK", "Critical Rate", "MaxHP", "Physical Pierce"]


class StatsTabReorderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tab = StatsTab(_RepositoryStub())
        self.draft = ItemDraft.new(item_id=999_001, schema_version=1)
        self.draft.stats = [
            StatDraft("ATK", 10.0, ConditionDraft(mode="known", slugs=("main_weapon",))),
            StatDraft("Critical Rate", 20.0),
            StatDraft("MaxHP", 30.0, ConditionDraft(mode="free_text", text="custom", needs_review=True)),
            StatDraft("Physical Pierce", 40.0),
        ]
        self.tab.set_draft(self.draft)

    def tearDown(self) -> None:
        self.tab.deleteLater()
        self.app.processEvents()

    def names(self) -> list[str]:
        return [stat.stat_name for stat in self.draft.stats]

    def test_move_first_stat_to_last(self) -> None:
        self.tab._move_stat_row(0, 3)
        self.assertEqual(
            self.names(),
            ["Critical Rate", "MaxHP", "Physical Pierce", "ATK"],
        )
        self.assertEqual(self.tab.table.currentRow(), 3)

    def test_move_last_stat_to_first(self) -> None:
        self.tab._move_stat_row(3, 0)
        self.assertEqual(
            self.names(),
            ["Physical Pierce", "ATK", "Critical Rate", "MaxHP"],
        )
        self.assertEqual(self.tab.table.currentRow(), 0)

    def test_move_middle_stat_both_directions(self) -> None:
        self.tab._move_stat_row(2, 1)
        self.assertEqual(
            self.names(),
            ["ATK", "MaxHP", "Critical Rate", "Physical Pierce"],
        )
        self.tab._move_stat_row(1, 2)
        self.assertEqual(
            self.names(),
            ["ATK", "Critical Rate", "MaxHP", "Physical Pierce"],
        )

    def test_no_op_does_not_emit_change(self) -> None:
        emissions = 0

        def changed() -> None:
            nonlocal emissions
            emissions += 1

        self.tab.draftChanged.connect(changed)
        self.tab._move_stat_row(1, 1)
        self.assertEqual(self.names(), ["ATK", "Critical Rate", "MaxHP", "Physical Pierce"])
        self.assertEqual(emissions, 0)

    def test_move_preserves_stat_object_data_and_emits_once(self) -> None:
        moved = self.draft.stats[2]
        emissions = 0

        def changed() -> None:
            nonlocal emissions
            emissions += 1

        self.tab.draftChanged.connect(changed)
        self.tab._move_stat_row(2, 0)

        self.assertIs(self.draft.stats[0], moved)
        self.assertEqual(self.draft.stats[0].amount, 30.0)
        self.assertEqual(self.draft.stats[0].condition.text, "custom")
        self.assertTrue(self.draft.stats[0].condition.needs_review)
        self.assertEqual(self.tab.table.item(0, self.tab.COL_REVIEW).text(), "Needs review")
        self.assertEqual(emissions, 1)

    def test_invalid_move_is_ignored(self) -> None:
        before = list(self.draft.stats)
        self.tab._move_stat_row(-1, 0)
        self.tab._move_stat_row(0, 99)
        self.assertEqual(self.draft.stats, before)

    def test_drop_destination_normalizes_downward_move(self) -> None:
        self.tab.table.resize(700, 400)
        self.tab.table.show()
        self.app.processEvents()

        last_rect = self.tab.table.visualRect(self.tab.table.model().index(3, 0))
        point_below_last_center = last_rect.center()
        point_below_last_center.setY(last_rect.bottom())

        self.assertEqual(
            self.tab.table._destination_for_drop(point_below_last_center, source=0),
            3,
        )

    def test_drop_destination_keeps_upward_index(self) -> None:
        self.tab.table.resize(700, 400)
        self.tab.table.show()
        self.app.processEvents()

        first_rect = self.tab.table.visualRect(self.tab.table.model().index(0, 0))
        point_above_first_center = first_rect.center()
        point_above_first_center.setY(first_rect.top())

        self.assertEqual(
            self.tab.table._destination_for_drop(point_above_first_center, source=3),
            0,
        )


class StatOrderPersistenceTests(unittest.TestCase):
    def test_update_and_reload_preserves_reordered_stats(self) -> None:
        source_db = Path("coryn_data/database/items.sqlite")
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "items.sqlite"
            shutil.copy2(source_db, database)
            repository = ItemRepository(database)
            try:
                item = next(
                    candidate
                    for candidate in repository.list_items()
                    if len(repository.load_item_draft(candidate.id).stats) >= 2
                )
                draft = repository.load_item_draft(item.id)
                original_names = [stat.stat_name for stat in draft.stats]
                draft.stats = draft.stats[1:] + draft.stats[:1]

                repository.update_item(draft)
                reloaded = repository.load_item_draft(item.id)

                self.assertEqual(
                    [stat.stat_name for stat in reloaded.stats],
                    original_names[1:] + original_names[:1],
                )
            finally:
                repository.close()
