"""
Telegram бот для экзистенциальной терапии.
"""

import os
import asyncio
import html
import logging
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.security import get_encryption_key

# Encryption for sensitive user data
try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    print("cryptography не установлен. Установите: pip install cryptography")

def _is_encryption_key_configured() -> bool:
    """Return True if USER_PREFS_ENCRYPTION_KEY is present in environment."""
    raw = os.getenv("USER_PREFS_ENCRYPTION_KEY")
    if not raw:
        return False
    return bool(raw.strip().strip('"').strip("'").strip())


def encrypt_data(data: str, key: Optional[bytes]) -> str:
    """Encrypt data with Fernet. Raises on missing/invalid key or encryption failure."""
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography package is not available; refusing to store unencrypted data")
    if not key:
        raise ValueError("Encryption key is missing or invalid; refusing to store unencrypted data")
    try:
        f = Fernet(key)
        encrypted = f.encrypt(data.encode('utf-8'))
        return encrypted.decode('utf-8')
    except Exception as e:
        print(f"[ENCRYPTION] Encryption failed: {type(e).__name__}")
        raise RuntimeError(f"Encryption failed: {type(e).__name__}") from e


def decrypt_data(data: str, key: Optional[bytes]) -> str:
    """Decrypt Fernet-encrypted data, or return legacy unencrypted JSON for migration."""
    stripped = (data or "").strip()
    if not stripped:
        return data
    if stripped.startswith('gAAAA'):
        if not CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography package is required to decrypt user preferences")
        if not key:
            raise ValueError("Encryption key is required to decrypt user preferences")
        try:
            f = Fernet(key)
            decrypted = f.decrypt(stripped.encode('utf-8'))
            return decrypted.decode('utf-8')
        except Exception as e:
            print(f"[ENCRYPTION] Decryption failed: {type(e).__name__}")
            raise ValueError("Failed to decrypt user preferences") from e
    # Legacy unencrypted JSON payload: allow reading for non-destructive migration
    return data



try:
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.filters import Command
    from aiogram.exceptions import (
        TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter, TelegramServerError,
    )
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    from aiogram.utils.chat_action import ChatActionSender
    from aiogram.utils.keyboard import ReplyKeyboardBuilder
    AIogram_AVAILABLE = True
except ImportError:
    AIogram_AVAILABLE = False
    print("aiogram не установлен. Установите: pip install aiogram")
    # Define dummy types to avoid NameError on import/annotations
    class _DummyAiogramTypes:
        Message = object

    class _DummyF:
        text = voice = photo = sticker = None

    class _DummyKeyboardButton:
        def __init__(self, text: str = ""):
            self.text = text

    class _DummyMarkup:
        def __init__(self, buttons=None, resize_keyboard: bool = True):
            self.buttons = buttons or []
            self.resize_keyboard = resize_keyboard

    class _DummyReplyKeyboardBuilder:
        def __init__(self):
            self._buttons = []

        def add(self, *buttons):
            self._buttons.extend(buttons)
            return self

        def adjust(self, *args, **kwargs):
            return self

        def as_markup(self, resize_keyboard: bool = True):
            return _DummyMarkup(self._buttons, resize_keyboard=resize_keyboard)

    types = _DummyAiogramTypes()
    F = _DummyF()
    Bot = Dispatcher = Command = ChatActionSender = None
    class _DummyTelegramError(Exception):
        pass
    TelegramBadRequest = TelegramNetworkError = TelegramRetryAfter = TelegramServerError = _DummyTelegramError
    ReplyKeyboardMarkup = _DummyMarkup
    KeyboardButton = _DummyKeyboardButton
    ReplyKeyboardBuilder = _DummyReplyKeyboardBuilder

from therapist_bot import ExistentialTherapistBot, Message, VoiceResponseTooLong, voice_response_fits
from lang_utils import detect_language
from i18n import t
from privacy import ConsentMiddleware, has_consent, show_consent
from code_reviewer import UpdateCache, _is_empty_changelog, _looks_technical
from app.features.music import Music


DEFAULT_LLM_MODEL = "deepseek-v4.1-flash"
logger = logging.getLogger(__name__)
_MARKUP_TOKEN = re.compile(
    r"<[A-Za-z/!][^<>]*>|&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);"
)

_UPDATE_SUMMARY_TECH = re.compile(
    r"(?i)\b(?:переменн\w*|услови\w*|констант\w*|хеш\w*|хэш\w*|"
    r"рефактор\w*|перемещен\w*|переименован\w*|if|elif|else)\b|"
    r"\b\w+_\w+\b|(?:^|\s)(?:src|app|prompts)/"
)


def _valid_update_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 3500:
        return False
    empty_values = {"нет", "none", "null", "n/a", "no", "нет изменений",
                    "нет пользовательских изменений", "no user-facing changes", "no changes"}
    lines = [re.sub(r"^(?:[-*•]|—|\d+[.)])\s*", "", line.strip())
             .casefold().strip(" \t.!…")
             for line in text.splitlines() if line.strip()]
    if not any(line and line not in empty_values and any(char.isalnum() for char in line)
               for line in lines):
        return False
    return not any(0xD800 <= ord(char) <= 0xDFFF for char in text)


def _update_review_state(pending, notes):
    """Check both persisted analysis and the exact bilingual draft being shown."""
    if pending is not None:
        if not isinstance(pending, dict) or pending.get("status") != "review":
            return "incomplete"
        analysis = pending.get("analysis")
        if not isinstance(analysis, dict):
            return "incomplete"
        count, fragments = analysis.get("fragment_count"), analysis.get("fragments")
        if (type(count) is not int or count < 0 or not isinstance(fragments, dict)
                or len(fragments) < count
                or (pending.get("paths") and count == 0
                    and (not isinstance(analysis.get("unchanged"), list)
                         or not all(isinstance(path, str) for path in analysis["unchanged"])
                         or sorted(analysis["unchanged"]) != sorted(pending["paths"])))
                or not all(isinstance(value, list) for value in fragments.values())
                or not isinstance(analysis.get("findings"), list)
                or not isinstance(analysis.get("missing"), list)
                or not isinstance(analysis.get("failures"), list)
                or analysis["missing"] or analysis["failures"]
                or str(pending.get("reason", "")).startswith("Не удалось")):
            return "incomplete"
        if str(pending.get("reason", "")).startswith("Модель не нашла пользовательских изменений"):
            return "no_changes"
        if pending.get("notes") != notes:
            return "invalid"
    if not isinstance(notes, dict) or not all(
        _valid_update_text(notes.get(lang)) for lang in ("ru", "en")
    ):
        return "invalid"
    return "review"


def _update_review_notice(state, pending=None):
    pending = pending if isinstance(pending, dict) else {}
    draft_id = pending.get("id", "")
    skip = ""
    if (pending.get("status") == "review" and (state == "no_changes" or (
        state == "incomplete" and isinstance(pending.get("analysis"), dict)
        and pending["analysis"].get("missing")
    ))) and isinstance(draft_id, str) and re.fullmatch(r"[0-9a-f]{32}", draft_id):
        skip = (
            "\nЧтобы явно пропустить этот черновик без рассылки и перейти к новым изменениям: "
            f"/updates skip {draft_id}"
        )
    if state == "no_changes":
        return "ℹ️ Пользовательских изменений не найдено. Рассылка не требуется; черновик сохранён." + skip
    if state == "invalid":
        return "⚠️ Тексты RU и EN отсутствуют или некорректны. Рассылка недоступна; черновик сохранён."
    if skip:
        return ("⚠️ Анализ обновления не завершён: не удалось восстановить предыдущие снимки "
                "или сравнить некоторые файлы. Черновик сохранён." + skip)
    return "⚠️ Анализ обновления не завершён. Черновик сохранён; повторите /updates."


def _update_summary(pending, notes):
    findings = (pending or {}).get("analysis", {}).get("findings", [])
    descriptions = [item.get("description", "") for item in findings
                    if isinstance(item, dict) and item.get("category") in {"feature", "fix", "change"}]
    result = []
    for candidates in (descriptions, (notes.get("ru") or "").splitlines()):
        for description in candidates:
            if not isinstance(description, str):
                continue
            text = re.sub(r"\s+", " ", description).strip()
            text = re.sub(r"^(?:[-*•]|—|\d+[.)])\s+", "", text)
            if (not text or _is_empty_changelog(text) or _looks_technical(text)
                    or _UPDATE_SUMMARY_TECH.search(text)):
                continue
            # At most five escaped lines must fit together in one Telegram message.
            text = text if len(text) <= 100 else text[:99].rstrip() + "…"
            if text.casefold() not in {entry.casefold() for entry in result}:
                result.append(text)
            if len(result) == 5:
                break
        if result:
            break
    return result


def _update_text_chunks(text, limit=3000):
    """Escape before splitting, without cutting HTML entities or surrogate pairs."""
    chunks, current, size, units = [], [], 0, 0
    for char in text:
        escaped = html.escape(char)
        char_units = 2 if ord(char) > 0xFFFF else 1
        if current and (size + len(escaped) > limit or units + char_units > limit):
            chunks.append("".join(current))
            current, size, units = [], 0, 0
        current.append(escaped)
        size += len(escaped)
        units += char_units
    if current:
        chunks.append("".join(current))
    return chunks


def _update_preview_parts(pending, notes):
    summary = _update_summary(pending, notes)
    lines = ["📋 <b>Предпросмотр обновления</b>",
             f"Изменено файлов: <b>{len((pending or {}).get('paths', []))}</b>",
             "", "<b>Краткая сводка</b>"]
    lines.extend("• " + html.escape(text) for text in summary)
    if not summary:
        lines.append("См. готовые тексты ниже.")
    parts = ["\n".join(lines)]
    for lang in ("ru", "en"):
        for index, chunk in enumerate(_update_text_chunks(notes[lang])):
            title = lang.upper() + (" — продолжение" if index else "")
            parts.append(f"<b>Готовый текст {title}</b>\n{chunk}")
    parts.append("Отправить?\n• <b>да</b> — разослать как есть\n"
                 "• <b>править</b> — отредактировать RU, затем EN\n"
                 "• <b>нет</b> — пропустить это обновление")
    return parts


class DeliveryError(RuntimeError):
    """A response was not fully confirmed by Telegram."""

    def __init__(self, delivered: str = ""):
        super().__init__("Telegram response delivery failed")
        self.delivered = delivered


@dataclass
class _LoadingAnimationState:
    rate_limited: bool = False
    retry_deadline: float = 0.0


class _SafeChatActionBot:
    """Action failures must not prevent delivery of an already generated voice."""

    def __init__(self, bot):
        self.bot = bot
        self.id = bot.id

    async def send_chat_action(self, **kwargs):
        try:
            return await self.bot.send_chat_action(**kwargs)
        except Exception as exc:
            logger.warning("Telegram voice action failed: type=%s", type(exc).__name__)


def _parse_admin_id(raw_value: Optional[str]) -> int:
    """Safely parse ADMIN_ID from environment without raising ValueError on placeholders."""
    if raw_value is None:
        return 0
    cleaned = str(raw_value).strip()
    if not cleaned:
        return 0
    try:
        return int(cleaned)
    except ValueError:
        print(f"[CONFIG] Invalid ADMIN_ID={cleaned!r}; admin commands disabled.")
        return 0


def strip_markdown(text: str) -> str:
    """Remove markdown formatting from text."""
    if not text:
        return text
    # Remove bold **text** or __text__
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    # Remove italic *text* or _text_
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    # Remove headings # ## ###
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    # Remove list markers - or * at start of line
    text = re.sub(r'^[\-\*]\s+', '', text, flags=re.MULTILINE)
    # Remove numbered list markers 1. 2. etc
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    # Remove inline code `code`
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Remove links [text](url)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Remove blockquotes >
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    # Remove thinking process
    text = re.sub(r'Анализирую\.\.\..*?Экзистенциальный отклик:', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove any remaining <think> tags if they exist
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove any remaining "Анализирую..." if it wasn't followed by "Экзистенциальный отклик:"
    text = re.sub(r'Анализирую\.\.\.', '', text, flags=re.IGNORECASE)
    return text.strip()

# Default language when nothing is detected or user's Telegram locale is unsupported
DEFAULT_LANG = os.getenv("DEFAULT_LANG", "en")



# Клавиатуры
def get_main_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Main keyboard localized by `lang`."""
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=t(lang, "button_assoc")))
    builder.add(KeyboardButton(text=t(lang, "button_analyze")))
    builder.add(KeyboardButton(text=t(lang, "button_help")))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_cancel_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Keyboard with a cancel button localized by `lang`."""
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=t(lang, "button_cancel")))
    return builder.as_markup(resize_keyboard=True)


