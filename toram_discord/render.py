from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import discord

import search_items as core
from toram_search.fallback import SearchIntentRequest
from toram_search.service import (
    ExpressionResultsPayload,
    ItemResultsPayload,
    SearchPayload,
    StatClarificationPayload,
    StatResultsPayload,
    UpgradeDetailPayload,
    UpgradeResultsPayload,
    format_search_request,
)
from toram_search.session import PendingItemSearch


PAGE_SIZE = 5


def truncate_discord_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    return text[: limit - 1].rstrip() + "…"

def visible_attachment_name(item_name: str, image_index: int, suffix: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", item_name.casefold()).strip("-") or "item"
    safe_suffix = (
        suffix.lower()
        if suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        else ".jpg"
    )
    return f"{stem}-{image_index + 1}{safe_suffix}"

def valid_local_image_paths(
    database_path: Path,
    images: Iterable[dict[str, object]],
) -> tuple[Path, ...]:
    output: list[Path] = []
    for image in images:
        local_path = image.get("local_path")
        if not local_path:
            continue
        path = Path(str(local_path))
        if path.is_absolute():
            resolved_path = path
        elif path.parts and path.parts[0].casefold() == "appearance":
            resolved_path = (database_path.parent.parent / path).resolve()
        else:
            resolved_path = (database_path.parent / path).resolve()
        if resolved_path.is_file():
            output.append(resolved_path)
    return tuple(output)

def _format_number(value: object, *, signed: bool = False) -> str:
    return core._format_number(value, signed=signed)

def _condition_label(value: object) -> str | None:
    return core._condition_label(value)

def _filter_label(parsed: core.ParsedSearch) -> str:
    return parsed.filter.label if parsed.filter else "All item types"

def _safe_field(embed: discord.Embed, name: str, value: str, *, inline: bool = False) -> None:
    cleaned = value.strip() or "None"
    embed.add_field(
        name=truncate_discord_text(name, 256),
        value=truncate_discord_text(cleaned, 1024),
        inline=inline,
    )

def _result_count(payload: SearchPayload) -> int:
    if isinstance(
        payload,
        (ItemResultsPayload, UpgradeResultsPayload, StatResultsPayload, ExpressionResultsPayload),
    ):
        return len(payload.results)
    return 0

def _result_item(payload: SearchPayload, index: int):
    if isinstance(
        payload,
        (ItemResultsPayload, UpgradeResultsPayload, StatResultsPayload, ExpressionResultsPayload),
    ):
        if 0 <= index < len(payload.results):
            return payload.results[index].item
    return None

def is_upgrade_suggestion_payload(payload: SearchPayload) -> bool:
    return (
        isinstance(payload, UpgradeResultsPayload)
        and bool(payload.results)
        and not all(result.match_kind == "upgrade" for result in payload.results)
    )

def _result_lines(payload: SearchPayload, index: int) -> list[str]:
    if isinstance(payload, ItemResultsPayload):
        result = payload.results[index]
        return [f"**{result.item.name}** — {result.item.item_type}"]

    if isinstance(payload, UpgradeResultsPayload):
        result = payload.results[index]
        return [f"**{result.item.name}** — {result.item.item_type}"]

    if isinstance(payload, StatResultsPayload):
        result = payload.results[index]
        parsed = payload.parsed
        stat_name = parsed.stat.stat_name if parsed.stat else result.primary.stat_name
        primary = core.format_stat_display(stat_name, result.primary.amount)
        condition = core.format_condition_display(result.primary)
        if condition:
            primary += f" [{condition}]"
        return [
            f"**{result.item.name}** — {result.item.item_type}",
            primary,
        ]

    if isinstance(payload, ExpressionResultsPayload):
        result = payload.results[index]
        lines = [f"**{result.item.name}** — {result.item.item_type}"]
        for match in result.matches:
            for row_index, row in enumerate(match.rows):
                prefix = "" if row_index == 0 else "Also: "
                text = prefix + core.format_stat_display(
                    match.clause.stat_name,
                    row.amount,
                )
                condition = core.format_condition_display(row)
                if condition:
                    text += f" [{condition}]"
                lines.append(text)
        return lines

    return []

def build_search_results_embed(payload: SearchPayload, page: int) -> discord.Embed:
    total = _result_count(payload)
    max_page = max((total - 1) // PAGE_SIZE, 0)
    page = min(max(page, 0), max_page)
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    if isinstance(payload, ItemResultsPayload):
        title = f'Item search: {payload.query}'
        order = "Closest matches first"
    elif isinstance(payload, UpgradeResultsPayload):
        direct_upgrade_results = bool(payload.results) and all(
            result.match_kind == "upgrade" for result in payload.results
        )
        if direct_upgrade_results:
            title = f"Upgrades from {payload.query}"
            order = "Direct upgrades"
        else:
            title = f'Upgrade search: {payload.query}'
            order = "Closest matches first"
    elif isinstance(payload, StatResultsPayload):
        stat_name = payload.parsed.stat.stat_name if payload.parsed.stat else "Stat"
        title = f"{stat_name} — {_filter_label(payload.parsed)}"
        order = "Highest first"
    elif isinstance(payload, ExpressionResultsPayload):
        parsed = payload.parsed
        if parsed.resolved_expression is not None:
            expression = core.format_resolved_expression(parsed.resolved_expression)
            primary = parsed.resolved_expression.groups[0].clauses[0].stat_name
        else:
            expression = parsed.raw_query
            primary = "primary stat"
        title = f"Stat search — {_filter_label(parsed)}"
        order = f"{primary} {'lowest' if parsed.primary_sort_ascending else 'highest'} first"
    else:
        return discord.Embed(title="Search results")

    if total == 0:
        embed = discord.Embed(title="No results")
        if isinstance(payload, StatResultsPayload) and payload.parsed.stat is not None:
            embed.description = (
                "No matching items were found.\n\n"
                f"Item type: {_filter_label(payload.parsed)}\n"
                f"Stat: {payload.parsed.stat.stat_name}"
            )
        elif isinstance(payload, ExpressionResultsPayload):
            embed.description = (
                "No matching items were found.\n\n"
                f"Item type: {_filter_label(payload.parsed)}\n"
                f"Query: {payload.parsed.raw_query}"
            )
        elif isinstance(payload, UpgradeResultsPayload):
            embed.description = f"No direct upgrade crystas were found for **{payload.query}**."
        else:
            embed.description = "No matching items were found."
        return embed

    description_lines = [order, f"Showing {start + 1}–{end} of {total}", ""]
    if isinstance(payload, ExpressionResultsPayload):
        if payload.parsed.resolved_expression is not None:
            description_lines.insert(
                1,
                core.format_resolved_expression(payload.parsed.resolved_expression),
            )
    for result_index in range(start, end):
        description_lines.append(f"{result_index + 1}. " + _result_lines(payload, result_index)[0])
        for extra in _result_lines(payload, result_index)[1:]:
            description_lines.append(f"   {extra}")
        description_lines.append("")
    return discord.Embed(
        title=truncate_discord_text(title, 256),
        description=truncate_discord_text("\n".join(description_lines).rstrip(), 4096),
    )

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

def build_item_detail_embed(
    detail: core.ItemDetail,
    *,
    image_count: int = 0,
    image_index: int = 0,
    attachment_name: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=truncate_discord_text(detail.summary.name, 256),
        description=truncate_discord_text(detail.summary.item_type, 4096),
    )

    overview: list[str] = []
    if detail.sell_price is not None:
        overview.append(f"Sell price: {_format_number(detail.sell_price)}")
    if detail.process_material or detail.process_amount is not None:
        overview.append(
            f"Process: {detail.process_material or 'Unknown'} × "
            f"{_format_number(detail.process_amount)}"
        )
    if detail.badge:
        overview.append(f"Badge: {detail.badge}")
    if overview:
        _safe_field(embed, "Info", "\n".join(overview))

    stat_lines: list[str] = []
    for group in core.build_item_stat_groups(detail.stats):
        if group.heading is not None:
            if stat_lines:
                stat_lines.append("")
            stat_lines.append(f"**{group.heading}**")
        stat_lines.extend(group.lines)
    _safe_field(embed, "Stats", "\n".join(stat_lines) if stat_lines else "None")

    source_lines: list[str] = []
    for source in detail.sources:
        source_name = str(source.get("source_name") or "Unknown")
        details: list[str] = []
        level = source.get("level")
        if level is not None:
            level_text = _format_number(level)
            level_pattern = rf"\blv\.?\s*{re.escape(level_text)}(?:\b|(?=\)))"
            if re.search(level_pattern, source_name, flags=re.IGNORECASE) is None:
                details.append(f"Lv {level_text}")
        if source.get("map"):
            details.append(str(source["map"]))
        if source.get("dye"):
            details.append(f"Dye: {source['dye']}")
        source_lines.append(
            source_name + (" — " + " — ".join(details) if details else "")
        )
    _safe_field(
        embed,
        "Obtained From",
        "\n".join(source_lines) if source_lines else "Unknown",
    )

    upgrade_lines: list[str] = []
    upgrade_lines.extend(f"Previous: {item.name}" for item in detail.upgrade_predecessors)
    upgrade_lines.extend(f"Next: {item.name}" for item in detail.upgrade_successors)
    if upgrade_lines:
        _safe_field(embed, "Upgrade", "\n".join(upgrade_lines))

    if detail.note:
        _safe_field(embed, "Notes", str(detail.note))

    if attachment_name is not None:
        embed.set_image(url=f"attachment://{attachment_name}")
    if image_count:
        embed.set_footer(text=f"Image {image_index + 1} of {image_count}")
    return embed

def build_help_embed(bot_example_prefix: str) -> discord.Embed:
    embed = discord.Embed(
        title="Toram Item Search",
        description=(
            "Search explicit item names, item types, and stats. "
            "Build-role recommendations such as tank/DPS evaluation are not supported yet."
        ),
    )
    _safe_field(
        embed,
        "Examples",
        "\n".join(
            [
                f"{bot_example_prefix} hp armor",
                f"{bot_example_prefix} find armor with hp",
                f"{bot_example_prefix} hp > 5000 and cr bow",
                f"{bot_example_prefix} item Rapier",
                f"{bot_example_prefix} upgrade <crysta name>",
                f"{bot_example_prefix} what upgrades from Don",
            ]
        ),
    )
    return embed

def _build_text_embed(title: str, text: str) -> discord.Embed:
    return discord.Embed(
        title=truncate_discord_text(title, 256),
        description=truncate_discord_text(text, 4096),
    )

def build_clarification_embed(payload: StatClarificationPayload) -> discord.Embed:
    clarification = payload.clarification
    if clarification.mode == "confirm":
        suggestion = clarification.display_labels[0] if clarification.display_labels else "the suggested stat"
        return discord.Embed(
            title="Confirm stat",
            description=f'Did you mean **{suggestion}** for "{clarification.typed_stat}"?',
        )
    labels = "\n".join(f"• {label}" for label in clarification.display_labels)
    return discord.Embed(
        title=f'What does "{clarification.typed_stat}" mean?',
        description=labels,
    )

def build_item_understanding_embed(pending: PendingItemSearch) -> discord.Embed:
    understanding = pending.understanding
    first_issue = understanding.uncertainties[0] if understanding.uncertainties else None

    if understanding.decision == "clarify" and first_issue is not None:
        title = f'What does "{first_issue.typed_text}" mean?'
        description = "\n".join(f"• {choice.value}" for choice in first_issue.choices)
    elif understanding.decision == "confirm" and first_issue is not None:
        title = "Confirm correction"
        choice = first_issue.choices[0] if first_issue.choices else None
        description = (
            f'Did you mean **{choice.value}** for "{first_issue.typed_text}"?'
            if choice is not None
            else "Confirm the suggested correction."
        )
    elif understanding.decision == "suggest":
        title = "I understood part of that search"
        description = "I can offer one safe search without guessing about the unresolved wording."
    else:
        title = "Ready to search"
        description = "The corrected search is fully resolved."

    embed = discord.Embed(
        title=truncate_discord_text(title, 256),
        description=truncate_discord_text(description, 4096),
    )
    if understanding.resolved_parts:
        _safe_field(
            embed,
            "I understood",
            "\n".join(f"• {part.display_label}" for part in understanding.resolved_parts),
        )
    if understanding.unresolved_tokens:
        _safe_field(
            embed,
            "I couldn't safely interpret",
            "\n".join(f"• `{token}`" for token in understanding.unresolved_tokens),
        )
    if understanding.decision == "suggest" and understanding.suggested_query:
        semantic = " + ".join(
            part.display_label for part in understanding.resolved_parts
        )
        _safe_field(embed, "Suggested search", semantic or understanding.suggested_query)
    elif understanding.decision == "confirm" and not understanding.uncertainties:
        semantic = " + ".join(
            part.display_label for part in understanding.resolved_parts
        )
        if semantic:
            _safe_field(embed, "Search", semantic)
    canonical = understanding.suggested_query or understanding.canonical_query
    if canonical:
        _safe_field(embed, "Search form", f"`{canonical}`")
    return embed

def build_qwen_confirmation_embed(
    requests: tuple[SearchIntentRequest, ...],
    selected_index: int,
) -> discord.Embed:
    if not requests:
        return _build_text_embed("Interpretation failed", "No valid interpretation was produced.")
    selected_index = min(max(selected_index, 0), len(requests) - 1)
    selected = requests[selected_index]
    description = format_search_request(selected)
    if len(requests) > 1:
        description += f"\n\nInterpretation {selected_index + 1} of {len(requests)}"
    return discord.Embed(
        title="Is this what you meant?",
        description=truncate_discord_text(description, 4096),
    )
