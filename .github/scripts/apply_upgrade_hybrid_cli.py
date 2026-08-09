from pathlib import Path

path = Path("search_items.py")
text = path.read_text()

marker = "@dataclass(frozen=True)\nclass UpgradeDisplay:"
first = text.index(marker)
second = text.find(marker, first + len(marker))
if second != -1:
    search_intent = text.index('SearchIntent = Literal[', second)
    text = text[:second] + text[search_intent:]

start = text.index("def render_upgrade_terminal(")
end = text.index("\n\ndef render_direct_upgrade_results(", start)
replacement = '''def render_upgrade_terminal(graph: UpgradeGraph, selected_item_id: int) -> str:\n    display = build_upgrade_display(graph, selected_item_id)\n    lines = [\n        \"\",\n        f\"Upgrade Tree — {display.selected_name}\",\n        \"\",\n        \"Selected paths\",\n        *display.selected_paths,\n        \"\",\n        \"Full tree\",\n        *display.tree_lines,\n    ]\n    return \"\\n\".join(lines)\n'''
text = text[:start] + replacement + text[end:]
path.write_text(text)
