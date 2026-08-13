from __future__ import annotations

import unittest

from toram_discord.config import PROJECT_ROOT, load_config


class SkillBotConfigTests(unittest.TestCase):
    def test_skill_database_path_defaults_to_canonical_database(self):
        config = load_config(
            {
                "DISCORD_BOT_TOKEN": "token",
                "DISCORD_GUILD_ID": "333",
            }
        )
        self.assertEqual(
            config.skill_database_path,
            (PROJECT_ROOT / "coryn_data/database/skills.sqlite").resolve(),
        )

    def test_skill_database_path_can_be_overridden(self):
        config = load_config(
            {
                "DISCORD_BOT_TOKEN": "token",
                "DISCORD_GUILD_ID": "333",
                "SKILL_DATABASE_PATH": "tmp/custom-skills.sqlite",
            }
        )
        self.assertEqual(
            config.skill_database_path,
            (PROJECT_ROOT / "tmp/custom-skills.sqlite").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
