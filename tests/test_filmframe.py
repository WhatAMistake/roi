"""Tests for film-frame feature.

Run: py -m pytest tests/test_filmframe.py -v
"""

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_gate_counters():
    """Reset in-memory counters before each test."""
    import app.features.filmframe.gate as gate_mod
    gate_mod._user_daily_counts.clear()
    gate_mod._user_daily_reset.clear()
    gate_mod._global_daily_count = 0
    gate_mod._global_daily_reset = 0.0


def _reload_all():
    """Reload config and gate modules after env changes."""
    import importlib
    import app.features.filmframe.config as cfg
    import app.features.filmframe.gate as gate_mod
    importlib.reload(cfg)
    importlib.reload(gate_mod)


@pytest.fixture
def mock_env_disabled(monkeypatch):
    monkeypatch.setenv("FILM_FRAME_ENABLED", "false")
    monkeypatch.setenv("FILM_FRAME_ALLOWED_USER_IDS", "")
    _reload_all()


@pytest.fixture
def mock_env_enabled(monkeypatch):
    monkeypatch.setenv("FILM_FRAME_ENABLED", "true")
    monkeypatch.setenv("FILM_FRAME_ALLOWED_USER_IDS", "12345,67890")
    monkeypatch.setenv("FILM_FRAME_PER_USER_DAILY_LIMIT", "3")
    monkeypatch.setenv("FILM_FRAME_GLOBAL_DAILY_LIMIT", "50")
    _reload_all()


# ---------------------------------------------------------------------------
# Gate tests
# ---------------------------------------------------------------------------

class TestGate:

    def test_feature_disabled(self, mock_env_disabled):
        from app.features.filmframe.gate import is_feature_available
        allowed, reason = is_feature_available(12345)
        assert not allowed
        assert reason == "feature_disabled"

    def test_regular_user_allowed_under_limits(self, mock_env_enabled):
        from app.features.filmframe.gate import is_feature_available
        allowed, reason = is_feature_available(99999)
        assert allowed
        assert reason is None

    def test_unlimited_user_ignores_limits(self, mock_env_enabled):
        from app.features.filmframe.gate import is_feature_available, record_usage
        for _ in range(10):
            allowed, _ = is_feature_available(12345)
            assert allowed
            record_usage(12345)
        allowed, reason = is_feature_available(12345)
        assert allowed
        assert reason is None

    def test_per_user_limit(self, mock_env_enabled):
        from app.features.filmframe.gate import is_feature_available, record_usage
        for _ in range(3):
            allowed, _ = is_feature_available(99999)
            assert allowed
            record_usage(99999)
        allowed, reason = is_feature_available(99999)
        assert not allowed
        assert reason == "user_limit_reached"

    def test_global_limit(self, mock_env_enabled, monkeypatch):
        monkeypatch.setenv("FILM_FRAME_GLOBAL_DAILY_LIMIT", "2")
        _reload_all()
        from app.features.filmframe.gate import is_feature_available, record_usage
        record_usage(99999)
        record_usage(88888)
        allowed, reason = is_feature_available(99999)
        assert not allowed
        assert reason == "global_limit_reached"


    def test_admin_bypasses_limits(self, mock_env_enabled):
        from app.features.filmframe.gate import is_feature_available, record_usage
        # Admin (id=282208693) should always be allowed
        for _ in range(10):
            allowed, reason = is_feature_available(282208693, admin_id=282208693)
            assert allowed
            assert reason is None
            record_usage(282208693)
        # Still allowed after exceeding per-user limit
        allowed, reason = is_feature_available(282208693, admin_id=282208693)
        assert allowed

    def test_admin_not_in_whitelist_still_works(self, mock_env_enabled):
        from app.features.filmframe.gate import is_feature_available
        # Admin not in whitelist but still allowed
        allowed, reason = is_feature_available(99999, admin_id=99999)
        assert allowed
        assert reason is None




# ---------------------------------------------------------------------------
# Scene builder tests
# ---------------------------------------------------------------------------

