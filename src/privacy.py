"""Versioned consent gate and per-user revocation."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import secrets
import time

from i18n import CONSENT_VERSION, t

logger = logging.getLogger(__name__)


def _privacy_state(owner):
    if not hasattr(owner, "_privacy_active"):
        owner._privacy_active = {}
    if not hasattr(owner, "_privacy_pending"):
        owner._privacy_pending = {}
    if not hasattr(owner, "_privacy_tokens"):
        owner._privacy_tokens = {}
    if not hasattr(owner, "_privacy_revoking"):
        owner._privacy_revoking = set()
    return owner._privacy_active


@asynccontextmanager
async def user_activity(owner, user_id):
    """Register work before the first await; pending revocations reject new work."""
    active = _privacy_state(owner)
    if user_id in owner._privacy_pending:
        yield False
        return
    task = asyncio.current_task()
    tasks = active.setdefault(user_id, set())
    added = task not in tasks
    tasks.add(task)
    try:
        yield True
    finally:
        if added:
            tasks.discard(task)
            if not tasks:
                active.pop(user_id, None)


def consent_notice(lang):
    return t(lang, "consent_notice")


def consent_details(lang):
    return t(lang, "consent_details")



class ConsentMiddleware:
    """Check consent before routing any command, text, media or callback."""

    def __init__(self, owner):
        self.owner = owner

    async def __call__(self, handler, event, data):
        user = event.from_user
        if not user or user.is_bot:
            return
        callback = hasattr(event, "data")
        message = event.message if callback else event
        lang = self.owner.user_langs.get(
            user.id, "ru" if (user.language_code or "").startswith("ru") else "en")
        if message is None:
            if callback:
                await event.answer()
            return
        if message.chat.type != "private" or message.chat.id != user.id:
            if callback:
                await event.answer(t(lang, "consent_private"), show_alert=True)
            else:
                await message.answer(t(lang, "consent_private"))
            return
        _privacy_state(self.owner)
        if callback and (event.data or "").startswith("privacy:"):
            return await self._revoke_callback(event, lang)
        command_parts = (message.text or "").split(maxsplit=1) if not callback else []
        command = (command_parts[0].split("@", 1)[0].lower() if command_parts else "")
        if command == "/privacy" and len(command_parts) > 1 and command_parts[1].strip().lower() == "revoke":
            return await self._revoke_prompt(message, user.id, lang)
        # The privacy document remains available even while deletion needs retry.
        if command == "/privacy":
            from aiogram.types import BufferedInputFile
            await message.answer_document(BufferedInputFile(
                consent_details(lang).encode("utf-8"), filename=f"roi-consent-{lang}.txt"))
            return
        async with user_activity(self.owner, user.id) as allowed:
            if not allowed:
                if callback:
                    await event.answer(t(lang, "privacy_revoke_busy"), show_alert=True)
                else:
                    await message.answer(t(lang, "privacy_revoke_busy"))
                return
            return await self._route(handler, event, data, message, user, callback, lang)

    async def _route(self, handler, event, data, message, user, callback, lang):
        from aiogram.types import ReplyKeyboardRemove

        if callback and not (event.data or "").startswith("consent:"):
            if not self.owner._has_consent(user.id):
                await event.answer()
                await self.owner._show_consent(message, lang)
                return
            return await handler(event, data)
        if callback:
            await event.answer()
            if (event.data or "") not in (f"consent:yes:{CONSENT_VERSION}", f"consent:no:{CONSENT_VERSION}"):
                await message.answer(t(lang, "consent_stale"))
                return
            if self.owner._has_consent(user.id):
                # Old buttons must not reverse a later decision or repeat onboarding.
                await message.answer(t(lang, "consent_already"))
                return
            if event.data.startswith("consent:no:"):
                await message.answer(t(lang, "consent_declined"), reply_markup=ReplyKeyboardRemove())
            else:
                record = {"version": CONSENT_VERSION, "accepted_at": datetime.now(timezone.utc).isoformat()}
                self.owner.user_consents[user.id] = record
                previous_lang = self.owner.user_langs.get(user.id)
                self.owner.user_langs[user.id] = lang
                # The first /start is intercepted by consent, so start its
                # daily interval here, in the same durable write as consent.
                meaning_maps = (
                    self.owner.user_meaning_enabled,
                    self.owner.user_meaning_history,
                    self.owner.user_meaning_last_time,
                )
                missing = object()
                previous_meaning = [values.get(user.id, missing) for values in meaning_maps]
                self.owner._restart_daily_meaning_timer(user.id)
                # Consent is not subject to the ordinary preference-save throttle.
                if self.owner._save_user_prefs(force=True) is not True:
                    self.owner.user_consents.pop(user.id, None)
                    if previous_lang is None:
                        self.owner.user_langs.pop(user.id, None)
                    else:
                        self.owner.user_langs[user.id] = previous_lang
                    for values, previous in zip(meaning_maps, previous_meaning):
                        if previous is missing:
                            values.pop(user.id, None)
                        else:
                            values[user.id] = previous
                    await message.answer(t(lang, "consent_save_failed"))
                    return
                from telegram_bot import get_main_keyboard
                self.owner.user_states[user.id] = "chat"
                welcome = t(lang, "welcome", name=user.first_name or "")
                await message.answer(t(lang, "consent_accepted") + "\n\n" + welcome,
                                     reply_markup=get_main_keyboard(lang))
            try:
                await message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return
        if not self.owner._has_consent(user.id):
            await self.owner._show_consent(message, lang)
            return
        return await handler(event, data)

    async def _revoke_prompt(self, message, user_id, lang):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        if user_id in self.owner._privacy_revoking:
            await message.answer(t(lang, "privacy_revoke_busy"))
            return
        now = time.monotonic()
        tokens = self.owner._privacy_tokens
        for token, (uid, created) in list(tokens.items()):
            if uid == user_id or now - created > 900:
                tokens.pop(token, None)
        token = secrets.token_urlsafe(12)
        tokens[token] = (user_id, now)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "privacy_revoke_yes"), callback_data=f"privacy:yes:{token}")],
            [InlineKeyboardButton(text=t(lang, "privacy_revoke_no"), callback_data=f"privacy:no:{token}")],
        ])
        await message.answer(t(lang, "privacy_revoke_prompt"), reply_markup=keyboard)

    async def _revoke_callback(self, event, lang):
        parts = (event.data or "").split(":")
        tokens = self.owner._privacy_tokens
        entry = tokens.get(parts[2]) if len(parts) == 3 else None
        if (len(parts) != 3 or parts[1] not in ("yes", "no") or not entry
                or entry[0] != event.from_user.id or time.monotonic() - entry[1] > 900):
            await event.answer(t(lang, "privacy_revoke_stale"), show_alert=True)
            return
        tokens.pop(parts[2], None)  # Consume before awaiting: callbacks are one-time.
        if parts[1] == "no":
            await event.answer()
            await event.message.answer(t(lang, "privacy_revoke_cancelled"))
            try:
                await event.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return
        user_id = event.from_user.id
        if user_id in self.owner._privacy_revoking:
            await event.answer(t(lang, "privacy_revoke_busy"), show_alert=True)
            return
        self.owner._privacy_revoking.add(user_id)
        self.owner._privacy_pending[user_id] = True
        self.owner.user_consents.pop(user_id, None)
        for token, (uid, _) in list(tokens.items()):
            if uid == user_id:
                tokens.pop(token, None)
        # Persist the pending marker before waiting; a restart must not regrant consent.
        try:
            persisted = self.owner._save_user_prefs(force=True) is True
        except Exception:
            persisted = False
        task = asyncio.create_task(self._finish_revocation(event.message, user_id, lang, persisted))
        await event.answer()
        await asyncio.shield(task)

    async def _finish_revocation(self, message, user_id, lang, persisted):
        from aiogram.types import ReplyKeyboardRemove
        try:
            if not persisted:
                raise RuntimeError("Could not persist pending revocation")
            tasks = list(self.owner._privacy_active.get(user_id, ()))
            if tasks:
                # Do not cancel executor-backed work: Python cannot stop its thread.
                # Waiting prevents late writes and guarantees temp-audio cleanup.
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.owner._delete_user_data(user_id)
            self.owner._privacy_pending.pop(user_id, None)
            if self.owner._save_user_prefs(force=True) is not True:
                self.owner._privacy_pending[user_id] = True
                raise RuntimeError("Could not persist completed revocation")
        except Exception as exc:
            self.owner._privacy_pending[user_id] = True
            logger.warning("Privacy revocation incomplete: type=%s", type(exc).__name__)
            await message.answer(t(lang, "privacy_revoke_failed"), reply_markup=ReplyKeyboardRemove())
        else:
            try:
                await message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await message.answer(t(lang, "privacy_revoke_done"), reply_markup=ReplyKeyboardRemove())
        finally:
            self.owner._privacy_revoking.discard(user_id)


def has_consent(owner, user_id):
    if user_id in getattr(owner, "_privacy_pending", {}):
        return False
    record = owner.user_consents.get(user_id, {})
    return isinstance(record, dict) and record.get("version") == CONSENT_VERSION and bool(record.get("accepted_at"))


async def show_consent(owner, message, lang):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    intro = t(lang, "consent_intro", name=message.from_user.first_name or "")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "consent_yes"), callback_data=f"consent:yes:{CONSENT_VERSION}")],
        [InlineKeyboardButton(text=t(lang, "consent_no"), callback_data=f"consent:no:{CONSENT_VERSION}")],
    ])
    await message.answer(intro + "\n\n" + consent_notice(lang), reply_markup=keyboard)
