# filter_search

## Discord bot

`discord_bot.py` exposes the same Toram item/stat search system through Discord.

The bot is intentionally mention-only and only responds inside configured servers. It ignores DMs, unmentioned messages, other bots/webhooks, and messages from servers outside the configured guild allowlist.

### Environment

Create a `.env` file in the repository root, beside `discord_bot.py`:

```env
DISCORD_BOT_TOKEN=your_real_token
DISCORD_GUILD_IDS=123456789012345678,987654321098765432
OLLAMA_MODEL=qwen3.5:2b
```

`DISCORD_GUILD_IDS` accepts one or more comma-separated Discord server IDs; spaces after commas are allowed. For backward compatibility, a single server can still be configured with `DISCORD_GUILD_ID`. If both variables are set, `DISCORD_GUILD_IDS` takes precedence.

You can copy `.env.example` as a safe template. The real `.env` is ignored by Git and must not be committed. Existing shell/OS environment variables take precedence over values in `.env`.

`OLLAMA_MODEL` is optional; the existing local Ollama client defaults to `qwen3.5:2b`.

Do not hardcode the Discord token in source code.

### Run

```bash
uv run python discord_bot.py
```

### Discord permissions

Invite the bot with these channel permissions:

- View Channels
- Send Messages
- Embed Links
- Attach Files

The bot uses standard guild/guild-message gateway intents. The privileged Message Content intent does not need to be enabled for this mention-only design because Discord still exposes message content when the bot itself is mentioned.

### Usage

Examples:

```text
@ToramBot hp armor
@ToramBot find armor with hp
@ToramBot hp > 5000 and cr bow
@ToramBot item Rapier
@ToramBot upgrade Don
@ToramBot how to use it
```

`upgrade <crysta name>` lists the direct upgrade crystas whose `Upgrade for` relationship points to that crysta. For example, `upgrade Don` lists all direct crysta upgrades from Don.

Usage/help wording such as `how to use it` is handled deterministically and does not require Qwen/Ollama.

Search results use five-item pages, an item dropdown, and Previous/Next controls. Item details do not show database IDs or Coryn page links. If an item has local images, the first image is shown and Previous Image / Next Image controls are available when multiple images exist.

Only the user who started a search can use its controls. A new tagged query from that same user in the same channel replaces their previous active search; old controls then report that the search is no longer active.
