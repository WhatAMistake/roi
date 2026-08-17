"""Film-frame flow handlers.

Integrates into the existing user_states state machine exactly like /assoc and /analyze.
Uses states: ff_describe, ff_preview, ff_edit.
"""

import asyncio
import base64
import logging

from aiogram import types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from .config import FILM_FRAME_MODEL
from .gate import is_feature_available, record_usage
from .scene_builder import SceneBuilder
from .image_client import ImageClient
from .logger import log_run
from .utils import _ff_key, _CONFIRM_WORDS, classify_error

logger = logging.getLogger("filmframe.handlers")

# State prefixes
STATE_DESCRIBE = "ff_describe"
STATE_PREVIEW = "ff_preview"
STATE_EDIT = "ff_edit"


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def get_describe_keyboard(lang: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=_ff_key(lang, "ff_button_cancel")))
    return builder.as_markup(resize_keyboard=True)


def get_preview_keyboard(lang: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=_ff_key(lang, "ff_button_shoot")))
    builder.add(KeyboardButton(text=_ff_key(lang, "ff_button_edit")))
    builder.add(KeyboardButton(text=_ff_key(lang, "ff_button_cancel")))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_edit_keyboard(lang: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=_ff_key(lang, "ff_button_cancel")))
    return builder.as_markup(resize_keyboard=True)


# ---------------------------------------------------------------------------
# Flow entry point
# ---------------------------------------------------------------------------

async def start_filmframe(bot_instance, message: types.Message) -> None:
    """Entry point: /meta or button."""
    user_id = message.from_user.id
    lang = bot_instance.user_langs.get(user_id, "ru")

    # Feature gate (admin bypasses limits)
    admin_id = getattr(bot_instance, 'admin_id', 0)
    allowed, reason = is_feature_available(user_id, admin_id=admin_id)
    if not allowed:
        if reason == "user_limit_reached":
            await message.answer(_ff_key(lang, "ff_limit_user"))
        elif reason == "global_limit_reached":
            await message.answer(_ff_key(lang, "ff_limit_global"))
        else:
            await message.answer(_ff_key(lang, "ff_disabled"))
        return

    # Initialize filmframe state
    if not hasattr(bot_instance, "filmframe_state"):
        bot_instance.filmframe_state = {}

    bot_instance.filmframe_state[user_id] = {
        "description": None,
        "preview": None,
        "scene": None,
        "image_prompt": None,
        "confirmed": False,
    }

    bot_instance.user_states[user_id] = STATE_DESCRIBE
    await message.answer(
        _ff_key(lang, "ff_describe_prompt"),
        reply_markup=get_describe_keyboard(lang),
    )


# ---------------------------------------------------------------------------
# State handlers
# ---------------------------------------------------------------------------

async def handle_ff_describe(bot_instance, message: types.Message, text: str) -> None:
    """User described state -> build scene."""
    user_id = message.from_user.id
    lang = bot_instance.user_langs.get(user_id, "ru")

    try:
        ff = bot_instance.filmframe_state.get(user_id, {})
        if not ff:
            await _cancel_ff(bot_instance, message)
            return

        ff["description"] = text
        bot_instance.user_states[user_id] = STATE_PREVIEW

        await message.answer(_ff_key(lang, "ff_building_scene"))

        therapist = bot_instance._get_therapist(user_id)
        sb = SceneBuilder(api_key=therapist.api_key, api_base=therapist.api_base)

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, sb.build_scene, text)
        except Exception as e:
            logger.error(f"Scene building failed for user {user_id}: {e}")
            await message.answer(_ff_key(lang, "ff_error"))
            await _cancel_ff(bot_instance, message)
            return

        ff["preview"] = result.get("preview", "")
        ff["scene"] = result.get("scene", "")
        ff["image_prompt"] = result.get("image_prompt", "")

        await message.answer(
            _ff_key(lang, "ff_scene_ready", preview=ff["preview"]),
            reply_markup=get_preview_keyboard(lang),
        )
    finally:
        _pop_message_queue(bot_instance, user_id)


