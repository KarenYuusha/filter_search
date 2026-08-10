from pathlib import Path

path = Path('search_items.py')
text = path.read_text(encoding='utf-8')
old = '''    if parsed.intent in {
        "exact_item",
        "exact_upgrade",
        "upgrade_search",
        "guided_stat",
        "stat_search",
        "stat_choices",
    }:
        return DeterministicRoute("search", parsed=parsed)
    if (
        parsed.intent == "stat_expression"
'''
new = '''    if parsed.intent in {
        "exact_item",
        "exact_upgrade",
        "upgrade_search",
        "guided_stat",
        "stat_search",
        "stat_choices",
    }:
        return DeterministicRoute("search", parsed=parsed)
    if parsed.intent == "item_search" and repository.exact_name_matches(raw):
        return DeterministicRoute("search", parsed=parsed)
    if (
        parsed.intent == "stat_expression"
'''
if old not in text:
    raise SystemExit('target routing block not found')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