class TestSceneBuilder:

    def test_build_scene_returns_valid_structure(self):
        from app.features.filmframe.scene_builder import SceneBuilder
        import json

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({
                "preview": "A quiet bus early morning.",
                "scene": "Interior of a city bus at 6:15 AM. Overcast morning light.",
            })))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        sb = SceneBuilder(api_key="test", api_base="https://test.com")
        sb.client = mock_client

        result = sb.build_scene("I feel tired and empty after a long week")
        assert "preview" in result
        assert "scene" in result
        assert "image_prompt" in result
        prompt = result["image_prompt"]
        assert "35mm" in prompt or "Portra" in prompt
        # Style-first assembly: film lock before scene body.
        assert prompt.index("Amateur 35mm") < prompt.index("Scene:")
        assert "Interior of a city bus" in prompt
        assert "No ultra-sharp" in prompt or "no ultra-sharp" in prompt.lower()

    def test_parse_markdown_fenced_json(self):
        from app.features.filmframe.scene_builder import parse_scene_payload

        raw = '''```json
{
  "preview": "Сцена на платформе в сумерках.",
  "scene": "Open railway platform at dusk. Red taillights fade into haze."
}
```'''
        result = parse_scene_payload(raw)
        assert result["preview"] == "Сцена на платформе в сумерках."
        assert "railway platform" in result["scene"]
        assert not result["preview"].startswith("{")
        assert "```" not in result["preview"]

    def test_parse_truncated_json_extracts_preview(self):
        from app.features.filmframe.scene_builder import parse_scene_payload

        # Mirrors the broken production response: fenced, truncated mid-string.
        raw = (
            '```json\n'
            '{\n'
            '  "preview": "Сцена на открытой железнодорожной платформе в сумерках. '
            'Красные габаритные огни ушедшего поезда растворяются в дымке на горизонте, '
            'а следующего рейса еще'
        )
        result = parse_scene_payload(raw)
        assert result["preview"].startswith("Сцена на открытой железнодорожной")
        assert not result["preview"].startswith("{")
        assert "```" not in result["preview"]
        assert '"preview"' not in result["preview"]

    def test_build_scene_rejects_unparseable_json_blob(self):
        from app.features.filmframe.scene_builder import SceneBuilder

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='```json\n{"nope": true'))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        sb = SceneBuilder(api_key="test", api_base="https://test.com")
        sb.client = mock_client

        with pytest.raises(RuntimeError):
            sb.build_scene("in-between state")

    def test_apply_edit_updates_scene(self):
        from app.features.filmframe.scene_builder import SceneBuilder
        import json

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=json.dumps({
                "preview": "Same bus but evening. Golden hour light.",
                "scene": "Interior of a city bus at 6:45 PM. Warm golden light.",
            })))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        sb = SceneBuilder(api_key="test", api_base="https://test.com")
        sb.client = mock_client

        result = sb.apply_edit("A bus at morning", "make it evening instead")
        assert "evening" in result["preview"].lower() or "golden" in result["preview"].lower()


# ---------------------------------------------------------------------------
# Image client tests
# ---------------------------------------------------------------------------

class TestImageClient:

    @pytest.mark.asyncio
    async def test_generate_image_success(self):
        from app.features.filmframe.image_client import ImageClient
        import base64

        mock_openai = MagicMock()
        mock_data = MagicMock()
        mock_data.b64_json = base64.b64encode(b"fake_image_data").decode()
        mock_data.url = None
        mock_data.revised_prompt = "seed: 12345"
        mock_response = MagicMock()
        mock_response.data = [mock_data]
        mock_openai.images.generate.return_value = mock_response

        with patch("app.features.filmframe.image_client.OpenAI", return_value=mock_openai):
            ic = ImageClient(api_key="test", api_base="https://test.com")
            result = await ic.generate_image("a test prompt")

        assert result["b64_json"] is not None
        assert result["latency_ms"] >= 0
        assert result["seed"] == 12345

    @pytest.mark.asyncio
    async def test_generate_image_timeout(self):
        from app.features.filmframe.image_client import ImageClient

        mock_openai = MagicMock()
        mock_openai.images.generate.side_effect = TimeoutError("Request timeout")

        with patch("app.features.filmframe.image_client.OpenAI", return_value=mock_openai):
            ic = ImageClient(api_key="test", api_base="https://test.com")
            with pytest.raises(TimeoutError):
                await ic.generate_image("a test prompt")

    @pytest.mark.asyncio
    async def test_generate_image_429(self):
        from app.features.filmframe.image_client import ImageClient

        mock_openai = MagicMock()
        mock_openai.images.generate.side_effect = Exception("429 Too Many Requests")

        with patch("app.features.filmframe.image_client.OpenAI", return_value=mock_openai):
            ic = ImageClient(api_key="test", api_base="https://test.com")
            with pytest.raises(Exception) as exc_info:
                await ic.generate_image("a test prompt")
            assert "429" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_image_content_filter(self):
        from app.features.filmframe.image_client import ImageClient

        mock_openai = MagicMock()
        mock_openai.images.generate.side_effect = Exception("content filter triggered")

        with patch("app.features.filmframe.image_client.OpenAI", return_value=mock_openai):
            ic = ImageClient(api_key="test", api_base="https://test.com")
            with pytest.raises(Exception) as exc_info:
                await ic.generate_image("a test prompt")
            assert "content" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_generate_image_5xx(self):
        from app.features.filmframe.image_client import ImageClient

        mock_openai = MagicMock()
        mock_openai.images.generate.side_effect = Exception("503 Service Unavailable")

        with patch("app.features.filmframe.image_client.OpenAI", return_value=mock_openai):
            ic = ImageClient(api_key="test", api_base="https://test.com")
            with pytest.raises(Exception) as exc_info:
                await ic.generate_image("a test prompt")
            assert "503" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Logger tests
