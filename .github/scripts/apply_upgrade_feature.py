from pathlib import Path

path = Path("search_items.py")
text = path.read_text(encoding="utf-8")
old = '''    natural_upgrade_target = extract_natural_upgrade_target(raw)\n    if natural_upgrade_target is not None:\n        upgrade_exact = repository.exact_upgrade_name_matches(natural_upgrade_target)\n        canonical_query = f"upgrade {natural_upgrade_target}"\n        if len(upgrade_exact) == 1:\n            return ParsedSearch("exact_upgrade", canonical_query, item_id=upgrade_exact[0].id)\n        return ParsedSearch("upgrade_search", canonical_query, item_query=natural_upgrade_target)\n'''
new = '''    natural_upgrade_target = extract_natural_upgrade_target(raw)\n    if natural_upgrade_target is not None:\n        upgrade_exact = repository.exact_upgrade_name_matches(natural_upgrade_target)\n        if len(upgrade_exact) == 1:\n            canonical_query = f"upgrade {upgrade_exact[0].name}"\n            return ParsedSearch("exact_upgrade", canonical_query, item_id=upgrade_exact[0].id)\n        canonical_query = f"upgrade {natural_upgrade_target}"\n        return ParsedSearch("upgrade_search", canonical_query, item_query=natural_upgrade_target)\n'''
if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise SystemExit("natural-upgrade canonical-name block not found")
path.write_text(text, encoding="utf-8")
