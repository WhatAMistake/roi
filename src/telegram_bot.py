"""
Telegram бот для экзистенциальной терапии.
"""

import os
import asyncio
import html
import re
import threading
import time
from pathlib import Path
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# Encryption for sensitive user data
try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    print("cryptography не установлен. Установите: pip install cryptography")

def get_encryption_key() -> Optional[bytes]:
    """Get encryption key from environment."""
    key = os.getenv("USER_PREFS_ENCRYPTION_KEY")
    if not key:
        print("[ENCRYPTION] No USER_PREFS_ENCRYPTION_KEY found in environment")
        return None
    
    # Strip whitespace and quotes (both single and double)
    key = key.strip().strip('"').strip("'").strip()
    
    print(f"[ENCRYPTION] Raw key from env (length: {len(key)} chars)")
    print(f"[ENCRYPTION] Key starts with: {key[:10]}... ends with: ...{key[-10:]}")
    
    try:
        # Validate key format (Fernet keys are 32-byte base64-encoded, 44 chars with padding)
        import base64
        # Add padding if missing
        padding_needed = 4 - len(key) % 4
        if padding_needed != 4:
            key = key + '=' * padding_needed
        
        decoded = base64.urlsafe_b64decode(key)
        if len(decoded) == 32:
            print(f"[ENCRYPTION] Key validated successfully (32 bytes decoded)")
            return key.encode('utf-8')
        else:
            print(f"[ENCRYPTION] Invalid key length: {len(decoded)} bytes (expected 32)")
            return None
    except Exception as e:
        print(f"[ENCRYPTION] Invalid key format: {e}")
        print(f"[ENCRYPTION] Key was: {key[:20]}...")
        return None



def encrypt_data(data: str, key: Optional[bytes]) -> str:
    """Encrypt data if key is available, otherwise return as-is."""
    if not key or not CRYPTO_AVAILABLE:
        if not CRYPTO_AVAILABLE:
            print("[ENCRYPTION] cryptography not available")
        elif not key:
            print("[ENCRYPTION] No encryption key provided")
        return data
    try:
        f = Fernet(key)
        encrypted = f.encrypt(data.encode('utf-8'))
        result = encrypted.decode('utf-8')
        print(f"[ENCRYPTION] Data encrypted successfully ({len(data)} -> {len(result)} chars)")
        return result
    except Exception as e:
        print(f"[ENCRYPTION] Encryption failed: {e}")
        import traceback
        traceback.print_exc()
        return data


def decrypt_data(data: str, key: Optional[bytes]) -> str:
    """Decrypt data if key is available, otherwise return as-is."""
    if not key or not CRYPTO_AVAILABLE:
        return data
    try:
        f = Fernet(key)
        # Check if data looks like Fernet token (starts with 'gAAAA')
        if not data.strip().startswith('gAAAA'):
            print("[ENCRYPTION] Data doesn't appear to be encrypted (not a Fernet token)")
            return data
        decrypted = f.decrypt(data.encode('utf-8'))
        return decrypted.decode('utf-8')
    except Exception as e:
        print(f"[ENCRYPTION] Decryption failed: {e}")
        return data



try:
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.filters import Command
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
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
    Bot = Dispatcher = Command = None
    ReplyKeyboardMarkup = _DummyMarkup
    KeyboardButton = _DummyKeyboardButton
    ReplyKeyboardBuilder = _DummyReplyKeyboardBuilder

from therapist_bot import ExistentialTherapistBot
from lang_utils import detect_language
from i18n import t
from code_reviewer import check_and_generate_changelog, save_current_hashes
import re


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

import sys
# Import init_cache functionality for auto-updating after restart
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
# Add workspace root so `app` package is importable when launched via run_telegram.py
sys.path.insert(0, str(Path(__file__).parent.parent))
from init_code_cache import init_cache

# Default language when nothing is detected or user's Telegram locale is unsupported
DEFAULT_LANG = os.getenv("DEFAULT_LANG", "en")