async def handle_ff_preview(bot_instance, message: types.Message, text: str) -> None:
    """User on preview -> confirm/edit/cancel."""
    user_id = message.from_user.id
    lang = bot_instance.user_langs.get(user_id, "ru")

    try:
        ff = bot_instance.filmframe_state.get(user_id, {})

        if not ff or not ff.get("scene"):
            await _cancel_ff(bot_instance, message)
            return

        text_lower = text.strip().lower()
        text_clean = text.strip()

        shoot_button = _ff_key(lang, "ff_button_shoot")
        edit_button = _ff_key(lang, "ff_button_edit")
        cancel_button = _ff_key(lang, "ff_button_cancel")

        if text_clean == shoot_button:
            # _do_generate owns queue cleanup for the long image call
            await _do_generate(bot_instance, message, pop_queue=False)
            return

        if text_clean == edit_button:
            bot_instance.user_states[user_id] = STATE_EDIT
            await message.answer(
                _ff_key(lang, "ff_edit_prompt"),
                reply_markup=get_edit_keyboard(lang),
            )
            return

        if text_clean == cancel_button:
            await _cancel_ff(bot_instance, message)
            return

        # Natural language interpretation
        is_confirm = any(w in text_lower.split() or text_lower == w for w in _CONFIRM_WORDS)

        if is_confirm and len(text_clean) > 5:
            stripped = text_lower
            for w in sorted(_CONFIRM_WORDS, key=len, reverse=True):
                stripped = stripped.replace(w, "", 1).strip().lstrip(",;. -").strip()
            if stripped:
                await _apply_edit_and_preview(bot_instance, message, ff, stripped, lang, pop_queue=False)
                return

        if is_confirm:
            await _do_generate(bot_instance, message, pop_queue=False)
            return

        await _apply_edit_and_preview(bot_instance, message, ff, text_clean, lang, pop_queue=False)
    finally:
        _pop_message_queue(bot_instance, user_id)


async def handle_ff_edit(bot_instance, message: types.Message, text: str) -> None:
    """User providing edit instructions."""
    user_id = message.from_user.id
    lang = bot_instance.user_langs.get(user_id, "ru")

    try:
        ff = bot_instance.filmframe_state.get(user_id, {})

        if not ff or not ff.get("scene"):
            await _cancel_ff(bot_instance, message)
            return

        # Keep this message in the queue until edit+preview finishes
        await _apply_edit_and_preview(bot_instance, message, ff, text.strip(), lang, pop_queue=False)
    finally:
        _pop_message_queue(bot_instance, user_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _apply_edit_and_preview(bot_instance, message: types.Message,
                                   ff: dict, edit_text: str, lang: str,
                                   pop_queue: bool = True) -> None:
    user_id = message.from_user.id

    try:
        await message.answer(_ff_key(lang, "ff_building_scene"))
        bot_instance.user_states[user_id] = STATE_PREVIEW

        therapist = bot_instance._get_therapist(user_id)
        sb = SceneBuilder(api_key=therapist.api_key, api_base=therapist.api_base)

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, sb.apply_edit, ff["scene"], edit_text)
        except Exception as e:
            logger.error(f"Scene edit failed for user {user_id}: {e}")
            await message.answer(_ff_key(lang, "ff_error"))
            await _cancel_ff(bot_instance, message)
            return

        ff["preview"] = result.get("preview", "")
        ff["scene"] = result.get("scene", "")
        ff["image_prompt"] = result.get("image_prompt", "")

        await message.answer(
            _ff_key(lang, "ff_scene_ready", preview=ff["preview"]),
            reply_markup=get_preview_keyboard(lang),
        )
    finally:
        if pop_queue:
            _pop_message_queue(bot_instance, user_id)


