# Discord `.env` Configuration Design

## Goal

Make `discord_bot.py` automatically load local Discord configuration from a project-root `.env` file so normal startup only requires:

```bash
uv run python discord_bot.py
```

This change affects configuration loading only. It must not change Discord routing, search behavior, sessions, Qwen behavior, or rendering.

## Approach

Use `python-dotenv` rather than implementing a custom `.env` parser.

Add `python-dotenv` to `pyproject.toml`, then load the project-root `.env` before `load_config()` reads `DISCORD_BOT_TOKEN` and `DISCORD_GUILD_ID`.

The repository already ignores `.env`, so real secrets remain local and must not be committed.

## File Layout

```text
project/
├─ .env                # local secrets/config, ignored by git
├─ .env.example        # committed safe template
├─ discord_bot.py
├─ pyproject.toml
└─ ...
```

## `.env` Format

Local `.env`:

```env
DISCORD_BOT_TOKEN=your_real_token
DISCORD_GUILD_ID=123456789012345678
OLLAMA_MODEL=qwen3.5:2b
```

Committed `.env.example`:

```env
DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=
OLLAMA_MODEL=qwen3.5:2b
```

No real Discord token or real private configuration may be committed to `.env.example`, tests, logs, or documentation.

## Precedence

Existing process/environment variables take precedence over values loaded from `.env`.

Therefore the loader must use `python-dotenv` without overriding already-set environment variables.

Example:

```text
.env: DISCORD_GUILD_ID=111
shell: DISCORD_GUILD_ID=222
```

The bot uses `222`.

This preserves deployment flexibility while making local development convenient.

## Loading Location

Load `.env` during startup before configuration validation. The preferred boundary is `main()` or a small startup helper called by `main()`.

`load_config()` should remain independently testable with an explicit environment mapping and should not unexpectedly mutate process-wide environment state when unit tests call it directly.

Conceptually:

```text
main()
  |
  +-- load project-root .env
  |
  +-- load_config()
  |
  +-- create_client()
  |
  +-- client.run()
```

The `.env` path should be derived from the directory containing `discord_bot.py`, not from the caller's current working directory. This allows `discord_bot.py` to find the same project `.env` even when launched from another directory.

## Validation and Errors

Retain the existing startup validation:

- missing `DISCORD_BOT_TOKEN` -> clear startup error
- missing/non-numeric `DISCORD_GUILD_ID` -> clear startup error
- `OLLAMA_MODEL` remains optional and keeps the current default behavior

`python-dotenv` parsing must not cause secrets to be printed.

## Documentation

Update `README.md` to instruct local users to copy/create `.env` in the repository root and fill in the Discord token and guild ID.

The documented normal startup becomes:

```bash
uv run python discord_bot.py
```

No shell `export` / PowerShell setup is required for ordinary local use, although explicit OS environment variables remain supported and take precedence.

## Tests

Add focused tests proving:

1. a project-root `.env` can supply `DISCORD_BOT_TOKEN` and `DISCORD_GUILD_ID`;
2. existing environment variables are not overwritten by `.env`;
3. `load_config()` retains existing validation behavior;
4. `.env.example` contains only safe placeholder/default values;
5. the focused Discord/service verification suite still passes.

## Non-Goals

This change does not:

- commit a real `.env` file;
- add encrypted secret storage;
- add multiple guild IDs;
- alter Discord permissions or intents;
- alter search/session/Qwen behavior;
- modify the existing Ollama environment-variable semantics beyond making `.env` values available to the process.