# ---------------------------------------------------------------------------

class TestLogger:

    def test_log_run_writes_to_file(self, tmp_path):
        from app.features.filmframe.logger import log_run
        import app.features.filmframe.logger as log_mod

        original = log_mod.FILM_FRAME_LOG_FILE
        log_mod.FILM_FRAME_LOG_FILE = tmp_path / "film_frame_runs.jsonl"
        try:
            log_run(
                user_id=12345,
                description="test description",
                preview="test preview",
                image_prompt="test prompt",
                model="test-model",
                seed=42,
                latency_ms=1500,
                status="success",
            )
            assert log_mod.FILM_FRAME_LOG_FILE.exists()
            content = log_mod.FILM_FRAME_LOG_FILE.read_text()
            assert "12345" in content
            assert "test-model" in content
            assert "success" in content
            assert "sk-" not in content.lower()
            assert "api_key" not in content.lower()
        finally:
            log_mod.FILM_FRAME_LOG_FILE = original


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestConfig:

    def test_film_technical_suffix_is_string(self):
        from app.features.filmframe.config import FILM_TECHNICAL_SUFFIX
        assert isinstance(FILM_TECHNICAL_SUFFIX, str)
        assert len(FILM_TECHNICAL_SUFFIX) > 50
        assert "35mm" in FILM_TECHNICAL_SUFFIX
        lower = FILM_TECHNICAL_SUFFIX.lower()
        assert "soft focus" in lower or "film grain" in lower
        assert "ultra-sharp" in lower or "hyperdetailed" in lower

    def test_build_image_prompt_is_style_first(self):
        from app.features.filmframe.config import build_image_prompt

        prompt = build_image_prompt(
            "Two melting popsicles held against a sunlit street; trees blurred behind."
        )
        assert prompt.startswith("Amateur 35mm")
        assert "Scene: Two melting popsicles" in prompt
        assert prompt.index("Amateur 35mm") < prompt.index("Scene:")
        assert prompt.index("Scene:") < prompt.index("Handheld eye-level")
        assert "No ultra-sharp" in prompt
        assert "Portra 400" in prompt

    def test_scene_builder_prompt_has_rules(self):
        from app.features.filmframe.config import SCENE_BUILDER_SYSTEM_PROMPT
        assert "CRITICAL RULES" in SCENE_BUILDER_SYSTEM_PROMPT
        assert "emotional labels" in SCENE_BUILDER_SYSTEM_PROMPT.lower()
        assert "cliché" in SCENE_BUILDER_SYSTEM_PROMPT.lower()
        assert "snapshot" in SCENE_BUILDER_SYSTEM_PROMPT.lower()
        lower = SCENE_BUILDER_SYSTEM_PROMPT.lower()
        assert "one main subject" in lower
        assert "close or medium" in lower

    def test_default_model(self):
        from app.features.filmframe.config import FILM_FRAME_MODEL
        assert FILM_FRAME_MODEL == "seedream-5-0-pro-260628"


# ---------------------------------------------------------------------------
# Error classification tests (from utils, no aiogram dependency)
# ---------------------------------------------------------------------------

