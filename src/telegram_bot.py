"""
Telegram bot for existential therapy.
"""


import os
import asyncio
import html
from pathlib import Path
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

try:
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.filters import Command
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    from aiogram.utils.keyboard import ReplyKeyboardBuilder
    AIogram_AVAILABLE = True
except ImportError:
    AIogram_AVAILABLE = False
    print("aiogram не установлен. Установите: pip install aiogram")
    # Define dummy types to avoid NameError
    ReplyKeyboardMarkup = None
    KeyboardButton = None
    ReplyKeyboardBuilder = None

from therapist_bot import ExistentialTherapistBot
from lang_utils import detect_language
from i18n import t
import re



def strip_markdown(text: str) -> str:
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
    return text


# Default language when nothing is detected or user's Telegram locale is unsupported
DEFAULT_LANG = os.getenv("DEFAULT_LANG", "en")




# Keyboards
def get_main_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Main keyboard localized by `lang`."""

    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=t(lang, "button_assoc")))
    builder.add(KeyboardButton(text=t(lang, "button_analyze")))
    builder.add(KeyboardButton(text=t(lang, "button_reset")))
    builder.add(KeyboardButton(text=t(lang, "button_help")))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_cancel_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Keyboard with a cancel button localized by `lang`."""

    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=t(lang, "button_cancel")))
    return builder.as_markup(resize_keyboard=True)


