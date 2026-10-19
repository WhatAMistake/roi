"""Small, versioned Telegram consent gate; not a legal-compliance certification."""

from datetime import datetime, timezone

from i18n import t

CONSENT_VERSION = "2026-10-19-v1"


class ConsentMiddleware:
    """Check consent before routing any command, text, media or callback."""

    def __init__(self, owner):
        self.owner = owner

    async def __call__(self, handler, event, data):
        from aiogram.types import ReplyKeyboardRemove

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
                # Consent is not subject to the ordinary preference-save throttle.
                if self.owner._save_user_prefs(force=True) is not True:
                    self.owner.user_consents.pop(user.id, None)
                    if previous_lang is None:
                        self.owner.user_langs.pop(user.id, None)
                    else:
                        self.owner.user_langs[user.id] = previous_lang
                    await message.answer(t(lang, "consent_save_failed"))
                    return
                from telegram_bot import get_main_keyboard
                self.owner.user_states[user.id] = "chat"
                await message.answer(t(lang, "consent_accepted"), reply_markup=get_main_keyboard(lang))
            try:
                await message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return
        command = ((message.text or "").split(maxsplit=1) or [""])[0].split("@", 1)[0].lower()
        if command == "/privacy":
            from aiogram.types import BufferedInputFile
            await message.answer_document(BufferedInputFile(
                t(lang, "consent_details").encode("utf-8"), filename=f"roi-consent-{lang}.txt"))
            return
        if not self.owner._has_consent(user.id):
            await self.owner._show_consent(message, lang)
            return
        return await handler(event, data)


def has_consent(owner, user_id):
    record = owner.user_consents.get(user_id, {})
    return isinstance(record, dict) and record.get("version") == CONSENT_VERSION and bool(record.get("accepted_at"))


async def show_consent(owner, message, lang):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    welcome = t(lang, "welcome", name=message.from_user.first_name or "")
    # Preserve the introduction; invite the person to talk only after acceptance.
    for ending in ("С чего начнём?", "So, where shall we start?"):
        if welcome.endswith(ending):
            welcome = welcome[:-len(ending)].rstrip()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "consent_yes"), callback_data=f"consent:yes:{CONSENT_VERSION}")],
        [InlineKeyboardButton(text=t(lang, "consent_no"), callback_data=f"consent:no:{CONSENT_VERSION}")],
    ])
    await message.answer(welcome + "\n\n" + t(lang, "consent_notice"), reply_markup=keyboard)
