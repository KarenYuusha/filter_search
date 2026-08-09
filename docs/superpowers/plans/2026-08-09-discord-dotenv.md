# Discord `.env` Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `discord_bot.py` automatically load Discord/Ollama configuration from a project-root `.env` file while preserving explicit OS environment-variable precedence.

**Architecture:** Keep `load_config(environ=...)` unchanged as a pure validator. Add `load_project_environment(env_path: Path | None = None) -> Path`, which calls `load_dotenv(..., override=False)` on the supplied path or `<discord_bot.py directory>/.env`. `main()` invokes it before `load_config()`.

**Tech Stack:** Python 3.12, `python-dotenv`, `unittest`, existing Discord frontend.

## Global Constraints

- Never commit a real Discord token or real private guild configuration.
- `.env` remains ignored by Git; only `.env.example` is committed.
- OS/process environment variables override `.env` values.
- Resolve the default `.env` relative to `discord_bot.py`, not the current working directory.
- Do not change search, Discord routing, sessions, Qwen behavior, permissions, intents, or rendering.

---

### Task 1: Add automatic `.env` startup loading

**Files:**
- Modify: `tests/test_discord_bot.py`
- Modify: `discord_bot.py`
- Modify: `pyproject.toml`
- Create: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Produces: `load_project_environment(env_path: Path | None = None) -> Path`
- Preserves: `load_config(environ: Mapping[str, str] = os.environ) -> DiscordBotConfig`

- [x] **Step 1: Write failing tests**

Add tests proving a temporary `.env` supplies token/guild ID, existing `os.environ` wins, `load_config()` still accepts an explicit mapping, and `.env.example` contains only blank placeholders plus `OLLAMA_MODEL=qwen3.5:2b`.

- [x] **Step 2: Verify RED**

Run:

```bash
python -m unittest tests.test_discord_bot.DiscordConfigTests -v
```

Expected: failure because `load_project_environment` and `.env.example` do not exist yet.

- [x] **Step 3: Add dependency/template**

Add:

```toml
"python-dotenv>=1.1,<2",
```

Create `.env.example`:

```env
DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=
OLLAMA_MODEL=qwen3.5:2b
```

- [x] **Step 4: Implement minimal loader**

Add:

```python
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


def load_project_environment(env_path: Path | None = None) -> Path:
    path = env_path if env_path is not None else PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=path, override=False)
    return path
```

Update `main()` to call `load_project_environment()` immediately before `load_config()`.

- [x] **Step 5: Update README**

Document project-root `.env`, `.env.example`, environment precedence, and normal startup:

```bash
uv run python discord_bot.py
```

- [x] **Step 6: Verify GREEN and regressions**

Run:

```bash
python -m py_compile discord_bot.py toram_search/service.py search_items.py
python -m unittest \
  tests.test_search_service \
  tests.test_discord_bot \
  tests.test_direct_structured_intent \
  tests.test_llm -v
```

Expected: compilation success and zero focused test failures.

- [x] **Step 7: Secret/scope review**

Verify `.env` is absent from the diff, `.env.example` has no real values, `load_dotenv` uses `override=False`, and only startup/configuration/docs/tests changed.
