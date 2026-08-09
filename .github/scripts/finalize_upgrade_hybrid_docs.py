from pathlib import Path

spec_path = Path("docs/superpowers/specs/2026-08-10-upgrade-hybrid-display-design.md")
plan_path = Path("docs/superpowers/plans/2026-08-10-upgrade-hybrid-display.md")

path_replacements = [
    (
        "1. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F",
        "2. Don → Don Upgrade A → Don Upgrade C → Don Upgrade F",
    ),
    (
        "2. Don → Don Alternative → Don Upgrade C → Don Upgrade F",
        "1. Don → Don Alternative → Don Upgrade C → Don Upgrade F",
    ),
]

old_tree = """Don
├── Don Upgrade A
│   └── Don Upgrade C
│       └── Don Upgrade F  ◀ selected
└── Don Alternative
    └── Don Upgrade C  ↩ already shown"""
new_tree = """Don
├── Don Alternative
│   └── Don Upgrade C
│       └── Don Upgrade F  ◀ selected
└── Don Upgrade A
    └── Don Upgrade C  ↩ already shown"""

old_tuple_tree = """        \"Don\",
        \"├── Don Upgrade A\",
        \"│   └── Don Upgrade C\",
        \"│       └── Don Upgrade F  ◀ selected\",
        \"└── Don Alternative\",
        \"    └── Don Upgrade C  ↩ already shown\","""
new_tuple_tree = """        \"Don\",
        \"├── Don Alternative\",
        \"│   └── Don Upgrade C\",
        \"│       └── Don Upgrade F  ◀ selected\",
        \"└── Don Upgrade A\",
        \"    └── Don Upgrade C  ↩ already shown\","""

for path in (spec_path, plan_path):
    text = path.read_text()
    for old, new in path_replacements:
        text = text.replace(old, new)
    text = text.replace(old_tree, new_tree)
    text = text.replace(old_tuple_tree, new_tuple_tree)
    path.write_text(text)

plan = plan_path.read_text()
plan = plan.replace(
    "**Files:**\n- Modify: `discord_bot.py`\n- Modify: `tests/test_discord_bot.py`",
    "**Files:**\n- Modify: `discord_bot.py`\n- Create: `tests/test_discord_upgrade_display.py`\n- Regression coverage: `tests/test_discord_bot.py`",
)
plan = plan.replace(
    "Extend `tests/test_discord_bot.py` with an `UpgradeDetailPayload` fixture and assert:",
    "Create `tests/test_discord_upgrade_display.py` with an `UpgradeDetailPayload` fixture and assert:",
)
plan = plan.replace(
    "python -m unittest tests.test_discord_bot -v\n```\n\nExpected: upgrade-detail formatting assertions fail against the current flat edge list.",
    "python -m unittest tests.test_discord_upgrade_display -v\n```\n\nExpected: upgrade-detail formatting assertions fail against the current flat edge list.",
)
plan = plan.replace(
    "python -m unittest tests.test_upgrade_display tests.test_discord_bot tests.test_cli_upgrade -v",
    "python -m unittest tests.test_upgrade_display tests.test_cli_upgrade tests.test_discord_upgrade_display tests.test_discord_bot -v",
)
plan = plan.replace(
    "git add discord_bot.py tests/test_discord_bot.py",
    "git add discord_bot.py tests/test_discord_upgrade_display.py",
)
plan = plan.replace(
    "tests/test_discord_bot.py\n```\n\nNo `.env`",
    "tests/test_discord_upgrade_display.py\n```\n\nNo `.env`",
)
plan_path.write_text(plan)
