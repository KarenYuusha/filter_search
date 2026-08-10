from pathlib import Path

path = Path("search_items.py")
text = path.read_text()

constant_anchor = "FUZZY_STAT_THRESHOLD = 70.0\n"
constant_line = "ITEM_FUZZY_RELEVANCE_THRESHOLD = 70.0\n"
if constant_line not in text:
    if constant_anchor not in text:
        raise SystemExit("missing fuzzy stat threshold anchor")
    text = text.replace(
        constant_anchor,
        constant_anchor + constant_line,
        1,
    )

helper = '''\ndef _is_relevant_ranked_item(result: RankedItem) -> bool:\n    if result.match_kind in {"exact", "prefix", "substring", "all_tokens"}:\n        return True\n    return result.score >= ITEM_FUZZY_RELEVANCE_THRESHOLD\n\n\n'''
rank_anchor = "\ndef rank_items(query: str, items: Iterable[ItemSummary]) -> list[RankedItem]:\n"
if helper not in text:
    if rank_anchor not in text:
        raise SystemExit("missing rank_items anchor")
    text = text.replace(rank_anchor, helper + rank_anchor, 1)

old_results = "    results = [_score_item(normalized_query, item) for item in items]\n"
new_results = '''    results = [\n        result\n        for item in items\n        if _is_relevant_ranked_item(result := _score_item(normalized_query, item))\n    ]\n'''
if old_results in text:
    text = text.replace(old_results, new_results, 1)
elif new_results not in text:
    raise SystemExit("missing rank_items result construction")

path.write_text(text)