class TestErrorClassification:

    def test_classify_timeout(self):
        from app.features.filmframe.utils import classify_error
        assert classify_error("Request timeout after 30s") == "timeout"

    def test_classify_rate_limit(self):
        from app.features.filmframe.utils import classify_error
        assert classify_error("429 Too Many Requests") == "rate_limit"

    def test_classify_content_filter(self):
        from app.features.filmframe.utils import classify_error
        assert classify_error("content filter triggered: safety violation") == "content_filter"

    def test_classify_server_error(self):
        from app.features.filmframe.utils import classify_error
        assert classify_error("502 Bad Gateway") == "server_error"

    def test_classify_unknown(self):
        from app.features.filmframe.utils import classify_error
        assert classify_error("something weird happened") == "unknown"


# ---------------------------------------------------------------------------
# i18n regression tests
# ---------------------------------------------------------------------------

class TestI18nRegression:

    def test_button_assoc_exists(self):
        from i18n import t
        assert t("ru", "button_assoc") == "Анализ ассоциаций"
        assert t("en", "button_assoc") == "Association analysis"

    def test_button_analyze_exists(self):
        from i18n import t
        assert t("ru", "button_analyze") == "Анализ истории"
        assert t("en", "button_analyze") == "Story analysis"

    def test_button_filmframe_exists(self):
        from i18n import t
        assert t("ru", "button_filmframe") == "Снимок на плёнку"
        assert t("en", "button_filmframe") == "Snapshot"

    def test_button_cancel_exists(self):
        from i18n import t
        assert t("ru", "button_cancel") == "Отмена"
        assert t("en", "button_cancel") == "Cancel"

    def test_button_reset_removed(self):
        from i18n import t
        result_ru = t("ru", "button_reset")
        result_en = t("en", "button_reset")
        assert result_ru == "button_reset"
        assert result_en == "button_reset"

    def test_meta_prompt_still_exists(self):
        from i18n import t
        assert len(t("ru", "meta_prompt")) > 10
        assert len(t("en", "meta_prompt")) > 10

    def test_reset_confirm_still_exists(self):
        from i18n import t
        assert len(t("ru", "reset_confirm")) > 5
        assert len(t("en", "reset_confirm")) > 5


# ---------------------------------------------------------------------------
# Filmframe localization tests (from utils, no aiogram dependency)
# ---------------------------------------------------------------------------

class TestFilmframeLocalization:

    def test_ru_strings(self):
        from app.features.filmframe.utils import get_localized
        assert "Опиши" in get_localized("ff_describe_prompt", "ru")
        assert "Заснять" == get_localized("ff_button_shoot", "ru")
        assert "Изменить" == get_localized("ff_button_edit", "ru")
        assert "Отмена" == get_localized("ff_button_cancel", "ru")

    def test_en_strings(self):
        from app.features.filmframe.utils import get_localized
        assert "Describe" in get_localized("ff_describe_prompt", "en")
        assert "Shoot" == get_localized("ff_button_shoot", "en")
        assert "Edit" == get_localized("ff_button_edit", "en")
        assert "Cancel" == get_localized("ff_button_cancel", "en")

    def test_fallback_to_en(self):
        from app.features.filmframe.utils import get_localized
        assert "Shoot" == get_localized("ff_button_shoot", "fr")


# ---------------------------------------------------------------------------
# State machine tests (from utils, no aiogram dependency)
# ---------------------------------------------------------------------------

class TestStateMachine:

    def test_is_filmframe_state(self):
        from app.features.filmframe.utils import is_filmframe_state
        assert is_filmframe_state("ff_describe")
        assert is_filmframe_state("ff_preview")
        assert is_filmframe_state("ff_edit")
        assert not is_filmframe_state("chat")
        assert not is_filmframe_state("assoc_freedom")
        assert not is_filmframe_state("analyze_story")


# ---------------------------------------------------------------------------
# Handler regression tests
# ---------------------------------------------------------------------------

