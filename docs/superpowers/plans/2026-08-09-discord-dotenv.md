# Discord `.env` Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `discord_bot.py` automatically load Discord/Ollama configuration from a project-root `.env` file while preserving explicit OS environment-variable precedence.

**Architecture:** Keep `load_config(environ=...)` unchanged as a pure validator. Add a small startup helper in `discord_bot.py` that resolves `.env` relative to `discord_bot.py` and calls `python-dotenv` with `override=False`; `main()` invokes that helper before `load_config()`. Commit only `.env.example`; the real `.env` remains ignored.

**Tech Stack:** Python 3.12, `python-dotenv`, `unittest`, existing Discord bot frontend.

## Global Constraints

- Never commit a real Discord token or real private guild configuration.
- `.env` must remain ignored by Git.
- Existing OS/process environment variables must take precedence over `.env` values.
- The `.env` path must be based on the directory containing `discord_bot.py`, not the caller's current working directory.
- `load_config()` must remain independently testable with an explicit mapping and must not load/mutate `.env` itself.
- This change must not alter Discord routing, search/session behavior, Qwen behavior, permissions, intents, or rendering.

---

### Task 1: Load project `.env` safely at Discord startup

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_bot.py`
- Modify: `pyproject.toml`
- Create: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Produces: `load_project_environment(env_path: Path | None = None) -> Path`, which loads `env_path` or `<directory-containing-discord_bot.py>/.env` with `override=False` and returns the path used.
- Preserves: `load_config(environ: Mapping[str, str] = os.environ) -> DiscordBotConfig` exactly as the configuration validator.
- `main()` calls `load_project_environment()` before `load_config()`.

- [ ] **Step 1: Write failing `.env` loading and precedence tests**

Add imports:

```python
import os
from unittest.mock import patch

from discord_bot import load_config, load_project_environment
```

Add a test class:

```python
class DiscordConfigTests(unittest.TestCase):
    def test_dotenv_supplies_token_and_guild_id(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "DISCORD_BOT_TOKEN=from-file\n"
                "DISCORD_GUILD_ID=123456789\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                used_path = load_project_environment(env_path)
                config = load_config()

            self.assertEqual(used_path, env_path)
            self.assertEqual(config.token, "from-file")
            self.assertEqual(config.guild_id, 123456789)

    def test_existing_environment_overrides_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "DISCORD_BOT_TOKEN=from-file\n"
                "DISCORD_GUILD_ID=111\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "DISCORD_BOT_TOKEN": "from-shell",
                    "DISCORD_GUILD_ID": "222",
                },
                clear=True,
            ):
                load_project_environment(env_path)
                config = load_config()

            self.assertEqual(config.token, "from-shell")
            self.assertEqual(config.guild_id, 222)

    def test_load_config_keeps_explicit_mapping_validation(self):
        config = load_config(
            {
                "DISCORD_BOT_TOKEN": "explicit-token",
                "DISCORD_GUILD_ID": "333",
            }
        )
        self.assertEqual(config.token, "explicit-token")
        self.assertEqual(config.guild_id, 333)

    def test_env_example_has_only_safe_template_values(self):
        text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(
            encoding="utf-8"
        )
        self.assertIn("DISCORD_BOT_TOKEN=", text)
        self.assertIn("DISCORD_GUILD_ID=", text)
        self.assertIn("OLLAMA_MODEL=qwen3.5:2b", text)
        self.assertNotIn("from-file", text)
        self.assertNotIn("from-shell", text)
```

- [ ] **Step 2: Run the new config tests and verify they fail**

Run:

```bash
python -m unittest tests.test_discord_bot.DiscordConfigTests -v
```

Expected before implementation: import failure for `load_project_environment` and/or missing `.env.example`.

- [ ] **Step 3: Add `python-dotenv` dependency and safe template**

Add to `pyproject.toml` dependencies:

```toml
"python-dotenv>=1.1,<2",
```

Create `.env.example` exactly as:

```env
DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=
OLLAMA_MODEL=qwen3.5:2b
```

Do not create or commit `.env`.

- [ ] **Step 4: Implement the startup loader**

In `discord_bot.py`, import:

```python
from dotenv import load_dotenv
```

Add near the config functions:

```python
PROJECT_ROOT = Path(__file__).resolve().parent


def load_project_environment(env_path: Path | None = None) -> Path:
    path = env_path if env_path is not None else PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=path, override=False)
    return path
```

Keep `load_config()` unchanged. Update `main()` to:

```python
def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_project_environment()
    config = load_config()
    client = create_client(config)
    client.run(config.token, log_handler=None)
```

- [ ] **Step 5: Update README local setup**

Document that users create `.env` beside `discord_bot.py` with:

```env
DISCORD_BOT_TOKEN=your_real_token
DISCORD_GUILD_ID=123456789012345678
OLLAMA_MODEL=qwen3.5:2b
```

State that `.env` is ignored by Git, `.env.example` is the safe template, explicit shell/OS environment variables override `.env`, and normal startup is:

```bash
uv run python discord_bot.py
```

- [ ] **Step 6: Run focused config and Discord tests**

Run:

```bash
python -m unittest tests.test_discord_bot -v
```

Expected: all Discord bot tests pass.

- [ ] **Step 7: Run the established focused regression gate**

Run:

```bash
python -m py_compile discord_bot.py toram_search/service.py search_items.py
python -m unittest \
  tests.test_search_service \
  tests.test_discord_bot \
  tests.test_direct_structured_intent \
  tests.test_llm -v
```

Expected: compilation succeeds and the focused suite has zero failures.

- [ ] **Step 8: Review final diff for secret safety and scope**

Verify:

```text
.env is not a changed file
.env.example contains no real token/guild value
load_dotenv uses override=False
load_config still only validates its supplied mapping
main loads .env before load_config
no Discord/search/session behavior changed
```

- [ ] **Step 9: Commit and update PR #9 verification notes**

Commit message:

```bash
git commit -m "feat: load Discord config from dotenv"
```

Update PR #9 to note automatic `.env` loading and the fresh focused verification result.
