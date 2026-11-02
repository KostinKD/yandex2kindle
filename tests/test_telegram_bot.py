import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from app import telegram_bot


class TelegramBotTests(unittest.IsolatedAsyncioTestCase):
    def test_user_allowed_filter(self):
        with patch.object(telegram_bot, "ALLOWED_USERS", ["12345", "book_lover"]):
            self.assertTrue(telegram_bot.is_user_allowed({"id": 12345, "username": "other"}))
            self.assertTrue(telegram_bot.is_user_allowed({"id": 99999, "username": "Book_Lover"}))
            self.assertFalse(telegram_bot.is_user_allowed({"id": 99999, "username": "stranger"}))

        with patch.object(telegram_bot, "ALLOWED_USERS", []):
            self.assertTrue(telegram_bot.is_user_allowed({"id": 99999, "username": "anyone"}))

    async def test_bot_start_command(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=Mock(is_success=True))

        msg = {
            "chat": {"id": 111},
            "from": {"id": 111, "username": "user"},
            "text": "/start"
        }

        with patch.object(telegram_bot, "BOT_TOKEN", "fake_token"):
            await telegram_bot.handle_message(client, msg)

        client.post.assert_called_once()
        args, kwargs = client.post.call_args
        self.assertIn("Привет!", kwargs["json"]["text"])

    async def test_bot_stats_command(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=Mock(is_success=True))

        msg = {
            "chat": {"id": 222},
            "from": {"id": 222, "username": "reader"},
            "text": "/stats"
        }

        with patch.object(telegram_bot, "BOT_TOKEN", "fake_token"):
            with patch("app.stats.get_stats", return_value={
                "total_books": 10,
                "reading_books": 2,
                "finished_books": 5,
                "reading_streak_days": 7,
                "total_reading_minutes": 150
            }):
                await telegram_bot.handle_message(client, msg)

        client.post.assert_called_once()
        args, kwargs = client.post.call_args
        self.assertIn("Статистика библиотеки", kwargs["json"]["text"])
        self.assertIn("7 дн.", kwargs["json"]["text"])