class TelegramTherapistBot:
    """Telegram therapist bot."""

    
    def __init__(
        self,
        telegram_token: str,
        llm_model: str = "gpt-4o-mini",
        llm_analysis_model: str = "claude-3-opus-latest",
        llm_api_key: Optional[str] = None,
        llm_api_base: Optional[str] = None,
        use_rag: bool = True,
        ask_question_prob: Optional[float] = None
    ):

        if not AIogram_AVAILABLE:
            raise RuntimeError("aiogram not installed")
        
        self.telegram_token = telegram_token
        self.llm_model = llm_model
        self.llm_analysis_model = llm_analysis_model
        
        # Initialize Telegram bot
        self.bot = Bot(token=telegram_token)
        self.dp = Dispatcher()
        
        # Session storage (user_id -> therapist_bot)
        self.sessions: dict[int, ExistentialTherapistBot] = {}
        
        # User states
        self.user_states: dict[int, str] = {}  # user_id -> state
        # User languages (user_id -> 'ru'|'en')
        self.user_langs: dict[int, str] = {}
        
        # Temporary storage for associations
        self.temp_associations: dict[int, dict] = {}
        
        # LLM parameters
        self.llm_api_key = llm_api_key
        self.llm_api_base = llm_api_base
        self.use_rag = use_rag
        # probability to ask a clarifying question per-response (defaults to env or 0.2)
        try:
            self.ask_question_prob = ask_question_prob if ask_question_prob is not None else float(os.getenv("OPENAI_ASK_QUESTION_PROB", 0.2))
        except Exception:
            self.ask_question_prob = 0.2
        
        # Load persisted user preferences
        self.prefs_path = Path(__file__).parent.parent / "data" / "user_prefs.json"
        self.user_ask_prob: dict[int, float] = {}
        self._load_user_prefs()

        # Bot start time for filtering old messages
        import time
        self.start_time = time.time()
        self.processed_flood_users = set()
        
        # Track last update notification time
        self.last_update_notification: dict[int, float] = {}

        
        # Silence mode storage (user_id -> end_timestamp)
        self.silence_until: dict[int, float] = {}

        # Daily meaning tracking
        self.user_meaning_enabled: dict[int, bool] = {}
        self.user_meaning_history: dict[int, list[str]] = {}
        self.user_meaning_last_time: dict[int, datetime] = {}
        self.user_meaning_count: dict[int, int] = {}

        self._register_handlers()




    
    
    def _get_therapist(self, user_id: int) -> ExistentialTherapistBot:
        """Get or create therapist session for user."""
        if user_id not in self.sessions:
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            per_user_prob = self.user_ask_prob.get(user_id, self.ask_question_prob)
            try:
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
                self.sessions[user_id] = ExistentialTherapistBot(
                    model=self.llm_model,
                    api_key=self.llm_api_key,
                    api_base=self.llm_api_base,
                    use_rag=self.use_rag,
                    language=lang,
                )
        return self.sessions[user_id]


    def _load_user_prefs(self):
        try:
            self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
            if self.prefs_path.exists():
                import json
                from datetime import datetime
                with open(self.prefs_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.user_langs = {int(k): v for k, v in data.get('user_langs', {}).items()}
                    self.user_ask_prob = {int(k): float(v) for k, v in data.get('user_ask_prob', {}).items()}
                    self.user_meaning_enabled = {int(k): v for k, v in data.get('user_meaning_enabled', {}).items()}
                    self.user_meaning_history = {int(k): v for k, v in data.get('user_meaning_history', {}).items()}
                    self.user_meaning_last_time = {
                        int(k): datetime.fromisoformat(v) 
                        for k, v in data.get('user_meaning_last_time', {}).items()
                    }
                    self.user_meaning_count = {int(k): v for k, v in data.get('user_meaning_count', {}).items()}
        except Exception:
            self.user_langs = getattr(self, "user_langs", {}) or {}
            self.user_ask_prob = getattr(self, "user_ask_prob", {}) or {}
            self.user_meaning_enabled = getattr(self, "user_meaning_enabled", {}) or {}
            self.user_meaning_history = getattr(self, "user_meaning_history", {}) or {}
            self.user_meaning_last_time = getattr(self, "user_meaning_last_time", {}) or {}
            self.user_meaning_count = getattr(self, "user_meaning_count", {}) or {}




    def _save_user_prefs(self):
        try:
            import json
            payload = {
                'user_langs': {str(k): v for k, v in self.user_langs.items()},
                'user_ask_prob': {str(k): v for k, v in self.user_ask_prob.items()},
                'user_meaning_enabled': {str(k): v for k, v in self.user_meaning_enabled.items()},
                'user_meaning_history': {str(k): v for k, v in self.user_meaning_history.items()},
                'user_meaning_last_time': {
                    str(k): v.isoformat() for k, v in self.user_meaning_last_time.items()
                },
                'user_meaning_count': {str(k): v for k, v in self.user_meaning_count.items()},
            }
            with open(self.prefs_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


    
    
    def _register_handlers(self):
        """Register command handlers."""
        
        @self.dp.message(Command("start"))
        async def cmd_start(message: types.Message):
            await self._handle_start(message)
        
        @self.dp.message(Command("help"))
        async def cmd_help(message: types.Message):
            await self._handle_help(message)

        @self.dp.message(Command("askprob"))
        async def cmd_askprob(message: types.Message):
            # /askprob 0.1 or /askprob reset
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
            self.user_ask_prob[user_id] = val
            if user_id in self.sessions:
                sess = self.sessions[user_id]
                sess.ask_question_prob = val
            self._save_user_prefs()
            await message.answer(t(lang, "askprob_set", prob=str(val)))

        @self.dp.message(Command("lang"))
        async def cmd_lang(message: types.Message):
            # /lang en or /lang ru or /lang (show current)
            user_id = message.from_user.id
            parts = (message.text or "").split(None, 1)
            args = parts[1].strip().lower() if len(parts) > 1 else ""
            current = self.user_langs.get(user_id, DEFAULT_LANG)
            if not args:
                await message.answer(t(current, "lang_current", lang=current))
                return
            if args in ("ru", "en"):
                self.user_langs[user_id] = args
                if user_id in self.sessions:
                    sess = self.sessions[user_id]
                    sess.language = args
                    sess.system_prompt = sess._load_system_prompt()
                try:
                    self._save_user_prefs()
                except Exception:
                    pass
                await message.answer(t(args, "lang_set", lang=args), reply_markup=get_main_keyboard(args))
                return
            await message.answer(t(current, "lang_invalid"))

        @self.dp.message(Command("switchlang"))
        async def cmd_switchlang(message: types.Message):
            # Toggle between 'ru' and 'en'
            user_id = message.from_user.id
            current = self.user_langs.get(user_id, DEFAULT_LANG)
            new_lang = "ru" if current != "ru" else "en"
            self.user_langs[user_id] = new_lang
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
            print(f"[FEEDBACK] From user {user_id}: {feedback_text[:100]}...")
            await message.answer(t(lang, "feedback_thanks"))

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
        
        @self.dp.message(F.text == "Сбросить диалог")
        async def btn_reset(message: types.Message):
            await self._handle_reset(message)
        @self.dp.message(F.text == t('en', 'button_reset'))
        async def btn_reset_en(message: types.Message):
            await self._handle_reset(message)
        
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
            emoji = message.sticker.emoji or ""
            therapist = self._get_therapist(user_id)
            
            prompt = f"""Client sent a sticker (emoji: {emoji}).
            Give a very short, warm, empathetic response (1 sentence).
            Style: Irvin Yalom. Quiet, accepting, no pathos."""
            
            response = therapist.generate_response(prompt, temporary_system_instruction=prompt, use_analysis_model=True)
            await message.answer(response)

        @self.dp.message(Command("meta"))
        async def cmd_meta(message: types.Message):
            user_id = message.from_user.id
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            
            if user_id not in self.sessions or not self.sessions[user_id].history:
                await message.answer(t(lang, "story_too_short"))
                return
                
            await message.answer(t(lang, "meta_analyzing"), parse_mode="HTML")
            therapist = self._get_therapist(user_id)
            meta_prompt = t(lang, "meta_prompt")
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
            if user_id in self.silence_until:
                del self.silence_until[user_id]
                await message.answer(t(lang, "silence_end"))

        @self.dp.message(Command("void"))
        async def cmd_void(message: types.Message):
            user_id = message.from_user.id
            lang = self.user_langs.get(user_id, DEFAULT_LANG)
            try:
                void_text = "⠀\n⠀\n⠀\n⠀\n⠀"
                await message.answer(void_text)
            except Exception:
                try:
                    void_text = "ㅤ\nㅤ\nㅤ\nㅤ\nㅤ"
                    await message.answer(void_text)
                except Exception:
                    await message.answer("...")            
            await asyncio.sleep(3)
            await message.answer(t(lang, "void_msg"), parse_mode="HTML")

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
        """Handle /start command."""
        user_id = message.from_user.id
        chat_id = message.chat.id
        user_name = message.from_user.first_name
        
        self.user_states[user_id] = "chat"

        tg_lang = (message.from_user.language_code or "").lower()
        if user_id not in self.user_langs:
            if tg_lang.startswith("ru"):
                self.user_langs[user_id] = "ru"
            elif tg_lang.startswith("en"):
                self.user_langs[user_id] = "en"
            else:
                self.user_langs[user_id] = DEFAULT_LANG

        if user_id not in self.user_meaning_enabled:
            self.user

    
    async def _handle_start(self, message: types.Message):
        """Обработка /start."""
        user_id = message.from_user.id
        chat_id = message.chat.id
        user_name = message.from_user.first_name
        
        # Сбрасываем состояние
        self.user_states[user_id] = "chat"

        # Устанавливаем язык пользователя по его Telegram locale (если доступен)        # Но не переопределяем уже установленный пользователем язык.
        tg_lang = (message.from_user.language_code or "").lower()
        if user_id not in self.user_langs:
            if tg_lang.startswith("ru"):
                self.user_langs[user_id] = "ru"
            elif tg_lang.startswith("en"):
                self.user_langs[user_id] = "en"
            else:
                self.user_langs[user_id] = DEFAULT_LANG

        # Enable daily meanings by default for new users
        if user_id not in self.user_meaning_enabled:
            self.user_meaning_enabled[user_id] = True
            from datetime import datetime, timedelta
            self.user_meaning_last_time[user_id] = datetime.now() - timedelta(hours=25)
            
        # Force clear history for this user to ensure a new meaning is sent on /start
        if user_id in self.user_meaning_history:
            self.user_meaning_history[user_id] = []
        from datetime import datetime, timedelta
        self.user_meaning_last_time[user_id] = datetime.now() - timedelta(hours=25)

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
    
    async def _check_and_notify_updates(self):
        """Check for code updates and notify all active users."""
        try:
            from pathlib import Path
            project_root = Path(__file__).parent.parent
            
            print(f"[UPDATE CHECK] Starting update check...")
            print(f"[UPDATE CHECK] Project root: {project_root}")
            print(f"[UPDATE CHECK] Users in memory: {len(self.user_langs)}")
            
            # Get a therapist instance for LLM calls
            # Use first available user_id or create a temporary one
            admin_id = None
            for uid in self.user_langs.keys():
                admin_id = uid
                break
            
            if admin_id is None:
                print(f"[UPDATE CHECK] No users available for update check")
                return
            
            therapist = self._get_therapist(admin_id)
            print(f"[UPDATE CHECK] Therapist initialized: {therapist is not None}")
            
            # Generate changelogs for both languages (don't save hashes yet)
            # Hashes will be saved after notifications are sent successfully
            print(f"[UPDATE CHECK] Generating changelogs...")
            
            # Import here to avoid circular dependencies
            from scripts.check_code_cache import check_and_generate_changelog, save_current_hashes
            from scripts.init_code_cache import init_cache
            
            changelog_ru = check_and_generate_changelog(project_root, therapist, admin_id, "ru", should_save_hashes=False)
            changelog_en = check_and_generate_changelog(project_root, therapist, admin_id, "en", should_save_hashes=False)

            print(f"[UPDATE CHECK] Changelog RU: {'YES (' + str(len(changelog_ru)) + ' chars)' if changelog_ru else 'NO'}")
            print(f"[UPDATE CHECK] Changelog EN: {'YES (' + str(len(changelog_en)) + ' chars)' if changelog_en else 'NO'}")
            
            # Check if there's any changelog (None or empty string)
            has_changelog = bool(changelog_ru or changelog_en)
            if has_changelog:
                # Store changelogs for admin confirmation
                self.pending_update_changelogs = {
                    "ru": changelog_ru,
                    "en": changelog_en
                }
                
                # Send preview to admin for confirmation
                preview_limit = 800
                preview_lines = [
                    f"📋 <b>Предпросмотр обновления</b>",
                    "",
                    f"Получателей: <b>{len(self.user_langs)}</b>",
                    "",
                    "<b>RU:</b>",
                    (changelog_ru[:preview_limit] + "...") if changelog_ru and len(changelog_ru) > preview_limit else (changelog_ru or "—"),
                    "",
                    "<b>EN:</b>",
                    (changelog_en[:preview_limit] + "...") if changelog_en and len(changelog_en) > preview_limit else (changelog_en or "—"),
                    "",
                    "Отправить? Ответьте <b>да</b> для подтверждения рассылки."
                ]
                
                await self.bot.send_message(
                    admin_id,
                    "\n".join(preview_lines),
                    parse_mode="HTML"
                )
                
                print(f"[UPDATE CHECK] Admin confirmation requested for {len(self.user_langs)} users")
            else:
                print(f"[UPDATE CHECK] No changelogs to send, skipping notification")
        except Exception as e:
            print(f"[UPDATE CHECK] Failed to check/notify updates: {e}")
            import traceback
            traceback.print_exc()

    async def _process_update_broadcast(self):
        """Выполнение рассылки обновлений после подтверждения админа."""
        if not hasattr(self, 'pending_update_changelogs'):
            return

        changelogs = self.pending_update_changelogs
        del self.pending_update_changelogs

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
                localized_changelog = f"{header}{changelog}{footer}"

                await self.bot.send_message(
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

        # Save hashes after successful broadcast
        from pathlib import Path
        from scripts.check_code_cache import save_current_hashes
        from scripts.init_code_cache import init_cache
        project_root = Path(__file__).parent.parent
        save_current_hashes(project_root)
        init_cache()

        # Отправляем отчёт админу (первому доступному пользователю)
        for uid in self.user_langs.keys():
            try:
                await self.bot.send_message(
                    uid,
                    "\n".join(report_lines),
                    parse_mode="HTML"
                )
                break
            except:
                pass

    async def _handle_reset(self, message: types.Message):

        user_id = message.from_user.id
        
        if user_id in self.sessions:
            self.sessions[user_id].reset()
        
        self.user_states[user_id] = "chat"
        lang = self.user_langs.get(user_id, DEFAULT_LANG)

        await message.answer(
            t(lang, "reset_confirm"),
            reply_markup=get_main_keyboard(lang)
        )
    
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
        self.user_states[user_id] = "chat"
        if user_id in self.temp_associations:
            del self.temp_associations[user_id]

        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        await message.answer(
            t(lang, "action_cancelled"),
            reply_markup=get_main_keyboard(lang)
        )
    
    async def _process_text_message(self, message: types.Message, text: str, is_voice: bool = False):
        """Обработка текстового сообщения (или распознанного голоса)."""
        user_id = message.from_user.id
        
        # Получаем текущее состояние пользователя
        state = self.user_states.get(user_id, "chat")
        
        # Intercept localized cancel texts so they aren't processed as content while in a special state
        t_ru_cancel = t('ru', 'button_cancel')
        t_en_cancel = t('en', 'button_cancel')
        if text and text.strip() in (t_ru_cancel, t_en_cancel, '/cancel', 'Отмена', 'Cancel'):
            await self._handle_cancel(message)
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
        from datetime import datetime
        now = time.time()
        if message.date.timestamp() < self.start_time - 10:
            user_id = message.from_user.id
            if user_id not in self.processed_flood_users:
                lang = self.user_langs.get(user_id, DEFAULT_LANG)
                await message.answer(t(lang, "error_flood"))
                self.processed_flood_users.add(user_id)
            return

        user_id = message.from_user.id
        
        # Сохраняем настройки пользователя
        self._save_user_prefs()
        
        if message.text and message.text.startswith('/'):
            return
        # Проверка на активную минуту тишины
        if user_id in self.silence_until:
            remaining = int(self.silence_until[user_id] - now)
            if remaining > 0:
                lang = self.user_langs.get(user_id, DEFAULT_LANG)
                await message.answer(f"<i>Тишина... Осталось {remaining} сек.</i>", parse_mode="HTML")
                return
            else:
                del self.silence_until[user_id]

        if message.text:
            await self._process_text_message(message, message.text, is_voice=False)
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

        elif state == "assoc_nonsense":
            self.temp_associations[user_id]["nonsense"] = words[:5]
            self.user_states[user_id] = "assoc_solitude"
            await message.answer(
                t(lang, "assoc_confirm", label=("Бессмысленность" if lang == "ru" else "Meaninglessness"), words=', '.join(words[:5])),
                parse_mode="HTML"
            )
            await message.answer(t(lang, "assoc_solitude_prompt"), parse_mode="HTML")

        elif state == "assoc_solitude":
            self.temp_associations[user_id]["solitude"] = words[:5]
            self.user_states[user_id] = "assoc_death"
            await message.answer(
                t(lang, "assoc_confirm", label=("Одиночество" if lang == "ru" else "Isolation"), words=', '.join(words[:5])),
                parse_mode="HTML"
            )
            await message.answer(t(lang, "assoc_death_prompt"), parse_mode="HTML")

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
            analysis = therapist.analyze_associations(associations)

            self.user_states[user_id] = "chat"
            del self.temp_associations[user_id]

            await message.answer(
                f"<b>{('Интерпретация' if lang=='ru' else 'Interpretation')}:</b>\n\n{html.escape(analysis)}",
                parse_mode="HTML",
                reply_markup=get_main_keyboard(lang)
            )    
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
        await message.answer(t(lang, "analyzing"), reply_markup=get_main_keyboard(lang))        
        therapist = self._get_therapist(user_id)
        analysis = therapist.analyze_story(text)
        
        self.user_states[user_id] = "chat"
        
        await message.answer(
            f"<b>{('Экзистенциальный отклик' if lang=='ru' else 'Existential response')}:</b>\n\n{html.escape(analysis)}",
            parse_mode="HTML"
        )
    
    async def _handle_meaning(self, message: types.Message):
        user_id = message.from_user.id
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        therapist = self._get_therapist(user_id)
        meaning_prompt = t(lang, "meaning_prompt")
        response = therapist.generate_response(meaning_prompt, temporary_system_instruction="Ты — экзистенциальный поэт. Твоя задача — дарить мимолетные смыслы.", use_analysis_model=True)
        await message.answer(f"🌱 {response}")

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
            
            response = therapist.generate_response(meaning_prompt, temporary_system_instruction="Ты — экзистенциальный поэт. Твоя задача — дарить мимолетные смыслы.", use_analysis_model=True)
            
            history = self.user_meaning_history.get(user_id, [])
            if response in history:
                response = therapist.generate_response(meaning_prompt, temporary_system_instruction="Ты — экзистенциальный поэт. Твоя задача — дарить мимолетные смыслы.", use_analysis_model=True)
            
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
            await self.bot.send_message(chat_id, f"🌱 {response}")
            
            # Hint on 2nd, 7th... time
            count = self.user_meaning_count[user_id]
            if count == 2 or (count > 2 and (count - 2) % 5 == 0):
                hint = (
                    "You can disable daily meanings with /meaning_gone" 
                    if lang == "en" else 
                    "Вы можете отключить ежедневные смыслы командой /meaning_gone"
                )
                await self.bot.send_message(chat_id, hint)

    async def _handle_chat(self, message: types.Message, text: str, is_voice: bool = False):

        """Обработка обычного чата."""
        user_id = message.from_user.id
        user_input = text
        lang = self.user_langs.get(user_id, DEFAULT_LANG)
        
        # Показываем, что печатаем

        await self.bot.send_chat_action(
            chat_id=message.chat.id,
            action="typing"
        )
        
        # Получаем ответ
        therapist = self._get_therapist(user_id)
        try:
            response = therapist.chat(user_input)
            if not response or response.startswith("Ошибка:"):
                await message.answer(t(lang, "error_llm"))
                return
        except Exception:
            await message.answer(t(lang, "error_llm"))
            return
        
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
        if len(response) > max_length:
            parts = [response[i:i+max_length] for i in range(0, len(response), max_length)]
            for part in parts:
                await message.answer(part)
        else:
            await message.answer(response)
        
    
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
        await message.answer(t(lang, "listening"), reply_markup=get_main_keyboard(lang))

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
        
        await message.answer(t(lang, "analyzing_image"), reply_markup=get_main_keyboard(lang))

        therapist = self._get_therapist(user_id)

        # Выполняем в отдельном потоке
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                therapist.analyze_image,
                image_url,
                caption,
            )
        except Exception as e:
            await message.answer(t(lang, "image_analysis_failed", error=str(e)), reply_markup=get_main_keyboard(lang))
            return

        # If analyze_image returned an error-like string (Russian "Ошибка" or English "Error"), show localized message
        if not response or (isinstance(response, str) and ("Ошибка" in response or response.strip().lower().startswith("error"))):
            err_text = response if response else "unknown"
            await message.answer(t(lang, "image_analysis_failed", error=err_text), reply_markup=get_main_keyboard(lang))
            return

        await message.answer(response)

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
    parser.add_argument("--model", default="gpt-4o-mini", help="Модель LLM для чата")
    parser.add_argument("--analysis-model", default="claude-3-opus-latest", help="Модель LLM для анализов")
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
