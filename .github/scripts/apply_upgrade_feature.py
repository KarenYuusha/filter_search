from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise SystemExit(f"{label}: expected text not found")


# Shared natural upgrade normalization.
path = Path("search_items.py")
text = path.read_text(encoding="utf-8")
parse_marker = "\ndef parse_search_query(query: str, repository: ItemRepository) -> ParsedSearch:\n"
helper = r'''
_NATURAL_UPGRADE_PATTERNS = (
    r"upgrade\s+for\s+(.+)",
    r"upgrades\s+for\s+(.+)",
    r"(?:show|find)\s+upgrades\s+for\s+(.+)",
    r"what\s+upgrades\s+from\s+(.+)",
    r"what\s+can\s+upgrade\s+(.+)",
    r"what\s+comes\s+after\s+(.+)",
    r"next\s+xtal\s+after\s+(.+)",
)


def extract_natural_upgrade_target(text: str) -> str | None:
    cleaned = " ".join(text.strip().rstrip("?.!").split())
    if not cleaned:
        return None
    for pattern in _NATURAL_UPGRADE_PATTERNS:
        match = re.fullmatch(pattern, cleaned, flags=re.IGNORECASE)
        if match is not None:
            target = match.group(1).strip()
            return target or None
    return None

'''
if "def extract_natural_upgrade_target(" not in text:
    if parse_marker not in text:
        raise SystemExit("search parser marker not found")
    text = text.replace(parse_marker, "\n" + helper + parse_marker.lstrip("\n"), 1)

text = replace_once(
    text,
    '''    if not normalized:\n        return ParsedSearch("item_search", raw, item_query="")\n\n    first, _, remainder = raw.partition(" ")\n''',
    '''    if not normalized:\n        return ParsedSearch("item_search", raw, item_query="")\n\n    natural_upgrade_target = extract_natural_upgrade_target(raw)\n    if natural_upgrade_target is not None:\n        upgrade_exact = repository.exact_upgrade_name_matches(natural_upgrade_target)\n        canonical_query = f"upgrade {natural_upgrade_target}"\n        if len(upgrade_exact) == 1:\n            return ParsedSearch("exact_upgrade", canonical_query, item_id=upgrade_exact[0].id)\n        return ParsedSearch("upgrade_search", canonical_query, item_query=natural_upgrade_target)\n\n    first, _, remainder = raw.partition(" ")\n''',
    "natural upgrade parser insertion",
)
path.write_text(text, encoding="utf-8")


# Search-service continuation after a fuzzy base-crysta selection.
path = Path("toram_search/service.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''    def item_detail(self, item_id: int) -> ItemDetailPayload:\n        return ItemDetailPayload(self.repository.get_item(item_id))\n\n    @staticmethod\n''',
    '''    def item_detail(self, item_id: int) -> ItemDetailPayload:\n        return ItemDetailPayload(self.repository.get_item(item_id))\n\n    def continue_upgrade_selection(\n        self,\n        item_id: int,\n        item_name: str,\n    ) -> ServiceOutcome:\n        parsed = core.ParsedSearch(\n            intent="exact_upgrade",\n            raw_query=f"upgrade {item_name}",\n            item_id=item_id,\n        )\n        return ServiceOutcome("search", payload=self._materialize(parsed, {}))\n\n    @staticmethod\n''',
    "service upgrade continuation",
)
path.write_text(text, encoding="utf-8")


# Discord fuzzy-upgrade continuation and display-name examples.
path = Path("discord_bot.py")
text = path.read_text(encoding="utf-8")

text = replace_once(
    text,
    '''def extract_mentioned_query(content: str, bot_user_id: int) -> str:\n    cleaned = re.sub(rf"<@!?{bot_user_id}>", " ", content)\n    return " ".join(cleaned.split())\n\n\ndef truncate_discord_text''',
    '''def extract_mentioned_query(content: str, bot_user_id: int) -> str:\n    cleaned = re.sub(rf"<@!?{bot_user_id}>", " ", content)\n    return " ".join(cleaned.split())\n\n\ndef bot_example_prefix(guild, bot_user) -> str:\n    member = None\n    get_member = getattr(guild, "get_member", None) if guild is not None else None\n    if bot_user is not None and callable(get_member):\n        member = get_member(bot_user.id)\n    display_name = (\n        getattr(member, "display_name", None)\n        or getattr(bot_user, "display_name", None)\n        or getattr(bot_user, "name", None)\n        or "Bot"\n    )\n    return f"@{display_name}"\n\n\ndef truncate_discord_text''',
    "Discord example-prefix helper",
)

if "def is_upgrade_suggestion_payload(" not in text:
    marker = "\n\ndef _result_lines(payload: SearchPayload, index: int) -> list[str]:\n"
    helper = '''\n\ndef is_upgrade_suggestion_payload(payload: SearchPayload) -> bool:\n    return (\n        isinstance(payload, UpgradeResultsPayload)\n        and bool(payload.results)\n        and not all(result.match_kind == "upgrade" for result in payload.results)\n    )\n'''
    if marker not in text:
        raise SystemExit("Discord result-lines marker not found")
    text = text.replace(marker, helper + marker, 1)

