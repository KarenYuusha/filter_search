from __future__ import annotations

import unittest
from pathlib import Path

import discord_bot


class DiscordModuleBoundaryTests(unittest.TestCase):
    def test_config_symbols_have_canonical_package_owner(self):
        from toram_discord.config import (
            DiscordBotConfig,
            build_intents,
            extract_mentioned_query,
            is_allowed_message,
            load_config,
            load_project_environment,
        )

        self.assertIs(discord_bot.DiscordBotConfig, DiscordBotConfig)
        self.assertIs(discord_bot.build_intents, build_intents)
        self.assertIs(discord_bot.extract_mentioned_query, extract_mentioned_query)
        self.assertIs(discord_bot.is_allowed_message, is_allowed_message)
        self.assertIs(discord_bot.load_config, load_config)
        self.assertIs(discord_bot.load_project_environment, load_project_environment)

    def test_config_project_root_remains_repository_root(self):
        from toram_discord.config import PROJECT_ROOT

        self.assertEqual(PROJECT_ROOT, Path(discord_bot.__file__).resolve().parent)
        self.assertEqual(discord_bot.PROJECT_ROOT, PROJECT_ROOT)

    def test_session_symbols_have_canonical_package_owner(self):
        from toram_discord.sessions import DiscordSearchSession, DiscordSessionManager

        self.assertIs(discord_bot.DiscordSearchSession, DiscordSearchSession)
        self.assertIs(discord_bot.DiscordSessionManager, DiscordSessionManager)

    def test_render_symbols_have_canonical_package_owner(self):
        from toram_discord.render import (
            PAGE_SIZE,
            build_help_embed,
            build_item_detail_embed,
            build_search_results_embed,
            truncate_discord_text,
            valid_local_image_paths,
            visible_attachment_name,
        )

        self.assertEqual(PAGE_SIZE, 5)
        self.assertEqual(discord_bot.PAGE_SIZE, PAGE_SIZE)
        self.assertIs(discord_bot.build_help_embed, build_help_embed)
        self.assertIs(discord_bot.build_item_detail_embed, build_item_detail_embed)
        self.assertIs(discord_bot.build_search_results_embed, build_search_results_embed)
        self.assertIs(discord_bot.truncate_discord_text, truncate_discord_text)
        self.assertIs(discord_bot.valid_local_image_paths, valid_local_image_paths)
        self.assertIs(discord_bot.visible_attachment_name, visible_attachment_name)

    def test_service_bridge_has_canonical_package_owner(self):
        from toram_discord.views import (
            run_clarification_sync,
            run_confirmed_request_sync,
            run_item_detail_sync,
            run_item_understanding_choice_sync,
            run_pending_item_search_confirmation_sync,
            run_query_sync,
            run_upgrade_selection_sync,
            send_if_current,
        )

        self.assertIs(discord_bot.run_query_sync, run_query_sync)
        self.assertIs(discord_bot.run_confirmed_request_sync, run_confirmed_request_sync)
        self.assertIs(discord_bot.run_clarification_sync, run_clarification_sync)
        self.assertIs(discord_bot.run_item_understanding_choice_sync, run_item_understanding_choice_sync)
        self.assertIs(
            discord_bot.run_pending_item_search_confirmation_sync,
            run_pending_item_search_confirmation_sync,
        )
        self.assertIs(discord_bot.run_item_detail_sync, run_item_detail_sync)
        self.assertIs(discord_bot.run_upgrade_selection_sync, run_upgrade_selection_sync)
        self.assertIs(discord_bot.send_if_current, send_if_current)

    def test_view_symbols_have_canonical_package_owner(self):
        from toram_discord.views import (
            VIEW_TIMEOUT_SECONDS,
            ActionButton,
            ActionSelect,
            ItemDetailView,
            ItemUnderstandingView,
            QwenConfirmationView,
            SearchResultsView,
            SessionBoundView,
            StatClarificationView,
            build_item_detail_message,
            build_service_outcome_message,
            edit_service_outcome,
        )

        self.assertEqual(VIEW_TIMEOUT_SECONDS, 900)
        self.assertEqual(discord_bot.VIEW_TIMEOUT_SECONDS, VIEW_TIMEOUT_SECONDS)
        self.assertIs(discord_bot.SessionBoundView, SessionBoundView)
        self.assertIs(discord_bot.ActionButton, ActionButton)
        self.assertIs(discord_bot.ActionSelect, ActionSelect)
        self.assertIs(discord_bot.SearchResultsView, SearchResultsView)
        self.assertIs(discord_bot.ItemDetailView, ItemDetailView)
        self.assertIs(discord_bot.StatClarificationView, StatClarificationView)
        self.assertIs(discord_bot.ItemUnderstandingView, ItemUnderstandingView)
        self.assertIs(discord_bot.QwenConfirmationView, QwenConfirmationView)
        self.assertIs(discord_bot.build_item_detail_message, build_item_detail_message)
        self.assertIs(discord_bot.build_service_outcome_message, build_service_outcome_message)
        self.assertIs(discord_bot.edit_service_outcome, edit_service_outcome)

    def test_app_symbols_have_canonical_package_owner(self):
        from toram_discord.app import create_client, main, process_tagged_query

        self.assertIs(discord_bot.process_tagged_query, process_tagged_query)
        self.assertIs(discord_bot.create_client, create_client)
        self.assertIs(discord_bot.main, main)


if __name__ == "__main__":
    unittest.main()
