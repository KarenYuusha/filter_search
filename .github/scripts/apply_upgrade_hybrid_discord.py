from pathlib import Path

path = Path("discord_bot.py")
text = path.read_text()
start = text.index("def build_upgrade_detail_embed(")
end = text.index("\n\ndef build_item_detail_embed(", start)
replacement = '''def build_upgrade_detail_embed(payload: UpgradeDetailPayload) -> discord.Embed:\n    display = core.build_upgrade_display(payload.graph, payload.selected_item_id)\n    embed = discord.Embed(\n        title=truncate_discord_text(f\"Upgrade Tree — {display.selected_name}\", 256)\n    )\n    _safe_field(\n        embed,\n        \"Selected paths\",\n        \"\\n\".join(display.selected_paths),\n    )\n    _safe_field(\n        embed,\n        \"Full tree\",\n        \"```text\\n\" + \"\\n\".join(display.tree_lines) + \"\\n```\",\n    )\n    return embed\n'''
text = text[:start] + replacement + text[end:]
path.write_text(text)