text = replace_once(
    text,
    '''def build_help_embed(bot_mention: str) -> discord.Embed:\n    embed = discord.Embed(\n        title="Toram Item Search",\n        description=(\n            "Search explicit item names, item types, and stats. "\n            "Build-role recommendations such as tank/DPS evaluation are not supported yet."\n        ),\n    )\n    _safe_field(\n        embed,\n        "Examples",\n        "\\n".join(\n            [\n                f"{bot_mention} hp armor",\n                f"{bot_mention} find armor with hp",\n                f"{bot_mention} hp > 5000 and cr bow",\n                f"{bot_mention} item Rapier",\n                f"{bot_mention} upgrade <crysta name>",\n            ]\n        ),\n    )\n    return embed\n''',
    '''def build_help_embed(bot_example_prefix: str) -> discord.Embed:\n    embed = discord.Embed(\n        title="Toram Item Search",\n        description=(\n            "Search explicit item names, item types, and stats. "\n            "Build-role recommendations such as tank/DPS evaluation are not supported yet."\n        ),\n    )\n    _safe_field(\n        embed,\n        "Examples",\n        "\\n".join(\n            [\n                f"{bot_example_prefix} hp armor",\n                f"{bot_example_prefix} find armor with hp",\n                f"{bot_example_prefix} hp > 5000 and cr bow",\n                f"{bot_example_prefix} item Rapier",\n                f"{bot_example_prefix} upgrade <crysta name>",\n                f"{bot_example_prefix} what upgrades from Don",\n            ]\n        ),\n    )\n    return embed\n''',
    "Discord help examples",
)

text = replace_once(
    text,
    '''def run_item_detail_sync(\n    database_path: Path,\n    item_id: int,\n    *,\n    repository_factory=core.ItemRepository,\n) -> ItemDetailPayload:\n    repository = repository_factory(database_path.resolve())\n    try:\n        return ItemDetailPayload(repository.get_item(item_id))\n    finally:\n        repository.close()\n\n\nasync def send_if_current''',
    '''def run_item_detail_sync(\n    database_path: Path,\n    item_id: int,\n    *,\n    repository_factory=core.ItemRepository,\n) -> ItemDetailPayload:\n    repository = repository_factory(database_path.resolve())\n    try:\n        return ItemDetailPayload(repository.get_item(item_id))\n    finally:\n        repository.close()\n\n\ndef run_upgrade_selection_sync(\n    database_path: Path,\n    item_id: int,\n    item_name: str,\n    *,\n    repository_factory=core.ItemRepository,\n) -> ServiceOutcome:\n    repository = repository_factory(database_path.resolve())\n    try:\n        return SearchService(repository).continue_upgrade_selection(item_id, item_name)\n    finally:\n        repository.close()\n\n\nasync def send_if_current''',
    "Discord upgrade-selection worker",
)

text = replace_once(
    text,
    '''        if item_id is None:\n            await interaction.response.send_message("Invalid item selection.", ephemeral=True)\n            return\n        await interaction.response.defer()\n        detail_payload = await asyncio.to_thread(\n            run_item_detail_sync,\n            self.database_path,\n            item_id,\n        )\n''',
    '''        if item_id is None:\n            await interaction.response.send_message("Invalid item selection.", ephemeral=True)\n            return\n        if is_upgrade_suggestion_payload(self.payload):\n            item = _result_item(self.payload, result_index)\n            if item is None:\n                await interaction.response.send_message("Invalid item selection.", ephemeral=True)\n                return\n            await interaction.response.defer()\n            outcome = await asyncio.to_thread(\n                run_upgrade_selection_sync,\n                self.database_path,\n                item.id,\n                item.name,\n            )\n            if not self.sessions.is_current(self.key, self.generation):\n                return\n            session = self.sessions.get(self.key)\n            if session is None:\n                return\n            session.selected_index = result_index\n            session.page = 0\n            session.detail_payload = None\n            session.image_index = 0\n            await edit_service_outcome(\n                interaction,\n                outcome,\n                sessions=self.sessions,\n                key=self.key,\n                generation=self.generation,\n                database_path=self.database_path,\n            )\n            return\n        await interaction.response.defer()\n        detail_payload = await asyncio.to_thread(\n            run_item_detail_sync,\n            self.database_path,\n            item_id,\n        )\n''',
    "Discord fuzzy upgrade selection",
)

text = text.replace("bot_mention", "bot_example_prefix")

text = replace_once(
    text,
    '''    bot_id = interaction.client.user.id if interaction.client.user is not None else 0\n    embed, view, file = build_service_outcome_message(\n        outcome,\n        bot_example_prefix=f"<@{bot_id}>",\n''',
    '''    prefix = bot_example_prefix(interaction.guild, interaction.client.user)\n    embed, view, file = build_service_outcome_message(\n        outcome,\n        bot_example_prefix=prefix,\n''',
    "interaction display-name prefix",
)

text = replace_once(
    text,
    '''async def process_tagged_query(\n    message: discord.Message,\n    *,\n    bot_user_id: int,\n    config: DiscordBotConfig,\n    sessions: DiscordSessionManager,\n) -> None:\n    query = extract_mentioned_query(message.content, bot_user_id)\n''',
    '''async def process_tagged_query(\n    message: discord.Message,\n    *,\n    bot_user,\n    config: DiscordBotConfig,\n    sessions: DiscordSessionManager,\n) -> None:\n    bot_user_id = bot_user.id\n    query = extract_mentioned_query(message.content, bot_user_id)\n''',
    "tagged-query bot user",
)

text = replace_once(
    text,
    '''        bot_example_prefix=f"<@{bot_user_id}>",\n        sessions=sessions,\n''',
    '''        bot_example_prefix=bot_example_prefix(message.guild, bot_user),\n        sessions=sessions,\n''',
    "message display-name prefix",
)

text = replace_once(
    text,
    '''            await process_tagged_query(\n                message,\n                bot_user_id=client.user.id,\n                config=config,\n                sessions=sessions,\n            )\n''',
    '''            await process_tagged_query(\n                message,\n                bot_user=client.user,\n                config=config,\n                sessions=sessions,\n            )\n''',
    "client tagged-query call",
)

path.write_text(text, encoding="utf-8")