def get_update_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Admin keyboard for update changelog confirmation."""
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="✅ Да"))
    builder.add(KeyboardButton(text="✏️ Править"))
    builder.add(KeyboardButton(text="❌ Нет"))
    builder.adjust(3)
    return builder.as_markup(resize_keyboard=True)


_UPDATE_CONFIRM_YES = {"да", "yes", "д", "y", "✅ да", "✅"}
_UPDATE_CONFIRM_EDIT = {
    "edit", "править", "изменить", "редакт", "редактирование",
    "✏️ править", "✏️", "правка",
}
_UPDATE_CONFIRM_NO = {
    "нет", "no", "н", "n", "cancel", "отмена", "❌ нет", "❌",
}


class TelegramTherapistBot:
    """Telegram бот-терапевт."""
    
    def __init__(
        self,
        telegram_token: str,
        llm_model: Optional[str] = None,
        llm_analysis_model: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_api_base: Optional[str] = None,
        use_rag: bool = True,
        ask_question_prob: Optional[float] = None
    ):

        if not AIogram_AVAILABLE:
            raise RuntimeError("aiogram не установлен")
        
        self.telegram_token = telegram_token
        self._explicit_llm_model = llm_model
        self._explicit_llm_analysis_model = llm_analysis_model
        self.llm_model = llm_model or os.getenv("OPENAI_MODEL") or DEFAULT_LLM_MODEL
        self.llm_analysis_model = (
            llm_analysis_model
            or os.getenv("OPENAI_ANALYSIS_MODEL")
            or self.llm_model
        )
        self._init_llm_model = self.llm_model
        self._init_llm_analysis_model = self.llm_analysis_model
        
        # Инициализируем Telegram бота
        self.bot = Bot(token=telegram_token)
        self.dp = Dispatcher()
        
        # Filmframe state
        self.filmframe_state: dict[int, dict] = {}

        # Хранилище сессий (user_id -> therapist_bot)
        self.sessions: dict[int, ExistentialTherapistBot] = {}
        self.user_locks: dict[int, asyncio.Lock] = {}
        self.user_reset_epochs: dict[int, int] = {}
        
        # Состояния пользователей
        self.user_states: dict[int, str] = {}  # user_id -> state
        # Пользовательские языки (user_id -> 'ru'|'en')
        self.user_langs: dict[int, str] = {}
        
        # Временное хранилище ассоциаций
        self.temp_associations: dict[int, dict] = {}
        
        # Параметры LLM
        self.llm_api_key = llm_api_key
        self.llm_api_base = llm_api_base
        self.use_rag = use_rag
        # probability to ask a clarifying question per-response (defaults to env or 0.37)
        try:
            self.ask_question_prob = ask_question_prob if ask_question_prob is not None else float(os.getenv("OPENAI_ASK_QUESTION_PROB", 0.73))
        except Exception:
            self.ask_question_prob = 0.73
        
        # ID администратора для фидбека (безопасный парсинг без хардкода чужого ID и без падения на строке)
        self.admin_id = _parse_admin_id(os.getenv("ADMIN_ID"))
        self.update_cache = UpdateCache(Path(__file__).parent.parent)
        self.update_check_lock = asyncio.Lock()
        self.update_broadcast_lock = asyncio.Lock()

        # Регистрируем хендлеры
        
        # Initialize ALL dictionaries BEFORE loading prefs to ensure they exist
        # even if loading fails
        self.prefs_path = Path(__file__).parent.parent / "data" / "user_prefs.json"
        self.user_ask_prob: dict[int, float] = {}
        
        # Track last update notification time
        self.last_update_notification: dict[int, float] = {}

        # Message queue tracking for flood protection (>5 messages without response)
        self.message_queue: dict[int, list[float]] = {}  # user_id -> list of message timestamps
        self.user_consecutive_messages: dict[int, int] = {}  # user_id -> count of messages since last bot response

        # Хранилище для активных минут тишины (user_id -> end_timestamp)
        self.silence_until: dict[int, float] = {}

        # Daily meaning tracking - MUST be initialized before _load_user_prefs
        self.user_meaning_enabled: dict[int, bool] = {}
        self.user_meaning_history: dict[int, list[str]] = {}
        self.user_meaning_last_time: dict[int, datetime] = {}
        self.user_meaning_count: dict[int, int] = {}

        # User activity tracking for /stats - MUST be initialized before _load_user_prefs
        self.user_first_seen: dict[int, datetime] = {}
        self.user_last_active: dict[int, datetime] = {}
        self.blocked_users: dict[int, datetime] = {}  # user_id -> timestamp when blocked

        # User summary for internal admin use (every 16 messages) - MUST be initialized before _load_user_prefs
        self.user_summaries: dict[int, str] = {}  # user_id -> summary text
        self.user_message_counts: dict[int, int] = {}  # user_id -> message count since last summary
        self.user_usernames: dict[int, str] = {}  # user_id -> username for admin lookup
        # Store last 16 messages for each user (for recovery purposes)
        self.user_recent_messages: dict[int, list[dict]] = {}  # user_id -> list of {role, content, timestamp}

        # NOW load persisted preferences (after all dicts are initialized)
        self.user_consents: dict[int, dict] = {}
        self._prefs_loaded = False
        self._load_user_prefs()

        self.music = Music(self, key_provider=lambda: get_encryption_key(), default_lang=lambda: DEFAULT_LANG)

        # Время запуска бота для фильтрации старых сообщений
        import time
        self.start_time = time.time()
        self.processed_flood_users = set()

        middleware = ConsentMiddleware(self)
        self.dp.message.outer_middleware(middleware)
        self.dp.callback_query.outer_middleware(middleware)
        self._register_handlers()

    
    def _has_consent(self, user_id: int) -> bool:
        return has_consent(self, user_id)

    async def _show_consent(self, message, lang):
        await show_consent(self, message, lang)

    def _get_therapist(self, user_id: int) -> ExistentialTherapistBot:
        """Получить или создать сессию терапевта для пользователя."""
        if user_id not in self.sessions:
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            # determine per-user ask probability (override if user set)
            per_user_prob = self.user_ask_prob.get(user_id, self.ask_question_prob)
            try:
                # try with ask_question_prob param (newer versions)
                self.sessions[user_id] = ExistentialTherapistBot(
                    model=self.llm_model,
                    analysis_model=self.llm_analysis_model,
                    api_key=self.llm_api_key,
                    api_base=self.llm_api_base,
                    use_rag=self.use_rag,
                    language=lang,
                    **({"ask_question_prob": per_user_prob} if per_user_prob is not None else {})
                )
            except TypeError:
                # fallback for older ExistentialTherapistBot signature
                self.sessions[user_id] = ExistentialTherapistBot(
                    model=self.llm_model,
                    api_key=self.llm_api_key,
                    api_base=self.llm_api_base,
                    use_rag=self.use_rag,
                    language=lang,
                )
            self.sessions[user_id].history = [
                Message(role=item["role"], content=item["content"])
                for item in getattr(self, "user_recent_messages", {}).get(user_id, [])
                if item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str)
            ]
        return self.sessions[user_id]

    def _get_user_lock(self, user_id: int) -> asyncio.Lock:
        if not hasattr(self, "user_locks"):
            self.user_locks = {}
        return self.user_locks.setdefault(user_id, asyncio.Lock())

    async def _warn_prefs_not_saved(self, message, lang):
        await message.answer(
            "The change applies for now, but could not be saved and may be lost after restart."
            if lang == "en" else
            "Изменение действует сейчас, но его не удалось сохранить; после перезапуска оно может пропасть."
        )

    def _load_user_prefs(self):
        """Load user preferences from file."""
        import json
        from datetime import datetime
        
        # Log encryption status
        encryption_key = get_encryption_key()
        if CRYPTO_AVAILABLE and encryption_key:
            print("[LOAD PREFS] Encryption: ENABLED (key is set)")
        elif CRYPTO_AVAILABLE and not encryption_key:
            print("[LOAD PREFS] Encryption key unavailable; saving is disabled")
        else:
            print("[LOAD PREFS] Encryption: UNAVAILABLE - install cryptography package")
        
        # Check if file exists and has content
        if not self.prefs_path.exists():
            print(f"[LOAD PREFS] File not found: {self.prefs_path}")
            self._prefs_loaded = True  # Fresh start, allow saving
            return
        
        try:
            with open(self.prefs_path, 'r', encoding='utf-8') as f:
                raw_content = f.read()
            
            if not raw_content.strip():
                print("[LOAD PREFS] File is empty")
                self._prefs_loaded = True  # Fresh start, allow saving
                return
            
            # Try to decrypt if encryption is available
            decrypted_content = decrypt_data(raw_content, encryption_key)
            
            data = json.loads(decrypted_content)
            self._loaded_prefs_data = data
            self.user_consents = {int(k): v for k, v in data.get("user_consents", {}).items()}

            
            # Log what we found in the file
            print(f"[LOAD PREFS] File contains: {list(data.keys())}")
            for key in data:
                if isinstance(data[key], dict):
                    print(f"[LOAD PREFS]   {key}: {len(data[key])} items")
            
            # Load data - only update if key exists in file (don't overwrite with empty)
            if 'user_langs' in data:
                self.user_langs = {int(k): v for k, v in data['user_langs'].items()}
            if 'user_ask_prob' in data:
                self.user_ask_prob = {int(k): float(v) for k, v in data['user_ask_prob'].items()}
            if 'user_meaning_enabled' in data:
                self.user_meaning_enabled = {int(k): v for k, v in data['user_meaning_enabled'].items()}
            if 'user_meaning_history' in data:
                self.user_meaning_history = {int(k): v for k, v in data['user_meaning_history'].items()}
            if 'user_meaning_last_time' in data:
                self.user_meaning_last_time = {
                    int(k): datetime.fromisoformat(v) 
                    for k, v in data['user_meaning_last_time'].items()
                }
            if 'user_meaning_count' in data:
                self.user_meaning_count = {int(k): v for k, v in data['user_meaning_count'].items()}
            if 'user_first_seen' in data:
                self.user_first_seen = {
                    int(k): datetime.fromisoformat(v)
                    for k, v in data['user_first_seen'].items()
                }
            if 'user_last_active' in data:
                self.user_last_active = {
                    int(k): datetime.fromisoformat(v)
                    for k, v in data['user_last_active'].items()
                }
            if 'user_summaries' in data:
                self.user_summaries = {int(k): v for k, v in data['user_summaries'].items()}
            if 'user_message_counts' in data:
                self.user_message_counts = {int(k): v for k, v in data['user_message_counts'].items()}
            if 'user_usernames' in data:
                self.user_usernames = {int(k): v for k, v in data['user_usernames'].items()}
            if 'user_recent_messages' in data:
                self.user_recent_messages = {
                    int(k): v for k, v in data['user_recent_messages'].items()
                }
            if 'blocked_users' in data:
                self.blocked_users = {
                    int(k): datetime.fromisoformat(v)
                    for k, v in data['blocked_users'].items()
                }
            if 'user_consecutive_messages' in data:
                self.user_consecutive_messages = {
                    int(k): v for k, v in data['user_consecutive_messages'].items()
                }
            
            loaded_count = len(self.user_langs)
            print(f"[LOAD PREFS] Successfully loaded {loaded_count} users")
            print(f"[LOAD PREFS]   meaning_enabled: {len(self.user_meaning_enabled)} users")
            print(f"[LOAD PREFS]   last_active: {len(self.user_last_active)} timestamps")
            print(f"[LOAD PREFS]   meaning_count: {len(self.user_meaning_count)} counters")
            
            # Mark as loaded successfully
            self._prefs_loaded = True
            
        except Exception as e:
            print(f"[LOAD PREFS] ERROR loading preferences: {e}")
            import traceback
            traceback.print_exc()
            print(f"[LOAD PREFS] Keeping existing in-memory data (not resetting to empty)")
            # DO NOT reset any data here - keep whatever was loaded before or initialized
            # Only disable saving if we failed to load
            self._prefs_loaded = False





    def _save_user_prefs(self, force: bool = False):
        # Prevent saving if prefs weren't loaded successfully (avoids overwriting file with empty data)
        if not getattr(self, '_prefs_loaded', False):
            print("[SAVE PREFS] WARNING: Skipping save because prefs failed to load. Fix the load error to enable saving.")
            return False
        
        # Optimization: Throttle saving to once every 60 seconds unless forced
        import time
        now = time.time()
        last_save = getattr(self, '_last_save_time', 0)
        if not force and (now - last_save < 60):
            return
        
        try:
            import json

            encryption_key = get_encryption_key()
            
            payload = {
                **getattr(self, "_loaded_prefs_data", {}),
                'user_consents': {str(k): v for k, v in self.user_consents.items()},

                'user_langs': {str(k): v for k, v in self.user_langs.items()},
                'user_ask_prob': {str(k): v for k, v in self.user_ask_prob.items()},
                'user_meaning_enabled': {str(k): v for k, v in self.user_meaning_enabled.items()},
                'user_meaning_history': {str(k): v for k, v in self.user_meaning_history.items()},
                'user_meaning_last_time': {
                    str(k): v.isoformat() for k, v in self.user_meaning_last_time.items()
                },
                'user_meaning_count': {str(k): v for k, v in self.user_meaning_count.items()},
                # Persist user activity tracking data
                'user_first_seen': {
                    str(k): v.isoformat() for k, v in self.user_first_seen.items()
                },
                'user_last_active': {
                    str(k): v.isoformat() for k, v in self.user_last_active.items()
                },
                # Persist internal admin summaries
                'user_summaries': {str(k): v for k, v in self.user_summaries.items()},
                'user_message_counts': {str(k): v for k, v in self.user_message_counts.items()},
                'user_usernames': {str(k): v for k, v in self.user_usernames.items()},
                # Persist recent messages (last 16)
                'user_recent_messages': {str(k): v for k, v in self.user_recent_messages.items()},
                'blocked_users': {str(k): v.isoformat() for k, v in self.blocked_users.items()},
            # Persist consecutive message counter for flood protection
                'user_consecutive_messages': {str(k): v for k, v in self.user_consecutive_messages.items()},
            }
            json_content = json.dumps(payload, ensure_ascii=False, indent=2)
            
            # Encrypt if needed
            encrypted_content = encrypt_data(json_content, encryption_key)
            
            # Write to a temporary file first to avoid corruption
            self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.prefs_path.with_suffix('.json.tmp')
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(encrypted_content)
            
            # Atomic rename
            temp_path.replace(self.prefs_path)
            self._last_save_time = now
            self._loaded_prefs_data = payload
            
            print(f"[SAVE PREFS] Saved successfully to {self.prefs_path}")
            return True
        except Exception as e:
            print(f"[SAVE PREFS] Error saving prefs: {e}")
            import traceback
            traceback.print_exc()
            return False




    
    def _register_handlers(self):
        """Регистрация обработчиков."""
        
        @self.dp.callback_query(F.data.startswith("consent:"))
        async def consent_decision(callback: types.CallbackQuery):
            # ConsentMiddleware handles the decision before routing.
            pass

        @self.dp.message(Command("start"))
        async def cmd_start(message: types.Message):
            await self._handle_start(message)
        
        @self.dp.message(Command("help"))
        async def cmd_help(message: types.Message):
            await self._handle_help(message)

        @self.dp.message(Command("music"))
        async def cmd_music(message: types.Message):
            await self.music.command(message)

        @self.dp.callback_query(F.data.startswith("music:"))
        async def music_callback(callback: types.CallbackQuery):
            await self.music.callback(callback)

        @self.dp.message(Command("updates"))
        async def cmd_updates(message: types.Message):
            if message.from_user.id != self.admin_id or not self.admin_id:
                return
            args = (message.text or "").split()
            if len(args) > 1:
                await self._skip_unanalysable_update(message, args[1:])
                return
            result = await self._check_and_notify_updates(delay=False, retry_generation=True)
            if result == "idle":
                await message.answer("Нет ожидающих обновлений или изменений.")
            elif result == "error":
                await message.answer("Не удалось проверить обновления. Подробности в журнале сервиса.")

        @self.dp.message(Command("askprob"))
        async def cmd_askprob(message: types.Message):
            # /askprob 0.1  or /askprob reset
            user_id = message.from_user.id
            parts = (message.text or "").split(None, 1)
            args = parts[1].strip() if len(parts) > 1 else ""
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            if not args:
                await message.answer(t(lang, "askprob_current", prob=str(self.user_ask_prob.get(user_id, self.ask_question_prob))))
                return
            if args.lower() in ("reset", "default"):
                if user_id in self.user_ask_prob:
                    del self.user_ask_prob[user_id]
                    # update existing session
                    if user_id in self.sessions:
                        sess = self.sessions[user_id]
                        sess.ask_question_prob = self.ask_question_prob
                    if not self._save_user_prefs(force=True):
                        await self._warn_prefs_not_saved(message, lang)
                await message.answer(t(lang, "askprob_reset", prob=str(self.ask_question_prob)))
                return
            try:
                val = float(args)
                if not 0 <= val <= 1:
                    raise ValueError()
            except Exception:
                await message.answer(t(lang, "askprob_invalid"))
                return
            # set per-user override
            self.user_ask_prob[user_id] = val
            if user_id in self.sessions:
                sess = self.sessions[user_id]
                sess.ask_question_prob = val
            if not self._save_user_prefs(force=True):
                await self._warn_prefs_not_saved(message, lang)
            await message.answer(t(lang, "askprob_set", prob=str(val)))

        @self.dp.message(Command("lang"))
        async def cmd_lang(message: types.Message):
            # /lang en  or /lang ru  or /lang (show current)
            user_id = message.from_user.id
            parts = (message.text or "").split(None, 1)
            args = parts[1].strip().lower() if len(parts) > 1 else ""
            current = self.user_langs.get(user_id, DEFAULT_LANG)
            if not args:
                await message.answer(t(current, "lang_current", lang=current))
                return
            if args in ("ru", "en"):
                self.user_langs[user_id] = args
                # update session if exists
                if user_id in self.sessions:
                    sess = self.sessions[user_id]
                    sess.language = args
                    sess.system_prompt = sess._load_system_prompt()
                # persist
                if not self._save_user_prefs(force=True):
                    await self._warn_prefs_not_saved(message, args)
                await message.answer(t(args, "lang_set", lang=args), reply_markup=get_main_keyboard(args))
                return
            await message.answer(t(current, "lang_invalid"))

        @self.dp.message(Command("switchlang"))
        async def cmd_switchlang(message: types.Message):
            # Toggle between 'ru' and 'en' for convenience
            user_id = message.from_user.id
            current = self.user_langs.get(user_id, DEFAULT_LANG)
            new_lang = "ru" if current != "ru" else "en"
            self.user_langs[user_id] = new_lang
            # update session if exists
            if user_id in self.sessions:
                sess = self.sessions[user_id]
                sess.language = new_lang
                sess.system_prompt = sess._load_system_prompt()
            if not self._save_user_prefs(force=True):
                await self._warn_prefs_not_saved(message, new_lang)
            await message.answer(t(new_lang, "lang_set", lang=new_lang), reply_markup=get_main_keyboard(new_lang))
        
        @self.dp.message(Command("feedback"))
        async def cmd_feedback(message: types.Message):
            user_id = message.from_user.id
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            
            parts = (message.text or "").split(None, 1)
            if len(parts) < 2:
                await message.answer(t(lang, "feedback_prompt"), parse_mode="HTML")
                return
            
            feedback_text = parts[1]
            user_info = f"{message.from_user.full_name} (@{message.from_user.username}, id: {user_id})"
            
            # Отправляем админу
            try:
                admin_msg = t(lang, "feedback_admin_msg", user=user_info, text=feedback_text)
                await self.send_long_message(self.admin_id, admin_msg, parse_mode="HTML")
                await message.answer(t(lang, "feedback_thanks"))
            except Exception as e:
                print(f"Failed to send feedback: {e}")
                await message.answer("Error sending feedback.")

        @self.dp.message(Command("stats"))
        async def cmd_stats(message: types.Message):
            await self._handle_stats(message)

        @self.dp.message(Command("dumpall"))
        async def cmd_dumpall(message: types.Message):
            # Dump all users from memory to console (no file, just print)
            user_id = message.from_user.id
            if user_id != self.admin_id:
                return
            
            print("=" * 80)
            print("DUMP ALL USERS FROM MEMORY")
            print("=" * 80)
            print(f"Total users in memory: {len(self.user_langs)}")
            print()
            
            for uid in sorted(self.user_langs.keys()):
                lang = self.user_langs.get(uid, "unknown")
                username = self.user_usernames.get(uid, "not set")
                first_seen = self.user_first_seen.get(uid, "unknown")
                last_active = self.user_last_active.get(uid, "unknown")
                msg_count = self.user_message_counts.get(uid, 0)
                
                print(f"User ID: {uid}")
                print(f"  Username: @{username}")
                print(f"  Language: {lang}")
                print(f"  First seen: {first_seen}")
                print(f"  Last active: {last_active}")
                print(f"  Messages until summary: {16 - msg_count}")
                print()
            
            print("=" * 80)
            print(f"Dumped {len(self.user_langs)} users")
            print("=" * 80)
            
            await message.answer(f"Dumped {len(self.user_langs)} users to console. Check server logs.")

        @self.dp.message(Command("saveall"))
        async def cmd_saveall(message: types.Message):
            # Force save all user data from memory to user_prefs.json
            user_id = message.from_user.id
            if user_id != self.admin_id:
                return
            
            # Debug encryption status
            import sys
            print(f"[SAVEALL] Python: {sys.executable}")
            print(f"[SAVEALL] CRYPTO_AVAILABLE: {CRYPTO_AVAILABLE}")
            key = get_encryption_key()
            print(f"[SAVEALL] Encryption key: {'FOUND' if key else 'NOT FOUND'}")
            
            try:
                if not self._save_user_prefs(force=True):
                    raise RuntimeError("User preferences could not be saved")
                count = len(self.user_langs)
                
                # Check if file was actually encrypted
                with open(self.prefs_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                is_encrypted = content.strip().startswith('gAAAA')
                
                status = "🔒 ENCRYPTED" if is_encrypted else "⚠️ UNENCRYPTED"
                await message.answer(f"✅ Saved {count} users to user_prefs.json\n\nStatus: {status}")
                print(f"[ADMIN] Force saved {count} users. Encryption: {is_encrypted}")
            except Exception as e:
                await message.answer(f"❌ Error saving: {e}")
                print(f"[ADMIN] Error saving user prefs: {e}")
                import traceback
                traceback.print_exc()


        @self.dp.message(Command("recover"))
        async def cmd_recover(message: types.Message):
            """Recover usernames only - don't overwrite timestamps."""
            user_id = message.from_user.id
            if user_id != self.admin_id:
                return
            
            await message.answer("🔄 Recovering usernames only...")
            
            recovered_count = 0
            failed_count = 0
            
            for uid in list(self.user_langs.keys()):
                try:
                    chat = await self.bot.get_chat(uid)
                    if chat.username:
                        self.user_usernames[uid] = chat.username
                        recovered_count += 1
                except Exception as e:
                    failed_count += 1
                    print(f"[RECOVER] Failed for user {uid}: {e}")
            
            # Save only usernames - don't touch timestamps
            saved = self._save_user_prefs(force=True)
            
            # Report results
            total = len(self.user_langs)
            result_msg = (
                f"✅ Username recovery complete!\n\n"
                f"Total users: {total}\n"
                f"Recovered: {recovered_count}\n"
                f"Failed: {failed_count}\n\n"
                f"⚠️ Timestamps NOT overwritten."
            )
            await message.answer(result_msg)
            if not saved:
                await message.answer("❌ Username changes could not be saved.")
            print(f"[ADMIN] Recovered {recovered_count}/{total} usernames, failed: {failed_count}")

        @self.dp.message(Command("admin"))
        async def cmd_admin(message: types.Message):
            await self._handle_admin(message)

        @self.dp.message(Command("reset"))

        async def cmd_reset(message: types.Message):
            await self._handle_reset(message)
        
        @self.dp.message(Command("assoc"))
        async def cmd_assoc(message: types.Message):
            await self._handle_assoc_start(message)
        
        @self.dp.message(F.text == "Анализ ассоциаций")
        async def btn_assoc(message: types.Message):
            await self._handle_assoc_start(message)
        @self.dp.message(F.text == t('en', 'button_assoc'))
        async def btn_assoc_en(message: types.Message):
            await self._handle_assoc_start(message)
        
        @self.dp.message(Command("lysis"))
        async def cmd_lysis(message: types.Message):
            await self._handle_analyze_start(message)
        
        @self.dp.message(F.text == "Анализ истории")
        async def btn_analyze(message: types.Message):
            await self._handle_analyze_start(message)
        @self.dp.message(F.text == t('en', 'button_analyze'))
        async def btn_analyze_en(message: types.Message):
            await self._handle_analyze_start(message)
        
        @self.dp.message(F.text == "Снимок на плёнку")
        async def btn_filmframe(message: types.Message):
            await self._handle_meta(message)
        @self.dp.message(F.text == t('en', 'button_filmframe'))
        async def btn_filmframe_en(message: types.Message):
            await self._handle_meta(message)
        
        @self.dp.message(F.text == "Помощь")
        async def btn_help(message: types.Message):
            await self._handle_help(message)
        @self.dp.message(F.text == t('en', 'button_help'))
        async def btn_help_en(message: types.Message):
            await self._handle_help(message)
        
        @self.dp.message(F.text == "❌ Отмена")
        async def btn_cancel(message: types.Message):
            await self._handle_cancel(message)
        @self.dp.message(F.text == t('en', 'button_cancel'))
        async def btn_cancel_en(message: types.Message):
            await self._handle_cancel(message)
        
        @self.dp.message(F.voice)
        async def handle_voice(message: types.Message):
            await self._handle_voice(message)
        
        @self.dp.message(F.photo)
        async def handle_photo(message: types.Message):
            await self._handle_photo(message)
        
        @self.dp.message(F.sticker)
        async def handle_sticker(message: types.Message):
            user_id = message.from_user.id
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            
            # Эмодзи, связанный со стикером (если есть)
            emoji = message.sticker.emoji or ""
            
            therapist = self._get_therapist(user_id)
            
            # Формируем промпт для анализа стикера
            prompt = t(lang, "sticker_prompt", emoji=emoji)
            
            response = await asyncio.to_thread(therapist.generate_response, prompt, temporary_system_instruction=prompt, use_analysis_model=True)
            await message.answer(response)

        @self.dp.message(Command("shoot"))
        async def cmd_shoot(message: types.Message):
            await self._handle_meta(message)

        @self.dp.message(Command("remarque"))
        async def cmd_remarque(message: types.Message):
            await self._handle_remarque(message)

        @self.dp.message(Command("silence"))
        async def cmd_silence(message: types.Message):
            user_id = message.from_user.id
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            
            import time
            self.silence_until[user_id] = time.time() + 60
            
            await message.answer(t(lang, "silence_start"), parse_mode="HTML")
            
            await asyncio.sleep(60)
            
            # Если пользователь не прервал тишину или она закончилась сама
            if user_id in self.silence_until:
                del self.silence_until[user_id]
                await message.answer(t(lang, "silence_end"))

        @self.dp.message(Command("void"))
        async def cmd_void(message: types.Message):
            # Static effect only — never route through chat streaming.
            await self._handle_void(message)

        @self.dp.message(Command("meaning"))
        async def cmd_meaning(message: types.Message):
            await self._handle_meaning(message)

        @self.dp.message(Command("meaning_is"))
        async def cmd_meaning_is(message: types.Message):
            await self._handle_meaning_is(message)

        @self.dp.message(Command("meaning_gone"))
        async def cmd_meaning_gone(message: types.Message):
            await self._handle_meaning_gone(message)

        @self.dp.message(Command("meaning_where"))
        async def cmd_meaning_where(message: types.Message):
            await self._handle_meaning_where(message)

        @self.dp.message()
        async def handle_message(message: types.Message):
            await self._handle_message(message)
    
    async def _handle_start(self, message: types.Message):
        """Обработка /start."""
        user_id = message.from_user.id
        chat_id = message.chat.id
        user_name = message.from_user.first_name
        
        # Сбрасываем состояние
        self.user_states[user_id] = "chat"
        
        # Трекинг активности
        from datetime import datetime
        if user_id not in self.user_first_seen:
            self.user_first_seen[user_id] = datetime.now()
        self.user_last_active[user_id] = datetime.now()

        # Track username for admin lookup (fix for stats not showing username)
        if message.from_user.username:
            self.user_usernames[user_id] = message.from_user.username
            self._save_user_prefs(force=True)


        # Устанавливаем язык пользователя по его Telegram locale (если доступен)        # Но не переопределяем уже установленный пользователем язык.
        tg_lang = (message.from_user.language_code or "").lower()
        if user_id not in self.user_langs:
            if tg_lang.startswith("ru"):
                self.user_langs[user_id] = "ru"
            elif tg_lang.startswith("en"):
                self.user_langs[user_id] = "en"
            else:
                self.user_langs[user_id] = DEFAULT_LANG

        # Enable daily meanings by default for new users only
        # Do NOT reset existing users' meaning data to preserve timestamps and counters
        if user_id not in self.user_meaning_enabled:
            self.user_meaning_enabled[user_id] = True
            from datetime import datetime, timedelta
            self.user_meaning_last_time[user_id] = datetime.now() - timedelta(hours=25)
            # Initialize empty history for new users only
            self.user_meaning_history[user_id] = []


        # persist preference
        try:
            self._save_user_prefs(force=True)
        except Exception:
            pass

        # Immediate meaning for new users
        await self._check_and_send_daily_meaning(user_id, chat_id)

        # If session exists, update therapist language and reload prompt
        if user_id in self.sessions:
            sess = self.sessions[user_id]
            sess.language = self.user_langs[user_id]
            sess.system_prompt = sess._load_system_prompt()
        
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        welcome_text = t(lang, "welcome", name=user_name)

        await message.answer(welcome_text, reply_markup=get_main_keyboard(lang))
    
    async def _handle_help(self, message: types.Message):
        """Обработка /help."""
        user_id = message.from_user.id
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        help_text = t(lang, "help")

        await message.answer(help_text, parse_mode="HTML")
    
    async def _handle_stats(self, message: types.Message):
        """Обработка /stats (только для админа)."""
        user_id = message.from_user.id

        if user_id != self.admin_id:
            return  # Молча игнорируем не-админа

        parts = (message.text or "").split()
        export_full = len(parts) > 1 and parts[1].strip().lower() == "full"

        from datetime import datetime, timedelta

        now = datetime.now()
        yesterday = now - timedelta(hours=24)

        # Считаем уникальных пользователей за 24 часа
        active_24h = sum(1 for last in self.user_last_active.values() if last >= yesterday)

        # Общее количество пользователей
        total_users = len(self.user_langs)

        stats_text = f"📊 <b>Статистика бота</b>\n\n"
        stats_text += f"👤 Всего пользователей: <b>{total_users}</b>\n"
        stats_text += f"⏰ Активных за 24ч: <b>{active_24h}</b>\n\n"

        # Последние 37 уникальных юзеров по времени активности
        recent_users = sorted(
            self.user_last_active.items(),
            key=lambda x: x[1],
            reverse=True
        )[:37]

        # Если мало активных, добавляем остальных из user_langs без времени
        shown_ids = {uid for uid, _ in recent_users}
        remaining_users = [uid for uid in self.user_langs.keys() if uid not in shown_ids][:37 - len(recent_users)]

        if recent_users or remaining_users:
            stats_text += "📋 Последние 37 юзеров:\n"
            for uid, last_time in recent_users:
                blocked_marker = "⚫" if uid in self.blocked_users else ""
                username = self.user_usernames.get(uid, "—")
                username_str = f"@{username}" if username != "—" else "—"
                stats_text += f"• {uid} {username_str} {blocked_marker} ({last_time.strftime('%d.%m %H:%M')})\n"
            for uid in remaining_users:
                blocked_marker = "⚫" if uid in self.blocked_users else ""
                username = self.user_usernames.get(uid, "—")
                username_str = f"@{username}" if username != "—" else "—"
                stats_text += f"• {uid} {username_str} {blocked_marker}\n"

        if not export_full:
            stats_text += "\nℹ️ Для полной TXT-выгрузки: <code>/stats full</code>"

        await message.answer(stats_text, parse_mode="HTML")

        if not export_full:
            return

        # Полная выгрузка всех известных пользователей в TXT
        all_users_sorted = sorted(
            self.user_langs.keys(),
            key=lambda uid: self.user_last_active.get(uid, datetime.min),
            reverse=True,
        )

        txt_lines = [
            "TELEGRAM BOT USERS EXPORT",
            f"generated_at={now.isoformat()}",
            f"total_users={len(all_users_sorted)}",
            f"active_24h={active_24h}",
            "",
            "user_id\tusername\tlang\tblocked\tfirst_seen\tlast_active\tmsg_count\tsummary_present",
        ]

        for uid in all_users_sorted:
            username = self.user_usernames.get(uid, "—")
            username_str = f"@{username}" if username != "—" else "—"
            lang = self.user_langs.get(uid, DEFAULT_LANG)
            blocked = "yes" if uid in self.blocked_users else "no"
            first_seen = self.user_first_seen.get(uid)
            last_active = self.user_last_active.get(uid)
            first_seen_str = first_seen.strftime('%Y-%m-%d %H:%M:%S') if first_seen else "—"
            last_active_str = last_active.strftime('%Y-%m-%d %H:%M:%S') if last_active else "—"
            msg_count = self.user_message_counts.get(uid, 0)
            summary_present = "yes" if self.user_summaries.get(uid) else "no"
            txt_lines.append(
                f"{uid}\t{username_str}\t{lang}\t{blocked}\t{first_seen_str}\t{last_active_str}\t{msg_count}\t{summary_present}"
            )

        export_dir = Path(__file__).parent.parent / "data"
        export_dir.mkdir(exist_ok=True)
        export_path = export_dir / f"users_stats_{now.strftime('%Y%m%d_%H%M%S')}.txt"

        try:
            export_path.write_text("\n".join(txt_lines), encoding="utf-8")
            await message.answer_document(
                types.FSInputFile(str(export_path)),
                caption=f"📄 Полная выгрузка пользователей ({len(all_users_sorted)} записей)",
            )
        except Exception as e:
            await message.answer(f"Не удалось отправить TXT-выгрузку: {e}")
        finally:
            try:
                if export_path.exists():
                    export_path.unlink()
            except Exception:
                pass
    async def _handle_admin(self, message: types.Message):
        """Обработка /admin <сообщение> — массовая рассылка от админа."""
        admin_id = message.from_user.id
        
        if admin_id != self.admin_id:
            return  # Молча игнорируем не-админа
        
        # Парсим аргументы
        parts = (message.text or "").split(None, 1)
        if len(parts) < 2:
            await message.answer(
                "Использование: /admin <сообщение>\n\n"
                "Пример: /admin Важное обновление! Теперь бот поддерживает голосовые сообщения.\n\n"
                "Сообщение будет отправлено всем пользователям бота."
            )
            return
        
        broadcast_text = parts[1].strip()
        
        # Проверка длины сообщения
        if len(broadcast_text) > 4000:
            await message.answer("❌ Сообщение слишком длинное (максимум 4000 символов)")
            return
        
        if len(broadcast_text) < 1:
            await message.answer("❌ Сообщение не может быть пустым")
            return
        
        # Подтверждение перед отправкой
        preview = (
            f"📢 <b>Предпросмотр рассылки</b>\n\n"
            f"{broadcast_text[:200]}{'...' if len(broadcast_text) > 200 else ''}\n\n"
            f"Получателей: {len(self.user_langs)}\n\n"
            f"Отправить? Ответьте <b>да</b> для подтверждения."
        )
        
        await message.answer(preview, parse_mode="HTML")
        
        # Ждём подтверждения (простая реализация через состояние)
        self.user_states[admin_id] = f"admin_confirm:{broadcast_text}"
    
    async def _process_admin_broadcast(self, message: types.Message, broadcast_text: str):
        """Выполнение рассылки после подтверждения."""
        admin_id = message.from_user.id
        
        # Сбрасываем состояние
        self.user_states[admin_id] = "chat"
        
        # Статистика
        sent_count = 0
        failed_count = 0
        failed_users = []
        
        # Отправляем статус начала рассылки
        status_msg = await message.answer(f"🚀 Начинаю рассылку для {len(self.user_langs)} пользователей...")
        
        # Рассылка всем пользователям
        for user_id in list(self.user_langs.keys()):
            if not self._has_consent(user_id):
                continue
            try:
                await self.send_long_message(
                    user_id,
                    f"📢 <b>Сообщение от администратора:</b>\n\n{broadcast_text}",
                    parse_mode="HTML"
                )                
                sent_count += 1
            except Exception as e:
                failed_count += 1
                failed_users.append(str(user_id))
                print(f"[ADMIN BROADCAST] Failed to send to {user_id}: {e}")
        
        # Формируем отчёт
        report_lines = [
            f"✅ <b>Рассылка завершена</b>",
            f"",
            f"📤 Успешно отправлено: <b>{sent_count}</b>",
            f"❌ Ошибок: <b>{failed_count}</b>",
        ]
        
        if failed_count > 0:
            report_lines.append(f"")
            report_lines.append(f"Не удалось отправить пользователям: {', '.join(failed_users[:10])}")
            if len(failed_users) > 10:
                report_lines.append(f"... и ещё {len(failed_users) - 10}")
        
        # Логирование
        print(f"[ADMIN BROADCAST] Admin {admin_id} sent broadcast to {sent_count}/{len(self.user_langs)} users")
        
        await status_msg.edit_text("\n".join(report_lines), parse_mode="HTML")

    def _clear_pending_update(self, admin_id: Optional[int] = None) -> None:
        """Drop pending update changelogs and leave the update-confirm flow."""
        if hasattr(self, "pending_update_changelogs"):
            del self.pending_update_changelogs
        target_id = admin_id if admin_id is not None else self.admin_id
        if self.user_states.get(target_id, "").startswith("update_"):
            self.user_states[target_id] = "chat"

    async def _skip_unanalysable_update(self, message: types.Message, args: list[str]) -> None:
        """Explicitly skip only the identified unrecoverable or no-change draft."""
        if not self.admin_id or message.from_user.id != self.admin_id:
            return
        async with self.update_check_lock:
            pending = self.update_cache.pending()
            state = _update_review_state(pending, pending.get("notes")) if pending else None
            analysis = (pending or {}).get("analysis")
            missing = analysis.get("missing", []) if isinstance(analysis, dict) else []
            if (len(args) != 2 or args[0] != "skip" or not pending
                    or pending.get("id") != args[1]
                    or pending.get("status") != "review"
                    or not (state == "no_changes" or (state == "incomplete" and missing))):
                await message.answer("Черновик изменился или его нельзя пропустить этой командой. Повторите /updates.")
                return
            self.update_cache.resolve(pending)
            self._clear_pending_update(message.from_user.id)
        await message.answer(
            "Черновик пропущен без рассылки. Для проверки новых изменений вызовите /updates.",
            reply_markup=get_main_keyboard(self.user_langs.get(message.from_user.id, DEFAULT_LANG)),
        )

    def _format_update_preview(self, changelogs: dict) -> str:
        """Build HTML preview of RU/EN changelogs for admin confirmation."""
        cache = getattr(self, "update_cache", None)
        pending = (cache.pending() or {}) if isinstance(cache, UpdateCache) else None
        state = _update_review_state(pending, changelogs)
        if state != "review":
            return _update_review_notice(state, pending)
        return "\n\n".join(_update_preview_parts(pending, changelogs))

    async def _send_update_preview(self, chat_id: int, changelogs: dict) -> str:
        """Send update preview and put admin into confirmation state."""
        cache = getattr(self, "update_cache", None)
        pending = (cache.pending() or {}) if isinstance(cache, UpdateCache) else None
        state = _update_review_state(pending, changelogs)
        if state != "review":
            self._clear_pending_update(chat_id)
            await self.send_long_message(
                chat_id, _update_review_notice(state, pending),
                reply_markup=get_main_keyboard(self.user_langs.get(chat_id, DEFAULT_LANG)),
            )
            return state
        parts = _update_preview_parts(pending, changelogs)
        combined = "\n\n".join(parts)
        if len(combined) <= 4000 and len(combined.encode("utf-16-le")) // 2 <= 4000:
            parts = [combined]
        for index, part in enumerate(parts):
            await self.send_long_message(
                chat_id, part, parse_mode="HTML",
                reply_markup=get_update_confirm_keyboard() if index == len(parts) - 1 else None,
            )
        self.pending_update_changelogs = {"ru": changelogs["ru"], "en": changelogs["en"]}
        self.user_states[chat_id] = "update_confirm"
        return "review"

    async def _start_update_edit(self, message: types.Message) -> None:
        """Begin bilingual changelog editing: RU first, then EN."""
        user_id = message.from_user.id
        changelogs = getattr(self, "pending_update_changelogs", None)
        if not changelogs:
            self.user_states[user_id] = "chat"
            await message.answer(
                "Нет черновика обновления для редактирования.",
                reply_markup=get_main_keyboard(self.user_langs.get(user_id, DEFAULT_LANG)),
            )
            return

        self.user_states[user_id] = "update_edit_ru"
        current_ru = changelogs.get("ru") or "—"
        await message.answer(
            "✏️ <b>Редактирование changelog (RU)</b>\n\n"
            "Отправьте полный русский текст одним сообщением.\n"
            "Текущая версия:",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard("ru"),
        )
        await self.send_long_message(user_id, current_ru)

    async def _handle_update_confirm_input(self, message: types.Message, text: str) -> None:
        """Handle yes / edit / no while an update broadcast is pending."""
        user_id = message.from_user.id
        choice = (text or "").strip().lower()

        cache = getattr(self, "update_cache", None)
        if isinstance(cache, UpdateCache):
            pending = cache.pending()
            if pending and pending["status"] == "sending":
                await message.answer("Рассылка уже подтверждена. Для повтора используйте /updates.")
                return
            pending = pending or {}
        else:
            pending = None
        changelogs = getattr(self, "pending_update_changelogs", {})
        review_state = _update_review_state(pending, changelogs)
        if review_state != "review":
            self._clear_pending_update(user_id)
            await message.answer(
                _update_review_notice(review_state),
                reply_markup=get_main_keyboard(self.user_langs.get(user_id, DEFAULT_LANG)),
            )
            return
        if choice in _UPDATE_CONFIRM_YES:
            await self._process_update_broadcast()
            return

        if choice in _UPDATE_CONFIRM_EDIT:
            await self._start_update_edit(message)
            return

        if choice in _UPDATE_CONFIRM_NO:
            cache = getattr(self, "update_cache", None)
            resolved = False
            if isinstance(cache, UpdateCache):
                pending = cache.pending()
                if pending and _update_review_state(pending, changelogs) == "review":
                    cache.resolve(pending)
                    resolved = True
            self._clear_pending_update(user_id)
            await message.answer(
                "❌ Обновление пропущено",
                reply_markup=get_main_keyboard(self.user_langs.get(user_id, DEFAULT_LANG)),
            )
            if resolved:
                asyncio.create_task(self._check_and_notify_updates(delay=False))
            return

        await message.answer(
            "Ответьте <b>да</b>, <b>править</b> или <b>нет</b>.",
            parse_mode="HTML",
            reply_markup=get_update_confirm_keyboard(),
        )

    async def _handle_update_edit_input(self, message: types.Message, state: str, text: str) -> None:
        """Save edited RU/EN changelog text and continue the flow."""
        user_id = message.from_user.id
        if not hasattr(self, "pending_update_changelogs"):
            self.user_states[user_id] = "chat"
            await message.answer(
                "Черновик обновления уже неактуален.",
                reply_markup=get_main_keyboard(self.user_langs.get(user_id, DEFAULT_LANG)),
            )
            return

        cache = getattr(self, "update_cache", None)
        pending = (cache.pending() or {}) if isinstance(cache, UpdateCache) else None
        review_state = _update_review_state(pending, self.pending_update_changelogs)
        if review_state != "review":
            self._clear_pending_update(user_id)
            await message.answer(
                _update_review_notice(review_state),
                reply_markup=get_main_keyboard(self.user_langs.get(user_id, DEFAULT_LANG)),
            )
            return

        edited = (text or "").strip()
        if len(edited) > 3500:
            await message.answer(
                "❌ Слишком длинный текст (максимум 3500 символов). Сократите и отправьте снова.",
                reply_markup=get_cancel_keyboard("ru"),
            )
            return
        if not _valid_update_text(edited):
            await message.answer(
                "Текст пустой или некорректный. Пришлите полный changelog или нажмите отмену.",
                reply_markup=get_cancel_keyboard("ru"),
            )
            return

        if state == "update_edit_ru":
            self.pending_update_changelogs["ru"] = edited
            cache = getattr(self, "update_cache", None)
            if isinstance(cache, UpdateCache):
                pending = cache.pending()
                if pending and pending["status"] == "review":
                    pending["notes"]["ru"] = edited
                    cache.update(pending)
            self.user_states[user_id] = "update_edit_en"
            current_en = self.pending_update_changelogs.get("en") or "—"
            await message.answer(
                "✅ RU сохранён.\n\n"
                "✏️ <b>Редактирование changelog (EN)</b>\n\n"
                "Отправьте полный английский текст одним сообщением.\n"
                "Текущая версия:",
                parse_mode="HTML",
                reply_markup=get_cancel_keyboard("ru"),
            )
            await self.send_long_message(user_id, current_en)
            return

        # update_edit_en
        self.pending_update_changelogs["en"] = edited
        cache = getattr(self, "update_cache", None)
        if isinstance(cache, UpdateCache):
            pending = cache.pending()
            if pending and pending["status"] == "review":
                pending["notes"]["en"] = edited
                cache.update(pending)
        await message.answer("✅ EN сохранён. Обновлённый предпросмотр:")
        await self._send_update_preview(user_id, self.pending_update_changelogs)

    async def _process_update_broadcast(self):
        """Выполнение рассылки обновлений после подтверждения админа."""
        cache = self.update_cache
        async with self.update_broadcast_lock:
            pending = cache.pending()
            if not pending or pending["status"] not in ("review", "sending"):
                return
            if pending["status"] == "review":
                if _update_review_state(pending, pending.get("notes")) != "review":
                    return
                pending["recipients"] = {
                    str(user_id): {"status": "pending", "lang": self.user_langs.get(user_id, DEFAULT_LANG)}
                    for user_id in self.user_langs if self._has_consent(user_id)
                }
                pending["status"] = "sending"
                cache.update(pending)
            await self._resume_update_broadcast(pending)

    async def _resume_update_broadcast(self, pending):
        cache = self.update_cache
        changelogs = pending["notes"]
        sent_count = 0
        failed_users = []
        for user_key, recipient in pending["recipients"].items():
            if recipient["status"] in ("sent", "skipped"):
                continue
            if recipient["status"] in ("in_flight", "uncertain", "failed"):
                # Telegram may have accepted a request before a timeout or
                # process crash. Retrying it could broadcast the same update twice.
                recipient["status"] = "uncertain"
                failed_users.append(user_key)
                cache.update(pending)
                continue
            user_id = int(user_key)
            if not self._has_consent(user_id):
                recipient["status"] = "skipped"
                cache.update(pending)
                continue
            try:
                user_lang = recipient["lang"]
                changelog = changelogs["ru" if user_lang == "ru" else "en"]
                localized = (
                    t(user_lang, "update_notification_header")
                    + html.escape(str(changelog))
                    + t(user_lang, "update_notification_footer")
                )
                recipient["status"] = "in_flight"
                cache.update(pending)
                await self.send_long_message(user_id, localized, parse_mode="HTML")
                recipient["status"] = "sent"
                cache.update(pending)
                sent_count += 1
            except Exception as exc:
                recipient["status"] = "uncertain"
                cache.update(pending)
                failed_users.append(user_key)
                print(f"[UPDATE BROADCAST] Delivery failed: {type(exc).__name__}")

        cache.resolve(pending)
        self._clear_pending_update(self.admin_id)
        report = (
            f"Рассылка: отправлено в этой попытке: {sent_count}; "
            f"неизвестный результат: {len(failed_users)}."
            + (" Повторная автоматическая отправка отключена во избежание дублей."
               if failed_users else "")
        )
        try:
            await self.send_long_message(
                self.admin_id, report,
                reply_markup=get_main_keyboard(self.user_langs.get(self.admin_id, DEFAULT_LANG)),
            )
        finally:
            # Check after restoring the keyboard so a newer preview isn't overwritten.
            asyncio.create_task(self._check_and_notify_updates(delay=False))
    async def _handle_reset(self, message: types.Message):
        user_id = message.from_user.id
        async with self._get_user_lock(user_id):
            if hasattr(self, "music"):
                try:
                    store = self.music.existing_store()
                    if store:
                        store.reset_session(user_id)
                except Exception as exc:
                    logger.warning("Music session reset failed: type=%s", type(exc).__name__)
            if user_id in self.sessions:
                self.sessions[user_id].reset()
            self.user_message_counts[user_id] = 0
            self.user_recent_messages[user_id] = []
            self.user_summaries[user_id] = ""
            self.user_states[user_id] = "chat"
            saved = self._save_user_prefs(force=True)
        lang = self.user_langs.get(user_id, DEFAULT_LANG)

        await message.answer(
            t(lang, "reset_confirm") if saved else (
                "History was cleared for this session, but could not be saved; it may return after restart."
                if lang == "en" else
                "История очищена в текущей сессии, но не сохранена и может вернуться после перезапуска."
            ),
            reply_markup=get_main_keyboard(lang)
        )


    
    async def _handle_meta(self, message: types.Message):
        """Film-frame entry point."""
        from app.features.filmframe.handlers import start_filmframe
        await start_filmframe(self, message)

    async def _handle_remarque(self, message: types.Message):
        """Offer an observation grounded in recent dialogue without changing it."""
        user_id = message.from_user.id
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        async with self._get_user_lock(user_id):
            therapist = self._get_therapist(user_id)
            if not therapist.history:
                await message.answer(t(lang, 'story_too_short'))
                return
            context = "\n".join(f"{item.role}: {item.content}" for item in therapist.history[-10:])
            await message.answer(t(lang, 'meta_analyzing'), parse_mode='HTML')
            try:
                response = await asyncio.to_thread(
                    therapist.generate_response, f"{t(lang, 'meta_prompt')}\n\n{context}",
                    temporary_system_instruction=therapist.system_prompt, use_analysis_model=True,
                )
                if not response or response.startswith("Ошибка"):
                    raise ValueError("Empty or failed response")
                label = 'Наблюдение' if lang == 'ru' else 'Remarque'
                await message.answer(f'<b>{label}:</b>\n\n{html.escape(response)}', parse_mode='HTML')
            except Exception:
                await message.answer(t(lang, "error_llm"))

    async def _handle_ff_describe(self, message: types.Message, text: str):
        """Filmframe: user describes state -> build scene."""
        from app.features.filmframe.handlers import handle_ff_describe
        await handle_ff_describe(self, message, text)

    async def _handle_ff_preview(self, message: types.Message, text: str):
        """Filmframe: user on preview -> confirm/edit/cancel."""
        from app.features.filmframe.handlers import handle_ff_preview
        await handle_ff_preview(self, message, text)

    async def _handle_ff_edit(self, message: types.Message, text: str):
        """Filmframe: user editing scene."""
        from app.features.filmframe.handlers import handle_ff_edit
        await handle_ff_edit(self, message, text)

    async def _handle_assoc_start(self, message: types.Message):
        """Начало сбора ассоциаций."""
        user_id = message.from_user.id
        self.user_states[user_id] = "assoc_freedom"
        self.temp_associations[user_id] = {}
        lang = self.user_langs.get(user_id, DEFAULT_LANG)

        await message.answer(
            t(lang, "assoc_start"),
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard(lang)
        )
    
    async def _handle_analyze_start(self, message: types.Message):
        """Начало анализа истории."""
        user_id = message.from_user.id
        self.user_states[user_id] = "analyze_story"
        lang = self.user_langs.get(user_id, DEFAULT_LANG)

        await message.answer(
            t(lang, "analyze_start"),
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard(lang)
        )
    
    async def _handle_cancel(self, message: types.Message):
        """Отмена текущего действия."""
        user_id = message.from_user.id
        state = self.user_states.get(user_id, "chat")
        self.user_states[user_id] = "chat"
        if user_id in self.temp_associations:
            del self.temp_associations[user_id]
        if user_id in self.filmframe_state:
            del self.filmframe_state[user_id]
        # Cancellation leaves the persisted draft available via /updates.
        if user_id == self.admin_id and state.startswith("update_"):
            if hasattr(self, "pending_update_changelogs"):
                del self.pending_update_changelogs

        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        await message.answer(
            t(lang, "action_cancelled"),
            reply_markup=get_main_keyboard(lang)
        )
    
    async def _process_text_message(
        self, message: types.Message, text: str, is_voice: bool = False,
        queue_entry: Optional[float] = None,
    ):
        """Обработка текстового сообщения (или распознанного голоса)."""
        user_id = message.from_user.id
        # Intercept localized cancel texts so they aren't processed as content while in a special state
        t_ru_cancel = t('ru', 'button_cancel')
        t_en_cancel = t('en', 'button_cancel')
        if text and text.strip() in (t_ru_cancel, t_en_cancel, '/cancel', '❌ Отмена', 'Cancel'):
            try:
                await self._handle_cancel(message)
            finally:
                if queue_entry is not None:
                    try:
                        self.message_queue.get(user_id, []).remove(queue_entry)
                    except ValueError:
                        pass
            return
        
        # Проверяем подтверждение рассылки
        state = self.user_states.get(user_id, "chat")
        if state.startswith("admin_confirm:"):
            if text.strip().lower() in ("да", "yes", "д", "y"):
                broadcast_text = state[14:]  # убираем префикс "admin_confirm:"
                await self._process_admin_broadcast(message, broadcast_text)
            else:
                self.user_states[user_id] = "chat"
                await message.answer("❌ Рассылка отменена")
            return

        # Подтверждение / правка changelog обновления (admin)
        choice = (text or "").strip().lower()
        update_input = user_id == self.admin_id and (
            state in ("update_confirm", "update_edit_ru", "update_edit_en")
            # Backward-compatible: pending draft without explicit state.
            or (state == "chat" and hasattr(self, "pending_update_changelogs")
                and choice in _UPDATE_CONFIRM_YES | _UPDATE_CONFIRM_EDIT | _UPDATE_CONFIRM_NO)
        )
        if update_input:
            if state == "chat":
                self.user_states[user_id] = "update_confirm"
                state = "update_confirm"
            try:
                if state == "update_confirm":
                    await self._handle_update_confirm_input(message, text)
                else:
                    await self._handle_update_edit_input(message, state, text)
            finally:
                if queue_entry is not None:
                    try:
                        self.message_queue.get(user_id, []).remove(queue_entry)
                    except ValueError:
                        pass
            return

        
        # Если язык еще не установлен, пробуем определить его один раз
        if user_id not in self.user_langs:
            try:
                if len(text.strip()) >= 5:
                    code, prob = detect_language(text)
                    if code == "ru":
                        self.user_langs[user_id] = "ru"
                    else:
                        self.user_langs[user_id] = "en"
                    
                    if user_id in self.sessions:
                        sess = self.sessions[user_id]
                        sess.language = self.user_langs[user_id]
                        sess.system_prompt = sess._load_system_prompt()
                    self._save_user_prefs(force=True)
            except Exception:
                pass
        
        # Filmframe flow
        if state == "ff_describe":
            await self._handle_ff_describe(message, text)
            return
        if state == "ff_preview":
            await self._handle_ff_preview(message, text)
            return
        if state == "ff_edit":
            await self._handle_ff_edit(message, text)
            return

        # Если собираем ассоциации

        if state.startswith("assoc_"):
            await self._handle_assoc_input(message, state, text)
            return
        
        # Если анализируем историю
        if state == "analyze_story":
            await self._handle_story_input(message, text)
            return
        
        # Обычный чат
        await self._handle_chat(message, text, is_voice=is_voice)

    async def _handle_message(self, message: types.Message):
        """Обработка обычных сообщений."""
        # Защита от лавины старых сообщений при запуске
        import time
        now = time.time()
        if message.date.timestamp() < self.start_time - 10:
            user_id = message.from_user.id
            if user_id not in self.processed_flood_users:
                lang = self.user_langs.get(user_id, DEFAULT_LANG)
                await message.answer(t(lang, "error_flood"))
                self.processed_flood_users.add(user_id)
            return

        user_id = message.from_user.id

        # Commands are handled by dedicated handlers — never enqueue or stream them.
        if message.text and message.text.startswith('/'):
            return
        
        # Message queue flood protection (max 3 pending messages).
        # Drop stale entries so a stuck flow can't lock the user out forever.
        if user_id not in self.message_queue:
            self.message_queue[user_id] = []
        self.message_queue[user_id] = [
            ts for ts in self.message_queue[user_id] if now - ts < 180
        ]

        # If >=3 messages already waiting for response, trigger flood warning
        if len(self.message_queue[user_id]) >= 3:
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            await message.answer(t(lang, "error_flood"))
            return

        # Add current message to queue (using timestamp as ID)
        self.message_queue[user_id].append(now)
        
        # Трекинг активности пользователя        
        
        from datetime import datetime
        now_dt = datetime.now()
        is_new_user = user_id not in self.user_first_seen
        if is_new_user:
            self.user_first_seen[user_id] = now_dt
        self.user_last_active[user_id] = now_dt        
        # Сохраняем при каждом сообщении для актуальности таймстемпов
        self._save_user_prefs(force=True)
        # Проверка на активную минуту тишины
        if user_id in self.silence_until:
            remaining = int(self.silence_until[user_id] - now)
            if remaining > 0:
                lang = self.user_langs.get(user_id, DEFAULT_LANG)
                await message.answer(t(lang, "silence_remaining", remaining=remaining), parse_mode="HTML")
                return
            else:
                del self.silence_until[user_id]

        # Но если мы здесь, значит это либо текст, либо другой тип сообщения.
        if message.text:
            await self._process_text_message(message, message.text, is_voice=False, queue_entry=now)
        elif message.voice:
            # Голосовые сообщения обрабатываются отдельно, но если попали сюда - игнорируем
            pass
    async def _handle_assoc_input(self, message: types.Message, state: str, text: str):
        """Обработка ввода ассоциаций."""
        user_id = message.from_user.id
        text = text.strip()        
        # Парсим ассоциации
        words = []
        for sep in [',', ' ', ';']:
            if sep in text:
                words = [w.strip().lower() for w in text.split(sep) if w.strip()]
                break
        if not words:
            words = [text.lower()]
        
        # Сохраняем
        lang = self.user_langs.get(user_id, DEFAULT_LANG)

        if state == "assoc_freedom":
            self.temp_associations[user_id]["freedom"] = words[:5]
            self.user_states[user_id] = "assoc_nonsense"
            await message.answer(
                t(lang, "assoc_confirm", label=("Свобода" if lang == "ru" else "Freedom"), words=html.escape(', '.join(words[:5]))),
                parse_mode="HTML"
            )
            await message.answer(t(lang, "assoc_nonsense_prompt"), parse_mode="HTML")
            # Remove from queue since we handled it
            if user_id in self.message_queue and self.message_queue[user_id]:
                self.message_queue[user_id].pop(0)

        elif state == "assoc_nonsense":
            self.temp_associations[user_id]["nonsense"] = words[:5]
            self.user_states[user_id] = "assoc_solitude"
            await message.answer(
                t(lang, "assoc_confirm", label=("Бессмысленность" if lang == "ru" else "Meaninglessness"), words=html.escape(', '.join(words[:5]))),
                parse_mode="HTML"
            )
            await message.answer(t(lang, "assoc_solitude_prompt"), parse_mode="HTML")
            # Remove from queue since we handled it
            if user_id in self.message_queue and self.message_queue[user_id]:
                self.message_queue[user_id].pop(0)

        elif state == "assoc_solitude":
            self.temp_associations[user_id]["solitude"] = words[:5]
            self.user_states[user_id] = "assoc_death"
            await message.answer(
                t(lang, "assoc_confirm", label=("Одиночество" if lang == "ru" else "Isolation"), words=html.escape(', '.join(words[:5]))),
                parse_mode="HTML"
            )
            await message.answer(t(lang, "assoc_death_prompt"), parse_mode="HTML")
            # Remove from queue since we handled it
            if user_id in self.message_queue and self.message_queue[user_id]:
                self.message_queue[user_id].pop(0)
        elif state == "assoc_death":
            self.temp_associations[user_id]["death"] = words[:5]

            # Все ассоциации собраны, анализируем
            associations = self.temp_associations[user_id]

            summary_lines = [t(lang, "assoc_confirm", label=("Смерть" if lang == "ru" else "Death"), words=html.escape(', '.join(words[:5])))]
            summary_lines.append("")
            summary_lines.append(t(lang, "assoc_summary_intro"))
            summary_lines.append(f"{('Свобода' if lang=='ru' else 'Freedom')}: {html.escape(', '.join(associations.get('freedom', [])))}")
            summary_lines.append(f"{('Бессмысленность' if lang=='ru' else 'Meaninglessness')}: {html.escape(', '.join(associations.get('nonsense', [])))}")
            summary_lines.append(f"{('Одиночество' if lang=='ru' else 'Solitude')}: {html.escape(', '.join(associations.get('solitude', [])))}")
            summary_lines.append(f"{('Смерть' if lang=='ru' else 'Death')}: {html.escape(', '.join(associations.get('death', [])))}")
            summary_lines.append("")
            summary_lines.append(t(lang, "analyzing"))
            
            summary = "\n".join(summary_lines)

            await message.answer(summary, parse_mode="HTML")

            # Получаем анализ
            therapist = self._get_therapist(user_id)
            
            sent = await self._new_delivery_placeholder(message)
            try:
                async with self._animate_loading_dots(sent) as loading:
                    source = self._stream_generic(therapist.analyze_associations_stream, associations)
                    analysis, shown = await self._smooth_stream_generic(source, words_per_tick=7)
                if not analysis.strip():
                    raise ValueError("Empty analysis")
                title = 'Интерпретация' if lang == 'ru' else 'Interpretation'
                prefix = f"{title}:\n\n"
                # Deliver the model's text verbatim, with parsing disabled. Formatting
                # each incomplete prefix separately corrupts Markdown and identifiers.
                await self._deliver_response(
                    message, sent, prefix + analysis,
                    None if loading.rate_limited else shown, words_per_tick=7,
                )
                self.user_states[user_id] = "chat"
                del self.temp_associations[user_id]
            except DeliveryError as exc:
                await self._show_delivery_error(
                    message, None,
                    t(lang, "error_delivery_partial" if exc.delivered else "error_delivery"),
                )
            except Exception:
                await self._show_delivery_error(message, sent, t(lang, "error_generation_incomplete"))
            finally:
                if user_id in self.message_queue and self.message_queue[user_id]:
                    self.message_queue[user_id].pop(0)

    async def _handle_story_input(self, message: types.Message, text: str):        
        """Обработка ввода истории."""
        user_id = message.from_user.id
        text = text.strip()        
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        if len(text) < 20:
            try:
                await message.answer(t(lang, "story_too_short"))
            finally:
                if user_id in self.message_queue and self.message_queue[user_id]:
                    self.message_queue[user_id].pop(0)
            return
        
        if len(text) > 3000:
            try:
                await message.answer(t(lang, "story_too_long"))
            finally:
                if user_id in self.message_queue and self.message_queue[user_id]:
                    self.message_queue[user_id].pop(0)
            return        
        therapist = self._get_therapist(user_id)
        
        sent = await self._new_delivery_placeholder(message)
        try:
            async with self._animate_loading_dots(sent) as loading:
                source = self._stream_generic(therapist.analyze_story_stream, text)
                analysis, shown = await self._smooth_stream_generic(source, words_per_tick=7)
            if not analysis.strip():
                raise ValueError("Empty analysis")
            title = 'Экзистенциальный отклик' if lang == 'ru' else 'Existential response'
            prefix = f"{title}:\n\n"
            await self._deliver_response(
                message, sent, prefix + analysis,
                None if loading.rate_limited else shown, words_per_tick=7,
            )
            self.user_states[user_id] = "chat"
        except DeliveryError as exc:
            await self._show_delivery_error(
                message, None,
                t(lang, "error_delivery_partial" if exc.delivered else "error_delivery"),
            )
        except Exception:
            await self._show_delivery_error(message, sent, t(lang, "error_generation_incomplete"))
        finally:
            if user_id in self.message_queue and self.message_queue[user_id]:
                self.message_queue[user_id].pop(0)

    async def _handle_meaning(self, message: types.Message):
        user_id = message.from_user.id
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        therapist = self._get_therapist(user_id)
        meaning_prompt = t(lang, "meaning_prompt")
        meaning_system = t(lang, "meaning_system")
        try:
            response = await asyncio.to_thread(therapist.generate_response, meaning_prompt, temporary_system_instruction=meaning_system, use_analysis_model=False)
            await message.answer(f"🌱 {response}")        
        except Exception as e:
            print(f"[MEANING] Error: {e}")
            await message.answer(t(lang, "error_llm"))

    async def _handle_meaning_is(self, message: types.Message):        
        user_id = message.from_user.id
        self.user_meaning_enabled[user_id] = True
        from datetime import datetime
        self.user_meaning_last_time[user_id] = datetime.now()
        saved = self._save_user_prefs(force=True)
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        await message.answer(t(lang, "meaning_enabled"))
        if not saved:
            await self._warn_prefs_not_saved(message, lang)

    async def _handle_meaning_gone(self, message: types.Message):
        user_id = message.from_user.id
        self.user_meaning_enabled[user_id] = False
        saved = self._save_user_prefs(force=True)
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        await message.answer(t(lang, "meaning_disabled"))
        if not saved:
            await self._warn_prefs_not_saved(message, lang)

    async def _handle_meaning_where(self, message: types.Message):
        user_id = message.from_user.id
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        
        if not self.user_meaning_enabled.get(user_id, False):
            await message.answer(t(lang, "meaning_disabled"))
            return

        last_time = self.user_meaning_last_time.get(user_id)
        if not last_time:
            msg = "Next meaning will arrive soon." if lang == "en" else "Следующий смысл придет скоро."
            await message.answer(msg)
            return

        from datetime import datetime, timedelta
        now = datetime.now()
        next_time = last_time + timedelta(hours=24)
        diff = next_time - now
        
        if diff.total_seconds() <= 0:
            msg = "Next meaning will arrive soon." if lang == "en" else "Следующий смысл придет скоро."
        else:
            hours = int(diff.total_seconds() // 3600)
            minutes = int((diff.total_seconds() % 3600) // 60)
            if lang == "en":
                msg = f"Next meaning in {hours}h {minutes}m."
            else:
                msg = f"Следующий смысл через {hours}ч {minutes}мин."
        
        await message.answer(msg)

    async def _check_and_send_daily_meaning(self, user_id: int, chat_id: int):
        """Check if it's time to send a daily meaning and send it."""
        if not self._has_consent(user_id) or not self.user_meaning_enabled.get(user_id, False):
            return

        from datetime import datetime, timedelta
        now = datetime.now()
        last_time = self.user_meaning_last_time.get(user_id)
        
        if not last_time or (now - last_time) >= timedelta(hours=24):
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            therapist = self._get_therapist(user_id)
            meaning_prompt = t(lang, "meaning_prompt")
            meaning_system = t(lang, "meaning_system")
            
            try:
                response = await asyncio.to_thread(therapist.generate_response, meaning_prompt, temporary_system_instruction=meaning_system, use_analysis_model=False)

                history = self.user_meaning_history.get(user_id, [])
                if response in history:
                    response = await asyncio.to_thread(therapist.generate_response, meaning_prompt, temporary_system_instruction=meaning_system, use_analysis_model=False)
                history.append(response)
                if len(history) > 100:
                    history = [response]
                
                self.user_meaning_history[user_id] = history
                self.user_meaning_last_time[user_id] = now
                self.user_meaning_count[user_id] = self.user_meaning_count.get(user_id, 0) + 1
                
                # Auto-disable after 17 messages
                if self.user_meaning_count[user_id] >= 17:
                    self.user_meaning_enabled[user_id] = False
                    
                self._save_user_prefs(force=True)
                await self.send_long_message(chat_id, f"🌱 {response}")
            except Exception as e:
                print(f"[DAILY MEANING] Error for user {user_id}: {e}")            
            # Hint on 2nd, 7th... time
            count = self.user_meaning_count[user_id]
            if count == 2 or (count > 2 and (count - 2) % 5 == 0):
                hint = (
                    "You can disable daily meanings with /meaning_gone" 
                    if lang == "en" else 
                    "Вы можете отключить ежедневные смыслы командой /meaning_gone"
                )
                await self.send_long_message(chat_id, hint)
    async def send_long_message(self, chat_id: int, text: str, parse_mode: str = None, reply_markup=None):
        """Split long messages into chunks of 4000 characters."""
        if len(text) <= 4000:
            await self.bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            # Attach keyboard only to the last chunk so it stays visible after long previews.
            chunks = self._split_text(text, 4000)
            for idx, chunk in enumerate(chunks):
                kwargs = {"parse_mode": parse_mode}
                if reply_markup is not None and idx == len(chunks) - 1:
                    kwargs["reply_markup"] = reply_markup
                await self.bot.send_message(chat_id, chunk, **kwargs)

    async def _stream_chat(self, therapist, user_input):
        """Запускает синхронный chat_stream в отдельном потоке и отдаёт чанки в async."""
        loop = asyncio.get_event_loop()
        queue = asyncio.Queue()

        def _producer():
            try:
                for chunk in therapist.chat_stream(user_input):
                    loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        producer = threading.Thread(target=_producer, daemon=True)
        producer.start()
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "chunk":
                    if isinstance(payload, str) and re.match(r"^Ошибка(?::|\s)", payload):
                        raise RuntimeError(payload)
                    yield payload
                elif kind == "error":
                    raise RuntimeError(payload)
                else:
                    break
        finally:
            # The synchronous generator can still mutate history after cancellation.
            # Keep the user's lock until the producer has actually stopped.
            await asyncio.to_thread(producer.join)

    async def _stream_generic(self, sync_gen_func, *args):
        """Запускает синхронный генератор (sync_gen_func) в отдельном потоке
        и отдаёт чанки в async через очередь.
        
        sync_gen_func — функция, возвращающая sync-генератор (yield).
        """
        loop = asyncio.get_event_loop()
        queue = asyncio.Queue()

        def _producer():
            try:
                for chunk in sync_gen_func(*args):
                    loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        threading.Thread(target=_producer, daemon=True).start()

        while True:
            kind, payload = await queue.get()
            if kind == "chunk":
                if isinstance(payload, str) and re.match(r"^Ошибка(?::|\s)", payload):
                    raise RuntimeError(payload)
                yield payload
            elif kind == "error":
                raise RuntimeError(payload)
            elif kind == "done":
                break

    @asynccontextmanager
    async def _animate_loading_dots(
        self, message, interval: float = 0.52, *, stop_on_rate_limit: bool = False,
    ):
        """Анимирует индикатор ожидания (3 -> 2 -> 1 -> 2 -> 3 точки),
        пока модель генерирует ответ.

        stop_on_rate_limit is retained for callers; 429 always stops the dots.
        """
        state = _LoadingAnimationState()
        if message is None:
            yield state
            return
        stop_event = asyncio.Event()

        async def _runner():
            frames = ["..", ".", "..", "..."]
            idx = 0
            try:
                while not stop_event.is_set():
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval)
                        break
                    except (asyncio.TimeoutError, TimeoutError):
                        pass
                    if stop_event.is_set():
                        break
                    frame = frames[idx % len(frames)]
                    idx += 1
                    try:
                        await message.edit_text(frame)
                    except Exception as exc:
                        logger.warning(
                            "Telegram loading_edit failed: type=%s part=0 chars=%d",
                            type(exc).__name__, len(frame),
                        )
                        if isinstance(exc, TelegramRetryAfter):
                            state.rate_limited = True
                            state.retry_deadline = time.monotonic() + max(
                                0.0, float(exc.retry_after),
                            )
                            break
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(_runner())
        try:
            yield state
        finally:
            stop_event.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            # A 429 applies to the next request too, including an error notice
            # if generation failed. Do not continue the dots during this wait.
            if state.rate_limited:
                remaining = state.retry_deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)

    async def _smooth_stream(self, therapist, user_input, words_per_tick: int = 3, tick: float = 0.12):
        """Сглаживает стриминг: накапливает чанки от API и отдаёт
        последовательные префиксы текста до границ слов — так что
        edit_text(prefix) сразу показывает всё накопленное.

        Возвращает (полный_текст, список_префиксов).
        """
        full = ""
        shown = []

        async for chunk in self._stream_chat(therapist, user_input):
            full += chunk
            # Сколько префиксов нужно для words_per_tick слов в каждом
            words = re.findall(r'\S+', full)
            target = max(1, len(words) // words_per_tick)
            while len(shown) < target:
                n = (len(shown) + 1) * words_per_tick
                if n >= len(words):
                    shown.append(full)
                    break
                # Обрезаем строго по концу n-го слова
                match = list(re.finditer(r'\S+', full))[n - 1]
                shown.append(full[:match.end()])

        if not shown or shown[-1] != full:
            shown.append(full)
        return full, shown

    async def _smooth_stream_generic(self, chunk_source, words_per_tick: int = 3, tick: float = 0.12):
        """Универсальная версия _smooth_stream: принимает async-генератор чанков
        (не привязана к _stream_chat).

        Возвращает (полный_текст, список_префиксов).
        """
        chunks = []
        async for chunk in chunk_source:
            chunks.append(chunk)
        full = "".join(chunks)
        return full, self._typing_prefixes(full, words_per_tick)

    @staticmethod
    def _typing_prefixes(text: str, words_per_tick: int = 3) -> list[str]:
        """Build the typing simulation from the completed, unmodified text."""
        if words_per_tick <= 0:
            raise ValueError("words_per_tick must be positive")
        words = list(re.finditer(r"\S+", text))
        tokens = [(m.start(), m.end()) for m in _MARKUP_TOKEN.finditer(text)]
        prefixes = []
        for i in range(words_per_tick, len(words), words_per_tick):
            end = words[i - 1].end()
            for start, stop in tokens:
                if start < end < stop:
                    end = stop
                    break
            if not prefixes or len(prefixes[-1]) < end:
                prefixes.append(text[:end])
        if text and (not prefixes or prefixes[-1] != text):
            prefixes.append(text)
        return prefixes

    def _split_text(self, text: str, max_len: int = 4096) -> list[str]:
        """Split literal text losslessly, outside HTML-tag and entity tokens.

        Deliveries use parse_mode=None, so markup is never interpreted. Count
        UTF-16 units conservatively for Telegram, including supplementary emoji.
        """
        if max_len <= 0:
            raise ValueError("max_len must be positive")
        protected = [(m.start(), m.end()) for m in _MARKUP_TOKEN.finditer(text)]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start
            units = 0
            while end < len(text):
                size = 2 if ord(text[end]) > 0xFFFF else 1
                if units + size > max_len:
                    break
                units += size
                end += 1
            if end == len(text):
                chunks.append(text[start:end])
                break
            hard_end = end
            for token_start, token_end in protected:
                if token_start < end < token_end:
                    end = token_start
                    break
            if end <= start:
                # Literal delivery has parsing disabled. An oversized token
                # cannot fit in any message, so preserve it across messages.
                end = hard_end
            fragment = text[start:end]
            paragraph = fragment.rfind("\n\n")
            paragraph_end = start + paragraph + 2
            if (paragraph >= len(fragment) // 2
                    and not any(a < paragraph_end < b for a, b in protected)):
                end = start + paragraph + 2
            else:
                whitespace = list(re.finditer(r"\s+", fragment))
                if whitespace:
                    candidate = start + whitespace[-1].end()
                    if (candidate - start >= len(fragment) // 2
                            and not any(a < candidate < b for a, b in protected)):
                        end = candidate
            chunks.append(text[start:end])
            start = end
        return chunks

    async def _generate_user_summary(self, user_id: int, therapist: ExistentialTherapistBot) -> str:

        """Generate summary of user's conversation for internal admin use."""
        # Use saved recent messages (up to 16) instead of session history
        recent_messages = self.user_recent_messages.get(user_id, [])
        
        if not recent_messages or len(recent_messages) < 2:
            return "Недостаточно сообщений для резюме (нужно минимум 2)"
        
        # Build conversation text from saved messages (last 16)
        conversation = []
        for msg in recent_messages[-16:]:  # берём последние 16 сохранённых
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            prefix = "Клиент:" if role == "user" else "Терапевт:"
            conversation.append(f"{prefix} {content}")
        
        conv_text = "\n".join(conversation)
        
        # Get previous summary for context
        prev_summary = self.user_summaries.get(user_id, "")
        prev_context = f"\n\nПредыдущее резюме (контекст):\n{prev_summary}" if prev_summary else ""
        
        prompt = f"""Ты — супервизор экзистенциальной терапии. Дай лаконичное, конкретное резюме сессии.

Диалог (последние сообщения):
{conv_text}
{prev_context}

Правила:
- Пиши плотно: 2-3 коротких предложения, без воды и шаблонов.
- Только факты из диалога: тема, эмоция, динамика.
- Запрещены пустые формулы: "не выявлено", "начало диалога", "недостаточно материала", скобки-оправдания.
- Если сообщений мало — одно конкретное наблюдение, без оправданий.

Формат ответа (строго):
1) Что беспокоит сейчас
2) Эмоциональный тон
3) Что сдвинулось / застряло

Без заголовков, списков и markdown. Только связный текст."""
        
        try:
            summary = await asyncio.to_thread(
                therapist.generate_response,
                prompt,
                temporary_system_instruction=(
                    "Ты — супервизор. Пиши предельно лаконично и конкретно. "
                    "Максимум 3 коротких предложения. Без шаблонов и markdown."
                ),
                model=os.getenv("SUMMARY_MODEL") or self.llm_model
            )
            # Soft cap only as a safety net; laconic prompt should keep it short.
            return (summary or "").strip()[:2000]
        except Exception as e:
            return f"Ошибка генерации: {e}"


    async def _handle_void(self, message: types.Message):
        """Static /void effect — no LLM, no streaming, no message queue."""
        user_id = message.from_user.id
        lang = self.user_langs.get(user_id, DEFAULT_LANG)

        # Wide blank block (photo), pause, then the quote.
        # Text-only "blank" glyphs are accepted by Bot API but often collapse
        # to nothing in clients — a solid white PNG always reserves space.
        await self._send_void_spacer(message)
        await asyncio.sleep(3)
        await message.answer(t(lang, "void_msg"), parse_mode="HTML")

    @staticmethod
    def _void_spacer_png(width: int = 900, height: int = 520) -> bytes:
        """Build a solid near-white PNG without Pillow (stdlib only)."""
        import struct
        import zlib

        # Slightly off-white so dark/light themes still show a soft panel.
        r, g, b = 250, 250, 250
        raw = b"".join(b"\x00" + bytes([r, g, b]) * width for _ in range(height))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        return b"".join(
            [
                b"\x89PNG\r\n\x1a\n",
                chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
                chunk(b"IDAT", zlib.compress(raw, 9)),
                chunk(b"IEND", b""),
            ]
        )

    async def _send_void_spacer(self, message: types.Message) -> None:
        """Send a wide+tall empty panel that Telegram clients actually show."""
        try:
            photo = types.BufferedInputFile(
                self._void_spacer_png(),
                filename="void.png",
            )
            await message.answer_photo(photo)
            return
        except Exception as e:
            print(f"[VOID] Photo spacer failed: {type(e).__name__}: {e}")

        # Text fallback: force a real mono box with a barely-visible fullwidth dot
        # on each edge so clients cannot collapse the bubble to zero size.
        try:
            rows = 12
            width = 20
            # U+FF0E fullwidth full stop — nearly invisible at edges, keeps layout.
            edge = "\uff0e"
            fill = "\u3000"  # ideographic space
            lines = []
            for i in range(rows):
                if i in (0, rows - 1):
                    lines.append(edge + fill * (width - 2) + edge)
                else:
                    lines.append(fill * width)
            await message.answer("<pre>" + "\n".join(lines) + "</pre>", parse_mode="HTML")
        except Exception as e:
            print(f"[VOID] Text spacer failed: {type(e).__name__}: {e}")

    async def _telegram_request(
        self, operation, call, *args, part_index=0, max_attempts: int = 3, **kwargs,
    ):
        """Retry transient Telegram failures without logging message contents."""
        for attempt in range(1, max_attempts + 1):
            try:
                result = await call(*args, **kwargs)
                if result is None or result is False:
                    raise RuntimeError("Telegram did not confirm the request")
                return result
            except Exception as exc:
                if (operation == "edit_text" and isinstance(exc, TelegramBadRequest)
                        and "message is not modified" in str(exc).lower()):
                    return True
                retry_after = getattr(exc, "retry_after", None) if isinstance(exc, TelegramRetryAfter) else None
                transient = isinstance(exc, (
                    TelegramRetryAfter, TelegramNetworkError, TelegramServerError,
                    asyncio.TimeoutError, ConnectionError, OSError,
                ))
                delay = max(0.0, float(retry_after)) if retry_after is not None else 0.5 * (2 ** (attempt - 1))
                logger.warning(
                    "Telegram %s failed: type=%s attempt=%d part=%d chars=%d retry_delay=%s",
                    operation, type(exc).__name__, attempt, part_index,
                    len(args[0]) if args and isinstance(args[0], str) else 0,
                    delay if isinstance(exc, TelegramRetryAfter) or (
                        transient and attempt < max_attempts and delay <= 30
                    ) else None,
                )
                # Even when retries are exhausted, the caller may immediately
                # use another method (or send a notice). Respect the 429 first.
                if isinstance(exc, TelegramRetryAfter):
                    await asyncio.sleep(delay)
                if not transient or attempt == max_attempts or delay > 30:
                    raise
                if not isinstance(exc, TelegramRetryAfter):
                    await asyncio.sleep(delay)

    async def _show_delivery_error(self, message, sent, text):
        if sent is not None:
            try:
                await self._telegram_request("edit_text", sent.edit_text, text, parse_mode=None)
                return
            except Exception:
                pass  # _telegram_request logged the edit failure; try a new message.
        try:
            await self._telegram_request("send_message", message.answer, text, parse_mode=None)
        except Exception:
            # Do not allow a framework traceback to expose Telegram's error
            # message, which may contain private request text.
            raise DeliveryError() from None

    async def _new_delivery_placeholder(self, message):
        try:
            placeholder = await self._telegram_request(
                "send_message", message.answer, "...", parse_mode=None,
            )
            if callable(getattr(placeholder, "edit_text", None)):
                return placeholder
            logger.warning("Telegram placeholder has no edit method: part=0")
        except Exception:
            pass  # The failed request was logged; the answer can be sent directly.
        return None

    async def _deliver_response(
        self, message, sent, text, shown=None, *, words_per_tick: int = 3,
    ):
        """Deliver only confirmed text; raise with the confirmed prefix on failure."""
        lang = self.user_langs.get(message.from_user.id, DEFAULT_LANG)
        parts = self._split_text(text)
        delivered = ""
        for index, part in enumerate(parts):
            target = sent if index == 0 else None
            # The simulated typing animation operates on complete text, never
            # directly on model tokens or partially parsed Markdown.
            if target is None and index > 0 and shown:
                try:
                    target = await self._telegram_request(
                        "send_message", message.answer, "...", part_index=index, parse_mode=None,
                    )
                    if not callable(getattr(target, "edit_text", None)):
                        logger.warning("Telegram placeholder has no edit method: part=%d", index)
                        target = None
                except Exception:
                    target = None
            last = ""
            try:
                if target is not None:
                    previews = self._typing_prefixes(part, words_per_tick) if shown else [part]
                    for preview in previews:
                        try:
                            await self._telegram_request(
                                "edit_text", target.edit_text, preview,
                                part_index=index, parse_mode=None,
                                max_attempts=1 if shown and preview != part else 3,
                            )
                            last = preview
                        except Exception:
                            # Intermediate failures end animation. First try to
                            # complete the existing message, without deleting it.
                            if preview != part:
                                try:
                                    await self._telegram_request(
                                        "edit_text", target.edit_text, part,
                                        part_index=index, parse_mode=None,
                                    )
                                    last = part
                                except Exception:
                                    pass  # The failed request was logged.
                            if last != part:
                                remaining = part[len(last):]
                                if remaining:
                                    kwargs = {"reply_markup": get_main_keyboard(lang)} if index == len(parts) - 1 else {}
                                    await self._telegram_request(
                                        "send_message", message.answer, remaining,
                                        part_index=index, parse_mode=None, **kwargs,
                                    )
                                last = part
                            break
                        if shown:
                            await asyncio.sleep(0.12)
                    delivered += part
                else:
                    kwargs = {"reply_markup": get_main_keyboard(lang)} if index == len(parts) - 1 else {}
                    await self._telegram_request(
                        "send_message", message.answer, part, part_index=index, parse_mode=None, **kwargs,
                    )
                    delivered += part
            except Exception:
                raise DeliveryError(delivered + last) from None
        return delivered

    async def _handle_chat(self, message: types.Message, text: str, is_voice: bool = False):
        user_id = message.from_user.id
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        if (text or "").strip().startswith("/"):
            if user_id in self.message_queue and self.message_queue[user_id]:
                self.message_queue[user_id].pop(0)
            return
        async with self._get_user_lock(user_id):
            therapist = self._get_therapist(user_id)
            if hasattr(self, "music"):
                try:
                    store = self.music.existing_store()
                    if store:
                        store.invalidate_buttons(user_id)
                except Exception as exc:
                    logger.warning("Music button invalidation failed: type=%s", type(exc).__name__)
            previous = list(therapist.history)
            sent = None
            delivered = ""
            try:
                await self.bot.send_chat_action(chat_id=message.chat.id, action="typing")
                if is_voice:
                    worker = asyncio.create_task(asyncio.to_thread(therapist.chat, text, is_voice=True))
                    try:
                        response = await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        try:
                            await worker
                        except Exception as exc:
                            logger.warning("Cancelled chat worker failed: type=%s", type(exc).__name__)
                        raise
                    shown = None
                else:
                    sent = await self._new_delivery_placeholder(message)
                    async with self._animate_loading_dots(sent):
                        response, shown = await self._smooth_stream(therapist, text)
                if not response or not response.strip() or re.match(r"^Ошибка(?::|\s)", response):
                    raise ValueError("Empty or failed response")
                # Chat delivery uses parse_mode=None and performs no Markdown or
                # HTML conversion. Check exactly the literal string sent to TTS.
                if is_voice and not voice_response_fits(response):
                    logger.warning("Voice response exceeds character limit")
                    raise VoiceResponseTooLong()
                delivered = await self._deliver_response(message, sent, response, shown)
                if not delivered:
                    raise ValueError("Response was not delivered")
                therapist.history = previous + [Message(role="user", content=text), Message(role="assistant", content=delivered)]
                timestamp = datetime.now().isoformat()
                recent = self.user_recent_messages.setdefault(user_id, [])
                recent.extend([{"role": "user", "content": text, "timestamp": timestamp},
                               {"role": "assistant", "content": delivered, "timestamp": timestamp}])
                self.user_recent_messages[user_id] = recent[-16:]
                self.user_message_counts[user_id] = self.user_message_counts.get(user_id, 0) + 1
                if message.from_user.username:
                    self.user_usernames[user_id] = message.from_user.username
                self.user_consecutive_messages[user_id] = 0
                if not self._save_user_prefs(force=True):
                    try:
                        await message.answer(
                            "This conversation could not be saved and may be lost after restart."
                            if lang == "en" else
                            "Не удалось сохранить разговор; после перезапуска он может пропасть."
                        )
                    except Exception:
                        pass
                if is_voice:
                    audio_path = None
                    try:
                        if delivered != response:
                            raise ValueError("Voice response was not fully delivered")
                        temp_dir = Path("temp_audio")
                        temp_dir.mkdir(exist_ok=True)
                        audio_path = temp_dir / f"response_{user_id}.mp3"
                        if not voice_response_fits(delivered):
                            raise ValueError("Voice response exceeds character limit")
                        worker = asyncio.create_task(
                            asyncio.to_thread(therapist.generate_speech, delivered, str(audio_path))
                        )
                        try:
                            async with ChatActionSender.record_voice(
                                bot=_SafeChatActionBot(self.bot), chat_id=message.chat.id,
                                message_thread_id=getattr(message, "message_thread_id", None),
                                interval=4.5,
                            ):
                                result = await asyncio.shield(worker)
                        except asyncio.CancelledError:
                            # Stop the indicator immediately, but keep the user's lock
                            # until the synchronous worker can no longer write its file.
                            try:
                                await worker
                            except Exception as exc:
                                logger.warning("Cancelled voice worker failed: type=%s", type(exc).__name__)
                            finally:
                                try:
                                    audio_path.unlink(missing_ok=True)
                                except OSError:
                                    logger.warning("Cancelled voice cleanup failed")
                            raise
                        if not result or str(result).startswith("Ошибка") or not Path(result).is_file():
                            raise RuntimeError("Voice generation failed")
                        await message.answer_voice(types.FSInputFile(result))
                    except Exception as exc:
                        logger.warning("Voice generation or delivery failed: type=%s", type(exc).__name__)
                        try:
                            await message.answer(t(lang, "voice_delivery_failed"), parse_mode=None)
                        except Exception as notice_exc:
                            logger.warning("Voice error notice failed: type=%s", type(notice_exc).__name__)
                    finally:
                        if audio_path is not None:
                            try:
                                audio_path.unlink(missing_ok=True)
                            except OSError:
                                logger.warning("Voice cleanup failed")
                if self.user_message_counts[user_id] >= 16:
                    summary = await self._generate_user_summary(user_id, therapist)
                    if not summary.startswith("Ошибка генерации:"):
                        self.user_summaries[user_id] = summary
                        self.user_message_counts[user_id] = 0
                        self._save_user_prefs(force=True)
                if hasattr(self, "music"):
                    await self.music.maybe_offer(message, therapist, user_id)
            except asyncio.CancelledError:
                if not delivered:
                    therapist.history = previous
                raise
            except VoiceResponseTooLong:
                therapist.history = previous
                await self._show_delivery_error(message, sent, t(lang, "voice_length_failed"))
            except DeliveryError as exc:
                therapist.history = previous
                if exc.delivered:
                    therapist.history = previous + [
                        Message(role="user", content=text),
                        Message(role="assistant", content=exc.delivered),
                    ]
                    timestamp = datetime.now().isoformat()
                    recent = self.user_recent_messages.setdefault(user_id, [])
                    recent.extend([
                        {"role": "user", "content": text, "timestamp": timestamp},
                        {"role": "assistant", "content": exc.delivered, "timestamp": timestamp},
                    ])
                    self.user_recent_messages[user_id] = recent[-16:]
                    self.user_message_counts[user_id] = self.user_message_counts.get(user_id, 0) + 1
                    if message.from_user.username:
                        self.user_usernames[user_id] = message.from_user.username
                    self.user_consecutive_messages[user_id] = 0
                    self._save_user_prefs(force=True)
                await self._show_delivery_error(
                    message, None,
                    t(lang, "error_delivery_partial" if exc.delivered else "error_delivery"),
                )
            except Exception:
                if not delivered:
                    therapist.history = previous
                    await self._show_delivery_error(message, sent, t(lang, "error_llm"))
            finally:
                if user_id in self.message_queue and self.message_queue[user_id]:
                    self.message_queue[user_id].pop(0)

    async def _handle_voice(self, message: types.Message):
        """Обработка голосовых сообщений."""
        user_id = message.from_user.id
        
        # Скачиваем файл
        file_id = message.voice.file_id
        file = await self.bot.get_file(file_id)
        file_path = file.file_path
        
        # Создаем временную папку
        temp_dir = Path("temp_audio")
        temp_dir.mkdir(exist_ok=True)
        local_path = temp_dir / f"{file_id}.ogg"
        
        await self.bot.download_file(file_path, local_path)
        
        # Транскрибируем
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        await message.answer(t(lang, "listening"))

        therapist = self._get_therapist(user_id)

        # Выполняем в отдельном потоке, чтобы не блокировать бота
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, therapist.transcribe_audio, str(local_path))

        # Удаляем файл
        try:
            os.remove(local_path)
        except:
            pass

        if text.startswith("Ошибка"):
            await message.answer(t(lang, "transcribe_failed", error=text))
            return

        # Попытка определить язык по транскрибированному тексту
        try:
            code, prob = detect_language(text)
            if code and code.startswith("ru") and prob > 0.6:
                self.user_langs[user_id] = "ru"
                if user_id in self.sessions:
                    sess = self.sessions[user_id]
                    sess.language = "ru"
                    sess.system_prompt = sess._load_system_prompt()
                try:
                    self._save_user_prefs(force=True)
                except Exception:
                    pass
            else:
                if user_id not in self.user_langs:
                    self.user_langs[user_id] = DEFAULT_LANG
                    if user_id in self.sessions:
                        sess = self.sessions[user_id]
                        sess.language = DEFAULT_LANG
                        sess.system_prompt = sess._load_system_prompt()
                    try:
                        self._save_user_prefs(force=True)
                    except Exception:
                        pass
        except Exception:
            pass

        await message.answer(t(lang, "you_said", text=text), parse_mode="HTML")

        # Обрабатываем как текст
        await self._process_text_message(message, text, is_voice=True)

    async def _handle_photo(self, message: types.Message):
        """Обработка изображений."""
        user_id = message.from_user.id
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        caption = message.caption or t(lang, "image_default_caption")
        
        # Получаем самое большое фото
        photo = message.photo[-1]
        file_id = photo.file_id
        
        # Скачиваем файл
        file = await self.bot.get_file(file_id)
        file_path = file.file_path
        
        # Скачиваем в память
        import io
        import base64
        
        downloaded_file = await self.bot.download_file(file_path)
        
        # Кодируем в base64
        base64_image = base64.b64encode(downloaded_file.read()).decode('utf-8')
        image_url = f"data:image/jpeg;base64,{base64_image}"

        therapist = self._get_therapist(user_id)

        sent = await message.answer("...")
        try:
            async with self._animate_loading_dots(sent):
                source = self._stream_generic(therapist.analyze_image_stream, image_url, caption)
                response, shown = await self._smooth_stream_generic(source)
            if not response.strip():
                raise ValueError("Empty response")
            await self._deliver_response(message, sent, strip_markdown(response),
                                         [strip_markdown(part) for part in shown])
        except DeliveryError as exc:
            await self._show_delivery_error(
                message, None,
                t(lang, "error_delivery_partial" if exc.delivered else "error_delivery"),
            )
        except Exception:
            await self._show_delivery_error(message, sent, t(lang, "error_llm"))

    async def _check_and_notify_updates(self, delay=True, retry_generation=False):
        """Restore a pending draft or check changed hashes without losing it."""
        try:
            if delay:
                await asyncio.sleep(2.0)
            if not self.admin_id:
                print("[UPDATE CHECK] ADMIN_ID is not configured; leaving cache unchanged")
                return
            async with self.update_check_lock:
                pending = self.update_cache.prepare()
                if not pending:
                    print("[UPDATE CHECK] No changes detected")
                    return "idle"
                if pending["status"] == "sending":
                    await self._process_update_broadcast()
                    return "sending"
                if pending["status"] == "generating" or (
                    retry_generation and pending.get("reason", "").startswith("Не удалось")
                    and not any(pending["notes"].values())
                ):
                    therapist = self._get_therapist(self.admin_id)
                    pending = await asyncio.to_thread(self.update_cache.generate, therapist, pending)
                if not pending:
                    return "idle"
                if pending["status"] == "sending":
                    await self._process_update_broadcast()
                    return "sending"
                return await self._send_update_preview(self.admin_id, pending["notes"])
        except Exception as exc:
            print(f"[UPDATE CHECK] Failed: {type(exc).__name__}")
            return "error"



    async def run(self):
        # Get bot info for username
        try:
            me = await self.bot.get_me()
            print(f"Bot @{me.username} started")
        except Exception as e:
            print(f"Could not get bot username: {e}")
        
        # Check for updates in background so polling starts immediately
        asyncio.create_task(self._check_and_notify_updates())
        
        # Start background tasks
        self.daily_meaning_task = asyncio.create_task(self._daily_meaning_loop())
        
        await self.dp.start_polling(self.bot)

        
        # Cancel background tasks on shutdown
        if hasattr(self, 'daily_meaning_task'):
            self.daily_meaning_task.cancel()
            try:
                await self.daily_meaning_task
            except asyncio.CancelledError:
                pass

    async def _daily_meaning_loop(self):
        """Background task for sending daily meanings strictly every 24h."""
        while True:
            try:
                # Check every minute
                await asyncio.sleep(60)
                for user_id in list(self.user_langs.keys()):
                    if not self._has_consent(user_id):
                        continue
                    await self._check_and_send_daily_meaning(user_id, user_id)
            except Exception as e:
                print(f"Error in _daily_meaning_loop: {e}")
                await asyncio.sleep(10)


async def main():
    """Точка входа."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Telegram бот-терапевт")
    parser.add_argument("--model", default=None, help="Модель LLM для чата")
    parser.add_argument("--analysis-model", default=None, help="Модель LLM для анализов")
    parser.add_argument("--no-rag", action="store_true", help="Отключить RAG")
    args = parser.parse_args()
    
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        print("Ошибка: установите TELEGRAM_BOT_TOKEN в .env")
        print("Получить токен: @BotFather в Telegram")
        return
    
    bot = TelegramTherapistBot(
        telegram_token=telegram_token,
        llm_model=args.model,
        llm_analysis_model=args.analysis_model,
        llm_api_key=os.getenv("OPENAI_API_KEY"),
        llm_api_base=os.getenv("OPENAI_API_BASE"),
        use_rag=not args.no_rag
    )
    
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())