# Клавиатуры
def get_main_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Main keyboard localized by `lang`."""
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=t(lang, "button_assoc")))
    builder.add(KeyboardButton(text=t(lang, "button_analyze")))
    builder.add(KeyboardButton(text=t(lang, "button_filmframe")))
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
        llm_model: str = "deepseek-v4-pro",
        llm_analysis_model: str = "deepseek-v4-pro",
        llm_api_key: Optional[str] = None,
        llm_api_base: Optional[str] = None,
        use_rag: bool = True,
        ask_question_prob: Optional[float] = None
    ):

        if not AIogram_AVAILABLE:
            raise RuntimeError("aiogram не установлен")
        
        self.telegram_token = telegram_token
        self.llm_model = llm_model
        self.llm_analysis_model = llm_analysis_model
        
        # Инициализируем Telegram бота
        self.bot = Bot(token=telegram_token)
        self.dp = Dispatcher()
        
        # Filmframe state
        self.filmframe_state: dict[int, dict] = {}

        # Хранилище сессий (user_id -> therapist_bot)
        self.sessions: dict[int, ExistentialTherapistBot] = {}
        
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
        # probability to ask a clarifying question per-response (defaults to env or 0.2)
        try:
            self.ask_question_prob = ask_question_prob if ask_question_prob is not None else float(os.getenv("OPENAI_ASK_QUESTION_PROB", 0.37))
        except Exception:
            self.ask_question_prob = 0.37
        
        # ID администратора для фидбека
        self.admin_id = int(os.getenv("ADMIN_ID", "282208693"))

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
        self._prefs_loaded = False
        self._load_user_prefs()


        # Время запуска бота для фильтрации старых сообщений
        import time
        self.start_time = time.time()
        self.processed_flood_users = set()

        self._register_handlers()

    
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
        return self.sessions[user_id]

    def _load_user_prefs(self):
        """Load user preferences from file."""
        import json
        from datetime import datetime
        
        # Log encryption status
        encryption_key = get_encryption_key()
        if CRYPTO_AVAILABLE and encryption_key:
            print("[LOAD PREFS] Encryption: ENABLED (key is set)")
        elif CRYPTO_AVAILABLE and not encryption_key:
            print("[LOAD PREFS] Encryption: DISABLED - set USER_PREFS_ENCRYPTION_KEY in .env to enable")
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
            
            # Check if decryption failed
            if encryption_key and CRYPTO_AVAILABLE and decrypted_content == raw_content:
                print("[LOAD PREFS] WARNING: Decryption failed - possible key mismatch")
                print("[LOAD PREFS] Attempting to parse as unencrypted...")
            
            data = json.loads(decrypted_content)

            
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
            return
        
        # Optimization: Throttle saving to once every 60 seconds unless forced
        import time
        now = time.time()
        last_save = getattr(self, '_last_save_time', 0)
        if not force and (now - last_save < 60):
            return
        self._last_save_time = now
        
        try:
            import json

            encryption_key = get_encryption_key()
            
            # Warn if saving unencrypted
            if not (CRYPTO_AVAILABLE and encryption_key):
                print("[SAVE PREFS] WARNING: Saving UNENCRYPTED data - set USER_PREFS_ENCRYPTION_KEY to enable encryption")
            
            payload = {

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
            temp_path = self.prefs_path.with_suffix('.json.tmp')
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(encrypted_content)
            
            # Atomic rename
            temp_path.replace(self.prefs_path)
            
            print(f"[SAVE PREFS] Saved successfully to {self.prefs_path}")
        except Exception as e:
            print(f"[SAVE PREFS] Error saving prefs: {e}")
            import traceback
            traceback.print_exc()




    
    def _register_handlers(self):
        """Регистрация обработчиков."""
        
        @self.dp.message(Command("start"))
        async def cmd_start(message: types.Message):
            await self._handle_start(message)
        
        @self.dp.message(Command("help"))
        async def cmd_help(message: types.Message):
            await self._handle_help(message)

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
                    self._save_user_prefs()
                await message.answer(t(lang, "askprob_reset", prob=str(self.ask_question_prob)))
                return
            try:
                val = float(args)
                if val < 0 or val > 1:
                    raise ValueError()
            except Exception:
                await message.answer(t(lang, "askprob_invalid"))
                return
            # set per-user override
            self.user_ask_prob[user_id] = val
            if user_id in self.sessions:
                sess = self.sessions[user_id]
                sess.ask_question_prob = val
            self._save_user_prefs()
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
                try:
                    self._save_user_prefs()
                except Exception:
                    pass
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
            try:
                self._save_user_prefs()
            except Exception:
                pass
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
                self._save_user_prefs()
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
            self._save_user_prefs()
            
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
            print(f"[ADMIN] Recovered {recovered_count}/{total} usernames, failed: {failed_count}")

        @self.dp.message(Command("look"))
        async def cmd_look(message: types.Message):
            await self._handle_look(message)

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
        
        @self.dp.message(Command("analyze"))
        async def cmd_analyze(message: types.Message):
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
            prompt = f"""Клиент прислал стикер (эмодзи: {emoji}).
            
            Твоя задача:
            1. Не пытайся угадать, что на картинке.
            2. Дай очень короткий, теплый и эмпатичный отклик (1 предложение).
            3. Просто покажи, что ты рядом и принимаешь эту эмоцию.
            
            Примеры:
            - "Иногда слов действительно недостаточно."
            - "Я здесь. Я слышу эту эмоцию."
            - "Этот образ говорит о многом."
            
            Стиль: Ирвин Ялом. Тихий, принимающий, без пафоса."""
            
            response = therapist.generate_response(prompt, temporary_system_instruction=prompt, use_analysis_model=True)
            await message.answer(response)

        @self.dp.message(Command("shoot"))
        async def cmd_shoot(message: types.Message):
            await self._handle_meta(message)

        @self.dp.message(Command("remarque"))
        async def cmd_remarque(message: types.Message):
            user_id = message.from_user.id
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            
            # Проверяем, есть ли история
            if user_id not in self.sessions or not self.sessions[user_id].history:
                await message.answer(t(lang, "story_too_short"))
                return
                
            await message.answer(t(lang, "meta_analyzing"), parse_mode="HTML")
            
            therapist = self._get_therapist(user_id)
            # Генерируем метафору через специальный запрос к LLM
            meta_prompt = t(lang, "meta_prompt")
            
            # Временно добавляем системную инструкцию для генерации метафоры
            response = therapist.generate_response(meta_prompt, temporary_system_instruction=meta_prompt, use_analysis_model=True)
            
            await message.answer(f"✨ <b>{('Метафора' if lang == 'ru' else 'Metaphor')}:</b>\n\n{html.escape(response)}", parse_mode="HTML")

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
            self._save_user_prefs()


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
            self._save_user_prefs()
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
    async def _handle_look(self, message: types.Message):
        """Обработка /look <user_id> (только для админа)."""
        admin_id = message.from_user.id

        if admin_id != self.admin_id:
            return  # Молча игнорируем не-админа

        # Парсим аргументы
        parts = (message.text or "").split(None, 1)
        if len(parts) < 2:
            await message.answer("Использование: /look <user_id>")
            return

        try:
            target_user_id = int(parts[1].strip())
        except ValueError:
            await message.answer("user_id должен быть числом")
            return

        # Ищем пользователя в сохранённых данных (не только в активных сессиях)
        if target_user_id not in self.user_langs:
            await message.answer(f"Пользователь {target_user_id} не найден")
            return

        # Данные запрошенного пользователя
        target_lang = self.user_langs.get(target_user_id, DEFAULT_LANG)
        target_username = self.user_usernames.get(target_user_id, "—")
        target_recent_messages = self.user_recent_messages.get(target_user_id, [])

        # Для доминирующей данности смотрим активную сессию, если есть
        history_count = len(target_recent_messages)
        dominant = "нет данных"
        if target_user_id in self.sessions:
            therapist_session = self.sessions[target_user_id]
            if getattr(therapist_session, "last_dominant_given", None):
                dominant = therapist_session.last_dominant_given

        # Генерируем свежее ревью на основе сохранённых сообщений (даже без активной сессии)
        fresh_summary = None
        if len(target_recent_messages) >= 2:
            therapist = self._get_therapist(target_user_id)
            fresh_summary = await self._generate_user_summary(target_user_id, therapist)
            if not fresh_summary.startswith("Ошибка генерации:"):
                self.user_summaries[target_user_id] = fresh_summary
                self._save_user_prefs(force=True)

        # Получаем резюме (свежее или из файла)
        saved_summary = fresh_summary or self.user_summaries.get(target_user_id, "Резюме ещё не сгенерировано")
        msg_count = self.user_message_counts.get(target_user_id, 0)
        to_next_summary = max(0, 16 - msg_count)

        # Экранируем поля для безопасного HTML
        safe_username = html.escape(target_username)
        safe_dominant = html.escape(dominant)
        safe_summary = html.escape(saved_summary)

        summary_lines = [
            f"<b>Резюме пользователя {target_user_id}</b>",
            "",
            f"Username: @{safe_username if safe_username != '—' else '—'}",
            f"Язык: {target_lang}",
            f"Сообщений в памяти (посл.16): {history_count}",
            f"До следующего авто-резюме: {to_next_summary} сообщений",
            f"Доминирующая данность: {safe_dominant}",
            "",
            "<b>Ревью:</b>",
            f"{safe_summary[:1500]}{'...' if len(safe_summary) > 1500 else ''}",
        ]

        await message.answer("\n".join(summary_lines), parse_mode="HTML")

        # TXT dump of recent messages (if any)
        if not target_recent_messages:
            await message.answer("Нет сохранённых сообщений для этого пользователя.")
            return

        now = datetime.now()
        username_str = f"@{target_username}" if target_username and target_username != "—" else "—"
        txt_lines = [
            "USER RECENT MESSAGES EXPORT",
            f"generated_at={now.isoformat()}",
            f"user_id={target_user_id}",
            f"username={username_str}",
            f"lang={target_lang}",
            f"messages_count={history_count}",
            f"dominant_given={dominant}",
            "",
            "--- MESSAGES (oldest -> newest) ---",
            "",
        ]

        for i, msg in enumerate(target_recent_messages[-16:], start=1):
            role = msg.get("role", "unknown")
            content = (msg.get("content") or "").replace("\r\n", "\n")
            ts = msg.get("timestamp", "—")
            role_label = "USER" if role == "user" else ("ASSISTANT" if role == "assistant" else role.upper())
            txt_lines.append(f"[{i}] {ts} | {role_label}")
            txt_lines.append(content)
            txt_lines.append("")

        if saved_summary and not str(saved_summary).startswith("Ошибка генерации:"):
            txt_lines.extend([
                "--- REVIEW ---",
                "",
                str(saved_summary),
                "",
            ])

        export_dir = Path(__file__).parent.parent / "data"
        export_dir.mkdir(exist_ok=True)
        export_path = export_dir / f"look_{target_user_id}_{now.strftime('%Y%m%d_%H%M%S')}.txt"

        try:
            export_path.write_text("\n".join(txt_lines), encoding="utf-8")
            await message.answer_document(
                types.FSInputFile(str(export_path)),
                caption=f"📄 Последние {history_count} сообщений пользователя {target_user_id}",
            )
        except Exception as e:
            await message.answer(f"Не удалось отправить TXT с сообщениями: {e}")
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

    def _format_update_preview(self, changelogs: dict) -> str:
        """Build HTML preview of RU/EN changelogs for admin confirmation."""
        preview_limit = 1200
        changelog_ru = (changelogs or {}).get("ru") or ""
        changelog_en = (changelogs or {}).get("en") or ""

        def _clip(text: str) -> str:
            if not text:
                return "—"
            safe = html.escape(text)
            if len(text) > preview_limit:
                return html.escape(text[:preview_limit]) + "..."
            return safe

        return "\n".join([
            "📋 <b>Предпросмотр обновления</b>",
            "",
            f"Получателей: <b>{len(self.user_langs)}</b>",
            "",
            "<b>RU:</b>",
            _clip(changelog_ru),
            "",
            "<b>EN:</b>",
            _clip(changelog_en),
            "",
            "Отправить?",
            "• <b>да</b> — разослать как есть",
            "• <b>править</b> — отредактировать RU, затем EN",
            "• <b>нет</b> — отменить рассылку",
        ])

    async def _send_update_preview(self, chat_id: int, changelogs: dict) -> None:
        """Send update preview and put admin into confirmation state."""
        self.pending_update_changelogs = {
            "ru": (changelogs or {}).get("ru") or "",
            "en": (changelogs or {}).get("en") or "",
        }
        self.user_states[chat_id] = "update_confirm"
        await self.send_long_message(
            chat_id,
            self._format_update_preview(self.pending_update_changelogs),
            parse_mode="HTML",
            reply_markup=get_update_confirm_keyboard(),
        )

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

        if choice in _UPDATE_CONFIRM_YES:
            await message.answer(
                "✅ Начинаю рассылку обновлений...",
                reply_markup=get_main_keyboard(self.user_langs.get(user_id, DEFAULT_LANG)),
            )
            await self._process_update_broadcast()
            return

        if choice in _UPDATE_CONFIRM_EDIT:
            await self._start_update_edit(message)
            return

        if choice in _UPDATE_CONFIRM_NO:
            self._clear_pending_update(user_id)
            await message.answer(
                "❌ Рассылка обновлений отменена",
                reply_markup=get_main_keyboard(self.user_langs.get(user_id, DEFAULT_LANG)),
            )
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

        edited = (text or "").strip()
        if not edited:
            await message.answer(
                "Текст не может быть пустым. Пришлите полный changelog или нажмите отмену.",
                reply_markup=get_cancel_keyboard("ru"),
            )
            return

        if len(edited) > 3500:
            await message.answer(
                "❌ Слишком длинный текст (максимум 3500 символов). Сократите и отправьте снова.",
                reply_markup=get_cancel_keyboard("ru"),
            )
            return

        if state == "update_edit_ru":
            self.pending_update_changelogs["ru"] = edited
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
        await message.answer("✅ EN сохранён. Обновлённый предпросмотр:")
        await self._send_update_preview(user_id, self.pending_update_changelogs)

    async def _process_update_broadcast(self):
        """Выполнение рассылки обновлений после подтверждения админа."""
        if not hasattr(self, 'pending_update_changelogs'):
            return

        changelogs = self.pending_update_changelogs
        del self.pending_update_changelogs
        if self.user_states.get(self.admin_id, "").startswith("update_"):
            self.user_states[self.admin_id] = "chat"

        # Статистика
        sent_count = 0
        failed_count = 0
        failed_users = []

        # Рассылка всем пользователям
        for user_id in list(self.user_langs.keys()):
            try:
                # Get user's preferred language
                user_lang = self.user_langs.get(user_id, DEFAULT_LANG)

                # Select appropriate changelog
                if user_lang == "ru" and changelogs.get("ru"):
                    changelog = changelogs["ru"]
                elif user_lang == "en" and changelogs.get("en"):
                    changelog = changelogs["en"]
                else:
                    # Fallback to available changelog
                    changelog = changelogs.get("ru") or changelogs.get("en") or "Internal updates."

                # Build localized message with header and footer
                header = t(user_lang, "update_notification_header")
                footer = t(user_lang, "update_notification_footer")
                # Escape body so admin edits can't break HTML parse_mode
                localized_changelog = f"{header}{html.escape(str(changelog))}{footer}"

                await self.send_long_message(
                    user_id,
                    localized_changelog,
                    parse_mode="HTML"
                )
                sent_count += 1

            except Exception as e:
                failed_count += 1
                failed_users.append(str(user_id))
                print(f"[UPDATE BROADCAST] Failed to send to {user_id}: {e}")

        # Формируем отчёт
        report_lines = [
            f"✅ <b>Рассылка обновлений завершена</b>",
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
        print(f"[UPDATE BROADCAST] Admin sent update broadcast to {sent_count}/{len(self.user_langs)} users")

        # Re-init cache after broadcast
        init_cache()
        # Отправляем отчёт админу
        await self.send_long_message(
            self.admin_id,
            "\n".join(report_lines),
            parse_mode="HTML",
            reply_markup=get_main_keyboard(self.user_langs.get(self.admin_id, DEFAULT_LANG)),
        )
    async def _handle_reset(self, message: types.Message):
        user_id = message.from_user.id
        
        if user_id in self.sessions:
            self.sessions[user_id].reset()
        
        # Clear message counter, recent messages, and summary for this user
        self.user_message_counts[user_id] = 0
        self.user_recent_messages[user_id] = []
        self.user_summaries[user_id] = ""
        
        self.user_states[user_id] = "chat"
        lang = self.user_langs.get(user_id, DEFAULT_LANG)

        await message.answer(
            t(lang, "reset_confirm"),
            reply_markup=get_main_keyboard(lang)
        )


    
    async def _handle_meta(self, message: types.Message):
        """Film-frame entry point."""
        from app.features.filmframe.handlers import start_filmframe
        await start_filmframe(self, message)

    async def _handle_remarque(self, message: types.Message):
        """Old /meta behavior: existential metaphor."""
        user_id = message.from_user.id
        lang = self.user_langs.get(user_id, DEFAULT_LANG)

        if user_id not in self.sessions or not self.sessions[user_id].history:
            await message.answer(t(lang, 'story_too_short'))
            return

        await message.answer(t(lang, 'meta_analyzing'), parse_mode='HTML')
        therapist = self._get_therapist(user_id)
        meta_prompt = t(lang, 'meta_prompt')
        response = therapist.generate_response(meta_prompt, temporary_system_instruction=meta_prompt, use_analysis_model=True)
        meta_label = 'Метафора' if lang == 'ru' else 'Metaphor'
        await message.answer(f'✨ <b>{meta_label}:</b>\n\n{html.escape(response)}', parse_mode='HTML')

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
        # Cancel only drops the update draft when admin is inside that flow
        if user_id == self.admin_id and state.startswith("update_"):
            if hasattr(self, "pending_update_changelogs"):
                del self.pending_update_changelogs

        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        await message.answer(
            t(lang, "action_cancelled"),
            reply_markup=get_main_keyboard(lang)
        )
    
    async def _process_text_message(self, message: types.Message, text: str, is_voice: bool = False):
        """Обработка текстового сообщения (или распознанного голоса)."""
        user_id = message.from_user.id
        # Intercept localized cancel texts so they aren't processed as content while in a special state
        t_ru_cancel = t('ru', 'button_cancel')
        t_en_cancel = t('en', 'button_cancel')
        if text and text.strip() in (t_ru_cancel, t_en_cancel, '/cancel', '❌ Отмена', 'Cancel'):
            await self._handle_cancel(message)
            # This path is reached via the catch-all handler, which enqueues first.
            if user_id in self.message_queue and self.message_queue[user_id]:
                self.message_queue[user_id].pop(0)
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
        if user_id == self.admin_id and state == "update_confirm":
            await self._handle_update_confirm_input(message, text)
            return
        if user_id == self.admin_id and state in ("update_edit_ru", "update_edit_en"):
            await self._handle_update_edit_input(message, state, text)
            return
        # Backward-compatible: pending draft without explicit state
        if (
            user_id == self.admin_id
            and hasattr(self, "pending_update_changelogs")
            and state == "chat"
        ):
            choice = text.strip().lower()
            if choice in _UPDATE_CONFIRM_YES | _UPDATE_CONFIRM_EDIT | _UPDATE_CONFIRM_NO:
                self.user_states[user_id] = "update_confirm"
                await self._handle_update_confirm_input(message, text)
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
                    self._save_user_prefs()
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
        self._save_user_prefs()
        # Проверка на активную минуту тишины
        if user_id in self.silence_until:
            remaining = int(self.silence_until[user_id] - now)
            if remaining > 0:
                lang = self.user_langs.get(user_id, DEFAULT_LANG)
                await message.answer(f"<i>Тишина... Осталось {remaining} сек.</i>", parse_mode="HTML")
                return
            else:
                del self.silence_until[user_id]

        # Но если мы здесь, значит это либо текст, либо другой тип сообщения.
        if message.text:
            await self._process_text_message(message, message.text, is_voice=False)
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
                t(lang, "assoc_confirm", label=("Свобода" if lang == "ru" else "Freedom"), words=', '.join(words[:5])),
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
                t(lang, "assoc_confirm", label=("Бессмысленность" if lang == "ru" else "Meaninglessness"), words=', '.join(words[:5])),
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
                t(lang, "assoc_confirm", label=("Одиночество" if lang == "ru" else "Isolation"), words=', '.join(words[:5])),
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

            summary_lines = [t(lang, "assoc_confirm", label=("Смерть" if lang == "ru" else "Death"), words=', '.join(words[:5]))]
            summary_lines.append("")
            summary_lines.append(t(lang, "assoc_summary_intro"))
            summary_lines.append(f"{('Свобода' if lang=='ru' else 'Freedom')}: {', '.join(associations.get('freedom', []))}")
            summary_lines.append(f"{('Бессмысленность' if lang=='ru' else 'Meaninglessness')}: {', '.join(associations.get('nonsense', []))}")
            summary_lines.append(f"{('Одиночество' if lang=='ru' else 'Isolation')}: {', '.join(associations.get('solitude', []))}")
            summary_lines.append(f"{('Смерть' if lang=='ru' else 'Death')}: {', '.join(associations.get('death', []))}")
            summary_lines.append("")
            summary_lines.append(t(lang, "analyzing"))
            
            summary = "\n".join(summary_lines)

            await message.answer(summary, parse_mode="HTML")

            # Получаем анализ
            therapist = self._get_therapist(user_id)
            
            # Стриминг: отправляем пустое сообщение и редактируем его
            sent = await message.answer("…")
            full_analysis = ""
            try:
                chunk_source = self._stream_generic(therapist.analyze_associations_stream, associations)
                full_analysis, shown = await self._smooth_stream_generic(chunk_source)
                for part in shown:
                    if len(full_analysis) <= 4000:
                        try:
                            await sent.edit_text(part)
                        except Exception:
                            pass
                        await asyncio.sleep(0.12)
            except Exception as e:
                await sent.edit_text(t(lang, "error_llm"))
                return
            finally:
                # Remove from queue
                if user_id in self.message_queue and self.message_queue[user_id]:
                    self.message_queue[user_id].pop(0)

            self.user_states[user_id] = "chat"
            del self.temp_associations[user_id]

            # Очищаем и форматируем финальный ответ
            clean_analysis = strip_markdown(full_analysis)
            escaped_analysis = html.escape(clean_analysis)
            
            final_text = f"<b>{('Интерпретация' if lang=='ru' else 'Interpretation')}:</b>\n\n{escaped_analysis}"
            max_length = 4000
            if len(final_text) > max_length:
                # Если длинное — удаляем стрим-сообщение и отправляем частями
                await sent.delete()
                parts = [final_text[i:i+max_length] for i in range(0, len(final_text), max_length)]
                for i, part in enumerate(parts):
                    reply_markup = get_main_keyboard(lang) if i == len(parts) - 1 else None
                    await message.answer(part, parse_mode="HTML", reply_markup=reply_markup)
            else:
                try:
                    await sent.edit_text(final_text, parse_mode="HTML", reply_markup=get_main_keyboard(lang))
                except Exception:
                    await message.answer(final_text, parse_mode="HTML", reply_markup=get_main_keyboard(lang))
    async def _handle_story_input(self, message: types.Message, text: str):        
        """Обработка ввода истории."""
        user_id = message.from_user.id
        text = text.strip()        
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        if len(text) < 20:
            await message.answer(t(lang, "story_too_short"))
            return
        
        if len(text) > 3000:
            await message.answer(t(lang, "story_too_long"))
            return        
        therapist = self._get_therapist(user_id)
        
        # Стриминг: отправляем пустое сообщение и редактируем его
        sent = await message.answer("…")
        full_analysis = ""
        try:
            chunk_source = self._stream_generic(therapist.analyze_story_stream, text)
            full_analysis, shown = await self._smooth_stream_generic(chunk_source)
            for part in shown:
                if len(full_analysis) <= 4000:
                    try:
                        await sent.edit_text(part)
                    except Exception:
                        pass
                    await asyncio.sleep(0.12)
        except Exception as e:
            await sent.edit_text(t(lang, "error_llm"))
            return
        finally:
            # Remove from queue
            if user_id in self.message_queue and self.message_queue[user_id]:
                self.message_queue[user_id].pop(0)
        
        self.user_states[user_id] = "chat"
        
        # Очищаем и форматируем финальный ответ
        clean_analysis = strip_markdown(full_analysis)
        
        # Если после очистки текст пустой или содержит только технические фразы
        if not clean_analysis.strip() or "Ответ должен быть" in clean_analysis:
            print(f"[DEBUG] strip_markdown returned empty or technical text. Original: {full_analysis}")
            clean_analysis = full_analysis
        
        escaped_analysis = html.escape(clean_analysis)
        final_text = f"<b>{('Экзистенциальный отклик' if lang=='ru' else 'Existential response')}:</b>\n\n{escaped_analysis}"
        max_length = 4000
        if len(final_text) > max_length:
            # Если длинное — удаляем стрим-сообщение и отправляем частями
            await sent.delete()
            parts = [final_text[i:i+max_length] for i in range(0, len(final_text), max_length)]
            for i, part in enumerate(parts):
                kwargs = {"parse_mode": "HTML"}
                if i == len(parts) - 1:
                    kwargs["reply_markup"] = get_main_keyboard(lang)
                await message.answer(part, **kwargs)
        else:
            try:
                await sent.edit_text(final_text, parse_mode="HTML", reply_markup=get_main_keyboard(lang))
            except Exception:
                await message.answer(final_text, parse_mode="HTML", reply_markup=get_main_keyboard(lang))        
    async def _handle_meaning(self, message: types.Message):
        user_id = message.from_user.id
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        therapist = self._get_therapist(user_id)
        meaning_prompt = t(lang, "meaning_prompt")
        meaning_system = t(lang, "meaning_system")
        try:
            response = therapist.generate_response(meaning_prompt, temporary_system_instruction=meaning_system, use_analysis_model=False)
            await message.answer(f"🌱 {response}")        
        except Exception as e:
            print(f"[MEANING] Error: {e}")
            await message.answer(t(lang, "error_llm"))

    async def _handle_meaning_is(self, message: types.Message):        
        user_id = message.from_user.id
        self.user_meaning_enabled[user_id] = True
        from datetime import datetime
        self.user_meaning_last_time[user_id] = datetime.now()
        self._save_user_prefs()
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        await message.answer(t(lang, "meaning_enabled"))

    async def _handle_meaning_gone(self, message: types.Message):
        user_id = message.from_user.id
        self.user_meaning_enabled[user_id] = False
        self._save_user_prefs()
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        await message.answer(t(lang, "meaning_disabled"))

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
        if not self.user_meaning_enabled.get(user_id, False):
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
                response = therapist.generate_response(meaning_prompt, temporary_system_instruction=meaning_system, use_analysis_model=False)

                history = self.user_meaning_history.get(user_id, [])
                if response in history:
                    response = therapist.generate_response(meaning_prompt, temporary_system_instruction=meaning_system, use_analysis_model=False)
                history.append(response)
                if len(history) > 100:
                    history = [response]
                
                self.user_meaning_history[user_id] = history
                self.user_meaning_last_time[user_id] = now
                self.user_meaning_count[user_id] = self.user_meaning_count.get(user_id, 0) + 1
                
                # Auto-disable after 17 messages
                if self.user_meaning_count[user_id] >= 17:
                    self.user_meaning_enabled[user_id] = False
                    
                self._save_user_prefs()
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
            chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
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

        threading.Thread(target=_producer, daemon=True).start()

        while True:
            kind, payload = await queue.get()
            if kind == "chunk":
                yield payload
            elif kind == "error":
                raise RuntimeError(payload)
            else:
                break

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
                yield payload
            elif kind == "error":
                yield f"Ошибка: {payload}"
                break
            elif kind == "done":
                break

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
        full = ""
        shown = []

        async for chunk in chunk_source:
            full += chunk
            words = re.findall(r'\S+', full)
            target = max(1, len(words) // words_per_tick)
            while len(shown) < target:
                n = (len(shown) + 1) * words_per_tick
                if n >= len(words):
                    shown.append(full)
                    break
                match = list(re.finditer(r'\S+', full))[n - 1]
                shown.append(full[:match.end()])

        if not shown or shown[-1] != full:
            shown.append(full)
        return full, shown

    def _split_text(self, text: str, max_len: int = 4000) -> list:
        """Разбивает текст на части <= max_len, не обрезая на полуслове.

        Разбиение идёт по границам предложений (точка, восклицательный,
        вопросительный знак, многоточие, перенос строки). Если одно предложение
        длиннее max_len, оно режется по пробелам (по границам слов).
        """
        if len(text) <= max_len:
            return [text]

        sentences = re.split(r'(?<=[.!?…])\s+|\n+', text)
        chunks = []
        current = ""
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 <= max_len:
                current = (current + " " + sent).strip() if current else sent
            else:
                if current:
                    chunks.append(current)
                # Одно предложение длиннее лимита — режем по словам
                if len(sent) > max_len:
                    words = sent.split(" ")
                    buf = ""
                    for w in words:
                        if len(buf) + len(w) + 1 > max_len:
                            if buf:
                                chunks.append(buf)
                            buf = w
                        else:
                            buf = (buf + " " + w).strip() if buf else w
                    current = buf
                else:
                    current = sent
        if current:
            chunks.append(current)
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
            # Используем gpt-5.4-mini для резюме
            summary = therapist.generate_response(
                prompt,
                temporary_system_instruction=(
                    "Ты — супервизор. Пиши предельно лаконично и конкретно. "
                    "Максимум 3 коротких предложения. Без шаблонов и markdown."
                ),
                model=os.getenv("SUMMARY_MODEL", "gpt-5.4-mini")
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

    async def _handle_chat(self, message: types.Message, text: str, is_voice: bool = False):
        """Обработка обычного чата."""
        user_id = message.from_user.id
        user_input = text
        lang = self.user_langs.get(user_id, DEFAULT_LANG)

        # Never stream (or LLM-answer) slash-commands that slipped into chat.
        raw = (text or "").strip()
        if raw.startswith("/"):
            cmd = raw[1:].split("@", 1)[0].split()[0].lower()
            if cmd == "void":
                if user_id in self.message_queue and self.message_queue[user_id]:
                    self.message_queue[user_id].pop(0)
                await self._handle_void(message)
                return
            if user_id in self.message_queue and self.message_queue[user_id]:
                self.message_queue[user_id].pop(0)
            return
        
        # Track username for admin lookup and persist immediately
        if message.from_user.username:
            self.user_usernames[user_id] = message.from_user.username
            self._save_user_prefs()


        
        # Показываем, что печатаем
        await self.bot.send_chat_action(
            chat_id=message.chat.id,
            action="typing"
        )
        
        # Получаем ответ
        therapist = self._get_therapist(user_id)

        sent = None
        if is_voice:
            # Для голосовых сообщений нужен полный текст для генерации речи
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, therapist.chat, user_input)
            except Exception:
                response = "Ошибка: исключение при вызове LLM"
        else:
            # Стриминг: отправляем пустое сообщение и редактируем его по мере поступления
            sent = await message.answer("…")
            full_response = ""
            try:
                full_response, shown = await self._smooth_stream(therapist, user_input)
                # Каждый элемент shown — префикс полного текста до границы слов.
                # edit_text заменяет весь текст, поэтому просто присваиваем part.
                for part in shown:
                    if len(full_response) <= 4000:
                        try:
                            await sent.edit_text(part)
                        except Exception:
                            pass
                        await asyncio.sleep(0.12)
            except Exception:
                pass
            response = full_response
        if not response or response.startswith("Ошибка:"):
            if user_id in self.message_queue and self.message_queue[user_id]:
                self.message_queue[user_id].pop(0)
            if sent is not None:
                try:
                    await sent.edit_text(t(lang, "error_llm"))
                except Exception:
                    await message.answer(t(lang, "error_llm"))
            else:
                await message.answer(t(lang, "error_llm"))
            return

        # Update message count and generate summary every 16 messages (internal only)
        self.user_message_counts[user_id] = self.user_message_counts.get(user_id, 0) + 1

        # Store recent message
        if user_id not in self.user_recent_messages:
            self.user_recent_messages[user_id] = []        
        from datetime import datetime
        self.user_recent_messages[user_id].append({
            "role": "user",
            "content": user_input[:4000],  # keep enough context for admin review
            "timestamp": datetime.now().isoformat()
        })
        # Keep only last 16 messages
        self.user_recent_messages[user_id] = self.user_recent_messages[user_id][-16:]
        
        # Track username for admin lookup and persist immediately
        if message.from_user.username:
            self.user_usernames[user_id] = message.from_user.username
        
        if self.user_message_counts[user_id] >= 16:
            # Generate summary for internal admin use
            summary = await self._generate_user_summary(user_id, therapist)
            self.user_summaries[user_id] = summary
            self.user_message_counts[user_id] = 0  # reset counter
            self._save_user_prefs()
            print(f"[ADMIN SUMMARY] User {user_id}: {summary[:100]}...")
        else:
            # Save prefs even if summary not generated to persist message count and username
            self._save_user_prefs()

        
        # Если это голосовое сообщение, генерируем аудио
        if is_voice:
            await self.bot.send_chat_action(
                chat_id=message.chat.id,
                action="record_voice"
            )
            
            # Создаем временный файл
            temp_dir = Path("temp_audio")
            temp_dir.mkdir(exist_ok=True)
            audio_path = temp_dir / f"response_{user_id}.mp3"
            
            # Генерируем речь
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                therapist.generate_speech, 
                response, 
                str(audio_path)
            )
            
            if not result.startswith("Ошибка"):
                # Отправляем голосовое
                voice_file = types.FSInputFile(result)
                await message.answer_voice(voice_file)
                
                # Удаляем файл
                try:
                    os.remove(result)
                except:
                    pass
            else:
                await message.answer(f"Не удалось сгенерировать голос: {result}")
        
        # Отправляем текстовый ответ (разбиваем на части если длинный)
        max_length = 4000
        try:
            if is_voice:
                # Голосовой путь: отправляем текст как раньше
                parts = self._split_text(response, max_length)
                for i, part in enumerate(parts):
                    kwargs = {}
                    if i == len(parts) - 1:
                        kwargs["reply_markup"] = get_main_keyboard(lang)
                    await message.answer(part, **kwargs)
            elif len(response) > max_length:
                # Стриминг: сообщение уже отредактировано, но ответ длиннее лимита.
                # Удаляем стриминговое сообщение и отправляем частями по границам предложений.
                try:
                    await sent.delete()
                except Exception:
                    pass
                parts = self._split_text(response, max_length)
                for i, part in enumerate(parts):
                    kwargs = {}
                    if i == len(parts) - 1:
                        kwargs["reply_markup"] = get_main_keyboard(lang)
                    await message.answer(part, **kwargs)
            else:
                # Ответ уже показан через edit_text — обновим последнее сообщение с клавиатурой
                try:
                    await sent.edit_text(response, reply_markup=get_main_keyboard(lang))
                except Exception:
                    pass
        finally:
            # Remove message from queue after responding (or failing)
            if user_id in self.message_queue and self.message_queue[user_id]:
                self.message_queue[user_id].pop(0)
        
        # Store assistant response
        if user_id not in self.user_recent_messages:
            self.user_recent_messages[user_id] = []        
        from datetime import datetime
        self.user_recent_messages[user_id].append({
            "role": "assistant",
            "content": response[:4000],  # keep enough context for admin review
            "timestamp": datetime.now().isoformat()
        })
        # Keep only last 16 messages
        self.user_recent_messages[user_id] = self.user_recent_messages[user_id][-16:]
        
        # Reset consecutive message counter since bot just responded
        self.user_consecutive_messages[user_id] = 0

    
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
                    self._save_user_prefs()
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
                        self._save_user_prefs()
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

        # Стриминг: отправляем пустое сообщение и редактируем его
        sent = await message.answer("…")
        full_response = ""
        try:
            chunk_source = self._stream_generic(therapist.analyze_image_stream, image_url, caption)
            full_response, shown = await self._smooth_stream_generic(chunk_source)
            for part in shown:
                if len(full_response) <= 4000:
                    try:
                        await sent.edit_text(part)
                    except Exception:
                        pass
                    await asyncio.sleep(0.12)
        except Exception as e:
            await sent.edit_text(t(lang, "image_analysis_failed", error=str(e)))
            return

        # If analyze_image returned an error-like string
        if not full_response or ("Ошибка" in full_response or full_response.strip().lower().startswith("error")):
            await sent.edit_text(t(lang, "image_analysis_failed", error=full_response or "unknown"))
            return

        full_response = strip_markdown(full_response)
        if len(full_response) <= 4000:
            try:
                await sent.edit_text(full_response)
            except Exception:
                await message.answer(full_response)
        else:
            await sent.delete()
            await message.answer(full_response)
    async def _check_and_notify_updates(self):
        """Check for code updates and notify all active users."""
        try:
            from pathlib import Path
            project_root = Path(__file__).parent.parent
            
            print(f"[UPDATE CHECK] Starting update check...")
            print(f"[UPDATE CHECK] Project root: {project_root}")
            print(f"[UPDATE CHECK] Admin ID: {self.admin_id}")
            print(f"[UPDATE CHECK] Users in memory: {len(self.user_langs)}")
            
            # Get a therapist instance for LLM calls
            therapist = self._get_therapist(self.admin_id)
            print(f"[UPDATE CHECK] Therapist initialized: {therapist is not None}")
            
            # Generate changelogs for both languages (don't save hashes yet)
            print(f"[UPDATE CHECK] Generating changelogs...")
            changelog_ru = check_and_generate_changelog(project_root, therapist, self.admin_id, "ru", should_save_hashes=False)
            changelog_en = check_and_generate_changelog(project_root, therapist, self.admin_id, "en", should_save_hashes=False)

            # Always refresh hash+snapshot baseline after a check.
            # Otherwise technical-only diffs re-fire on every restart.
            save_current_hashes(project_root)

            print(f"[UPDATE CHECK] Changelog RU: {'YES (' + str(len(changelog_ru)) + ' chars)' if changelog_ru else 'NO'}")
            print(f"[UPDATE CHECK] Changelog EN: {'YES (' + str(len(changelog_en)) + ' chars)' if changelog_en else 'NO'}")
            # Check if there's any changelog (None or empty string)
            has_changelog = bool(changelog_ru or changelog_en)
            if has_changelog:
                await self._send_update_preview(
                    self.admin_id,
                    {"ru": changelog_ru or "", "en": changelog_en or ""},
                )
                print(f"[UPDATE CHECK] Admin confirmation requested for {len(self.user_langs)} users")
            else:
                print(f"[UPDATE CHECK] No changelogs to send, skipping notification")
        except Exception as e:
            print(f"[UPDATE CHECK] Failed to check/notify updates: {e}")
            import traceback
            traceback.print_exc()



    async def run(self):
        # Get bot info for username
        try:
            me = await self.bot.get_me()
            print(f"Bot @{me.username} started")
        except Exception as e:
            print(f"Could not get bot username: {e}")
        
        # Check for updates on startup
        await self._check_and_notify_updates()
        
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
                    await self._check_and_send_daily_meaning(user_id, user_id)
            except Exception as e:
                print(f"Error in _daily_meaning_loop: {e}")
                await asyncio.sleep(10)


async def main():
    """Точка входа."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Telegram бот-терапевт")
    parser.add_argument("--model", default="deepseek-v4-pro", help="Модель LLM для чата")
    parser.add_argument("--analysis-model", default="deepseek-v4-pro", help="Модель LLM для анализов")
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

