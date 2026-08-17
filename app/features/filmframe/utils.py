"""Utility functions for filmframe that don't depend on aiogram."""

# Confirm-like keywords (Russian + English)
_CONFIRM_WORDS = {"да", "делай", "го", "вот так", "снимай", "заснять", "shoot",
                  "yes", "do it", "go", "generate", "ok", "okay", "хорошо",
                  "погнали", "поехали", "вперёд", "давай", "ага", "угу"}


def get_localized(key: str, lang: str) -> str:
    """Minimal local i18n helper for filmframe strings."""
    strings = {
        "ru": {
            "ff_describe_prompt": "Опиши своё текущее состояние — что ты чувствуешь, где ты находишься, о чём думаешь. Я превращу это в кадр на плёнке.",
            "ff_describe_prompt_short": "Опиши состояние.",
            "ff_building_scene": "Ловлю кадр...",
            "ff_scene_ready": "Вот какой кадр получится:\n\n{preview}\n\nСтреляем?",
            "ff_button_shoot": "Заснять",
            "ff_button_edit": "Изменить",
            "ff_button_cancel": "Отмена",
            "ff_edit_prompt": "Что изменить в кадре?",
            "ff_generating": "Стреляю... (проявка плёнки займёт пару минут)",
            "ff_done": "Кадр готов.",
            "ff_error": "Плёнка засветилась. Не в этот раз.",
            "ff_limit_user": "Плёнка закончилась. Напиши создателю через /feedback, если хочется больше кадров.",
            "ff_limit_global": "Плёнка закончилась. Напиши создателю через /feedback, если хочется больше кадров.",
            "ff_disabled": "Камера в ремонте.",
            "ff_not_allowed": "Фотокамера в ремонте.",
        },
        "en": {
            "ff_describe_prompt": "Describe your current state — what you feel, where you are, what you're thinking about. I'll turn it into a frame on film.",
            "ff_describe_prompt_short": "Describe your state.",
            "ff_building_scene": "Framing the shot...",
            "ff_scene_ready": "Here's the frame I see:\n\n{preview}\n\nShoot?",
            "ff_button_shoot": "Shoot",
            "ff_button_edit": "Edit",
            "ff_button_cancel": "Cancel",
            "ff_edit_prompt": "What should we change?",
            "ff_generating": "Shooting... (film development takes a minute or two)",
            "ff_done": "Frame captured.",
            "ff_error": "The film got overexposed. Not this time.",
            "ff_limit_user": "We've run out of film. Though, you can get in touch with the dev via /feedback.",
            "ff_limit_global": "We've run out of film. Though, you can get in touch with the dev via /feedback.",
            "ff_disabled": "The camera is being repaired.",
            "ff_not_allowed": "The photocamera is being repaired.",
        },
    }
    locale = strings.get(lang, strings["en"])
    return locale.get(key, strings["en"].get(key, key))


def _ff_key(lang: str, key: str, **kwargs) -> str:
    text = get_localized(key, lang)
    if kwargs:
        text = text.format(**kwargs)
    return text


def classify_error(error_str: str) -> str:
    """Classify error string into a short code."""
    s = error_str.lower()
    if "timeout" in s:
        return "timeout"
    if "429" in s or "rate" in s:
        return "rate_limit"
    if "content" in s or "filter" in s or "safety" in s or "policy" in s:
        return "content_filter"
    if "5" in s[:3] and ("server" in s or "502" in s or "503" in s or "504" in s):
        return "server_error"
    if "malformed" in s or "json" in s or "decode" in s:
        return "malformed_response"
    if "401" in s or "403" in s or "auth" in s:
        return "auth_error"
    return "unknown"


def is_filmframe_state(state: str) -> bool:
    """Check if a user_states value is a filmframe flow state."""
    return state in ("ff_describe", "ff_preview", "ff_edit") or \
           (isinstance(state, str) and state.startswith("ff_"))