class TestHandlers:

    @pytest.mark.asyncio
    async def test_preview_buttons_pop_message_queue(self):
        """Edit/cancel must free the flood-protection queue slot."""
        from app.features.filmframe.handlers import handle_ff_preview

        bot = MagicMock()
        bot.user_langs = {1: "ru"}
        bot.user_states = {1: "ff_preview"}
        bot.message_queue = {1: [1.0, 2.0]}
        bot.filmframe_state = {
            1: {
                "description": "x",
                "preview": "p",
                "scene": "s",
                "image_prompt": "prompt",
                "confirmed": False,
            }
        }

        message = MagicMock()
        message.from_user.id = 1
        message.answer = AsyncMock()

        await handle_ff_preview(bot, message, "Изменить")

        assert bot.user_states[1] == "ff_edit"
        assert bot.message_queue[1] == [2.0]
        message.answer.assert_awaited()

    @pytest.mark.asyncio
    async def test_do_generate_sends_buffered_photo(self):
        """Generated b64 images must be sent via BufferedInputFile, not FSInputFile."""
        from app.features.filmframe import handlers as h
        import base64

        bot = MagicMock()
        bot.user_langs = {1: "ru"}
        bot.user_states = {1: "ff_preview"}
        bot.message_queue = {1: [1.0]}
        bot.filmframe_state = {
            1: {
                "description": "x",
                "preview": "p",
                "scene": "s",
                "image_prompt": "prompt",
                "confirmed": False,
            }
        }
        bot.bot.send_chat_action = AsyncMock()

        message = MagicMock()
        message.from_user.id = 1
        message.chat.id = 1
        message.answer = AsyncMock()
        message.answer_photo = AsyncMock()

        fake_b64 = base64.b64encode(b"fake-png-bytes").decode()

        class FakeIC:
            async def generate_image(self, prompt, size="1024x1024"):
                return {
                    "url": None,
                    "b64_json": fake_b64,
                    "seed": 7,
                    "latency_ms": 12,
                }

        with patch.object(h, "ImageClient", return_value=FakeIC()), \
             patch.object(h, "record_usage"), \
             patch.object(h, "log_run"), \
             patch("src.telegram_bot.get_main_keyboard", return_value="main-kb"), \
             patch.object(h, "_cancel_ff", new_callable=AsyncMock) as cancel_mock:
            await h._do_generate(bot, message)

        message.answer_photo.assert_awaited_once()
        photo_arg = message.answer_photo.await_args.args[0]
        from aiogram.types import BufferedInputFile
        assert isinstance(photo_arg, BufferedInputFile)
        # Success must restore keyboard on the photo, not send cancel copy.
        assert message.answer_photo.await_args.kwargs.get("reply_markup") == "main-kb"
        cancel_mock.assert_not_awaited()
        assert bot.user_states[1] == "chat"
        assert 1 not in bot.filmframe_state
        assert bot.message_queue[1] == []

        # No "Действие отменено" / action_cancelled text after success.
        for call in message.answer.await_args_list:
            text = call.args[0] if call.args else ""
            assert "отменено" not in str(text).lower()
            assert "cancelled" not in str(text).lower()

    @pytest.mark.asyncio
    async def test_do_generate_error_also_skips_cancel_copy(self):
        """Failed shoot should show ff_error and restore keyboard without cancel notice."""
        from app.features.filmframe import handlers as h

        bot = MagicMock()
        bot.user_langs = {1: "ru"}
        bot.user_states = {1: "ff_preview"}
        bot.message_queue = {1: [1.0]}
        bot.filmframe_state = {
            1: {
                "description": "x",
                "preview": "p",
                "scene": "s",
                "image_prompt": "prompt",
                "confirmed": False,
            }
        }
        bot.bot.send_chat_action = AsyncMock()

        message = MagicMock()
        message.from_user.id = 1
        message.chat.id = 1
        message.answer = AsyncMock()
        message.answer_photo = AsyncMock()

        class FakeIC:
            async def generate_image(self, prompt, size="1024x1024"):
                raise RuntimeError("boom")

        with patch.object(h, "ImageClient", return_value=FakeIC()), \
             patch.object(h, "record_usage"), \
             patch.object(h, "log_run"), \
             patch("src.telegram_bot.get_main_keyboard", return_value="main-kb"), \
             patch.object(h, "_cancel_ff", new_callable=AsyncMock) as cancel_mock:
            await h._do_generate(bot, message)

        cancel_mock.assert_not_awaited()
        assert bot.user_states[1] == "chat"
        assert 1 not in bot.filmframe_state

        # Last answer is error with main keyboard, not cancel copy.
        assert message.answer.await_count >= 2
        last = message.answer.await_args_list[-1]
        assert last.kwargs.get("reply_markup") == "main-kb"
        text = last.args[0] if last.args else ""
        assert "отменено" not in str(text).lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