async def _do_generate(bot_instance, message: types.Message, pop_queue: bool = True) -> None:
    from src.telegram_bot import get_main_keyboard

    user_id = message.from_user.id
    lang = bot_instance.user_langs.get(user_id, "ru")
    ff = bot_instance.filmframe_state.get(user_id, {})

    try:
        if not ff or not ff.get("image_prompt"):
            await _cancel_ff(bot_instance, message)
            return

        if ff.get("confirmed"):
            return
        ff["confirmed"] = True

        await message.answer(_ff_key(lang, "ff_generating"))

        try:
            await bot_instance.bot.send_chat_action(
                chat_id=message.chat.id,
                action="upload_photo",
            )
        except Exception:
            pass

        image_prompt = ff["image_prompt"]
        ic = ImageClient()
        main_kb = get_main_keyboard(lang)

        start_time = asyncio.get_event_loop().time()

        try:
            result = await ic.generate_image(image_prompt)
            latency_ms = result["latency_ms"]
            b64 = result.get("b64_json")
            seed = result.get("seed")

            if b64:
                img_bytes = base64.b64decode(b64)
                photo = types.BufferedInputFile(img_bytes, filename="film_frame.png")
                await message.answer_photo(
                    photo,
                    caption=_ff_key(lang, "ff_done"),
                    reply_markup=main_kb,
                )
            elif result.get("url"):
                await message.answer_photo(
                    result["url"],
                    caption=_ff_key(lang, "ff_done"),
                    reply_markup=main_kb,
                )
            else:
                raise RuntimeError("No image data in response")

            record_usage(user_id)
            log_run(
                user_id=user_id,
                description=ff.get("description", ""),
                preview=ff.get("preview", ""),
                image_prompt=image_prompt,
                model=FILM_FRAME_MODEL,
                seed=seed,
                latency_ms=latency_ms,
                status="success",
            )

        except Exception as e:
            error_str = f"{type(e).__name__}: {e}"
            error_code = classify_error(error_str)
            latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)

            logger.error(f"[FILMFRAME] Generation failed for user {user_id}: {error_code} - {error_str}")

            log_run(
                user_id=user_id,
                description=ff.get("description", ""),
                preview=ff.get("preview", ""),
                image_prompt=image_prompt,
                model=FILM_FRAME_MODEL,
                seed=None,
                latency_ms=latency_ms,
                status="error",
                error_code=error_code,
            )

            await message.answer(_ff_key(lang, "ff_error"), reply_markup=main_kb)

        finally:
            # Silent cleanup — do not send "Action cancelled" after shoot/error.
            _finish_ff(bot_instance, user_id)
    finally:
        if pop_queue:
            _pop_message_queue(bot_instance, user_id)


def _pop_message_queue(bot_instance, user_id: int) -> None:
    """Drop one pending message slot so filmframe doesn't trip flood protection."""
    queue = getattr(bot_instance, "message_queue", None)
    if queue and user_id in queue and queue[user_id]:
        queue[user_id].pop(0)


def _finish_ff(bot_instance, user_id: int) -> None:
    """Clear filmframe state and return user to chat without a cancel notice."""
    bot_instance.user_states[user_id] = "chat"
    if hasattr(bot_instance, "filmframe_state") and user_id in bot_instance.filmframe_state:
        del bot_instance.filmframe_state[user_id]


async def _cancel_ff(bot_instance, message: types.Message) -> None:
    """Cancel filmframe flow, restore main keyboard, announce cancellation."""
    from src.telegram_bot import get_main_keyboard

    user_id = message.from_user.id
    lang = bot_instance.user_langs.get(user_id, "ru")

    _finish_ff(bot_instance, user_id)

    lang_label = (
        "Действие отменено. Продолжаем диалог."
        if lang == "ru"
        else "Action cancelled. Back to dialog."
    )
    await message.answer(lang_label, reply_markup=get_main_keyboard(lang))