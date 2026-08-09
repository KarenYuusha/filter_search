from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise SystemExit(f"{label}: expected text not found")


# 1) Shared service: resolved upgrade lookup -> complete connected component.
path = Path("toram_search/service.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''        if parsed.intent == "exact_upgrade":\n            selected_id = int(parsed.item_id)\n            query = parsed.raw_query.partition(" ")[2].strip() or parsed.raw_query\n            successors = self.repository.get_upgrade_successors(selected_id)\n            return UpgradeResultsPayload(\n                query,\n                self._rank_upgrade_successors(successors),\n            )\n\n        if parsed.intent == "upgrade_search":\n            query = parsed.item_query or ""\n            exact = self.repository.exact_upgrade_name_matches(query)\n            if exact:\n                successors: list[core.ItemSummary] = []\n                for item in exact:\n                    successors.extend(self.repository.get_upgrade_successors(item.id))\n                if successors:\n                    return UpgradeResultsPayload(\n                        query,\n                        self._rank_upgrade_successors(successors),\n                    )\n            upgrade_items = [\n''',
    '''        if parsed.intent == "exact_upgrade":\n            selected_id = int(parsed.item_id)\n            return UpgradeDetailPayload(\n                graph=self.repository.get_upgrade_component(selected_id),\n                selected_item_id=selected_id,\n            )\n\n        if parsed.intent == "upgrade_search":\n            query = parsed.item_query or ""\n            exact = self.repository.exact_upgrade_name_matches(query)\n            if len(exact) == 1:\n                selected_id = exact[0].id\n                return UpgradeDetailPayload(\n                    graph=self.repository.get_upgrade_component(selected_id),\n                    selected_item_id=selected_id,\n                )\n            upgrade_items = [\n''',
    "service resolved upgrade component",
)
path.write_text(text, encoding="utf-8")


# 2) CLI: resolved exact/fuzzy-selected crysta -> existing full graph renderer.
path = Path("search_items.py")
text = path.read_text(encoding="utf-8")
marker = '''def _interactive_upgrade_results(\n    repository: ItemRepository,\n'''
helper = '''def _show_upgrade_component(\n    repository: ItemRepository,\n    selected: ItemSummary,\n    *,\n    output_fn: Callable[[str], None],\n) -> ResultScreenOutcome:\n    graph = repository.get_upgrade_component(selected.id)\n    output_fn(render_upgrade_terminal(graph, selected.id))\n    return ResultScreenOutcome("selected")\n\n\n'''
if "def _show_upgrade_component(" not in text:
    if marker not in text:
        raise SystemExit("CLI upgrade-results marker not found")
    text = text.replace(marker, helper + marker, 1)

text = replace_once(
    text,
    '''    if len(exact) == 1:\n        selected = exact[0]\n        return _interactive_direct_upgrade_results(\n            repository,\n            selected,\n            repository.get_upgrade_successors(selected.id),\n            input_fn=input_fn,\n            output_fn=output_fn,\n        )\n''',
    '''    if len(exact) == 1:\n        return _show_upgrade_component(\n            repository,\n            exact[0],\n            output_fn=output_fn,\n        )\n''',
    "CLI exact upgrade graph",
)
text = replace_once(
    text,
    '''                selected = current[parsed_input.selection - 1].item\n                return _interactive_direct_upgrade_results(\n                    repository,\n                    selected,\n                    repository.get_upgrade_successors(selected.id),\n                    input_fn=input_fn,\n                    output_fn=output_fn,\n                )\n''',
    '''                selected = current[parsed_input.selection - 1].item\n                return _show_upgrade_component(\n                    repository,\n                    selected,\n                    output_fn=output_fn,\n                )\n''',
    "CLI fuzzy selected upgrade graph",
)
text = replace_once(
    text,
    '''            screen = _interactive_direct_upgrade_results(\n                repository,\n                selected,\n                repository.get_upgrade_successors(selected_id),\n                input_fn=input_fn,\n                output_fn=output_fn,\n            )\n''',
    '''            screen = _show_upgrade_component(\n                repository,\n                selected,\n                output_fn=output_fn,\n            )\n''',
    "CLI routed exact upgrade graph",
)
path.write_text(text, encoding="utf-8")


# 3) Shared natural stat grammar: plain item-type has/have expression.
path = Path("toram_data/stat_query.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''    re.compile(r"^\\s*(.+?)\\s+that\\s+(?:has|have)\\s+(.+?)\\s*$", re.IGNORECASE),\n    re.compile(r"^\\s*(.+?)\\s+having\\s+(.+?)\\s*$", re.IGNORECASE),\n''',
    '''    re.compile(r"^\\s*(.+?)\\s+that\\s+(?:has|have)\\s+(.+?)\\s*$", re.IGNORECASE),\n    re.compile(r"^\\s*(.+?)\\s+has\\s+(.+?)\\s*$", re.IGNORECASE),\n    re.compile(r"^\\s*(.+?)\\s+have\\s+(.+?)\\s*$", re.IGNORECASE),\n    re.compile(r"^\\s*(.+?)\\s+having\\s+(.+?)\\s*$", re.IGNORECASE),\n''',
    "plain has/have natural stat patterns",
)
path.write_text(text, encoding="utf-8")
