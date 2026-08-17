""""
Экзистенциальный терапевт-бот.
Интеграция LLM + RAG + System Prompt.
"""

import os
import json
from pathlib import Path
from typing import Optional, Generator
from dataclasses import dataclass

from dotenv import load_dotenv
from i18n import t


from lang_utils import detect_language

# Загружаем переменные окружения
load_dotenv()


@dataclass
class Message:
    role: str
    content: str

_global_rag = None

class ExistentialTherapistBot:    
    """Экзистенциальный терапевт-бот."""
    
    def __init__(
        self,
        model: str = os.getenv("OPENAI_MODEL", "deepseek-v4-pro"),  # основная модель для чата
        analysis_model: str = os.getenv("OPENAI_ANALYSIS_MODEL", "deepseek-v4-pro"),  # модель для анализов
        api_key: Optional[str] = None,

        api_base: Optional[str] = None,
        use_rag: bool = True,
        data_dir: Optional[str] = None,
        language: str = "ru",
        ask_question_prob: Optional[float] = None,
    ):
        self.model = model
        self.analysis_model = analysis_model

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.api_base = api_base or os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        self.language = language
        # Probability to ask a clarifying/deep question in a response (0.0 - 1.0)
        # Can be overridden per-instance by passing `ask_question_prob`.
        try:
            self.ask_question_prob = float(os.getenv("OPENAI_ASK_QUESTION_PROB", 0.2))
        except Exception:
            self.ask_question_prob = 0.2

        # Override by explicit parameter if provided
        if ask_question_prob is not None:
            try:
                self.ask_question_prob = float(ask_question_prob)
            except Exception:
                pass
        
        self.use_rag = use_rag        
        # Загружаем system prompt
        self.system_prompt = self._load_system_prompt()
        
        # �?стория диалога
        self.history: list[Message] = []
        
        self.rag = None
        if use_rag:
            self._init_rag(data_dir)
        
        # LLM клиент
        self.client = None
        self._init_llm()
        # Last detected dominant given (session-scoped, not persisted)
        self.last_dominant_given: Optional[str] = None
    
    def _load_system_prompt(self) -> str:
        """Загрузка system prompt."""
        prompts_dir = Path(__file__).parent.parent / "prompts"
        # Try language-specific prompt first (e.g. system_prompt.ru.md or system_prompt.en.md)
        lang_file = prompts_dir / f"system_prompt.{self.language}.md"
        if lang_file.exists():
            with open(lang_file, 'r', encoding='utf-8') as f:
                return f.read()

        # Fallback to generic prompt
        prompt_path = prompts_dir / "system_prompt.md"
        if prompt_path.exists():
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()

        return self._default_prompt()
    
    def _default_prompt(self) -> str:
        """Дефолтный промпт если файл не найден."""
        return """Ты — эмпатичный экзистенциальный психотерапевт в традиции Ирвина Ялома. Твоё имя — Рой (или Рои).
    Помогай клиенту исследовать экзистенциальные данности: смерть, свободу, одиночество, бессмысленность.
    Не давай советов, задавай открытые вопросы, используй феноменологическое слушание.

    Важно: не задавай уточняющие вопросы автоматически в конце каждого ответа. Задавай вопрос только если он действительно помогает продвижению терапии (прояснить противоречие, открыть новый ракурс или прояснить ключевой момент). В остальных случаях делай отражение и краткое исследование без вопроса. Можно задавать глубокий вопрос в ~20–35% ответов, но только если он уместен и не звучит формально."""
    def _init_rag(self, data_dir: Optional[str]):
        """�?нициализация RAG."""
        try:
            # Use a global RAG instance to avoid memory leaks and slow initialization
            global _global_rag
            if _global_rag is None:
                from rag import ExistentialRAG
                _global_rag = ExistentialRAG(data_dir=data_dir)
                print("Global RAG инициализирован")
            self.rag = _global_rag
        except Exception as e:
            print(f"RAG недоступен: {e}")
            self.rag = None    
    def _init_llm(self):
        """�?нициализация LLM клиента."""
        try:
            from openai import OpenAI
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base
            )
            print(f"LLM клиент инициализирован: {self.model}")
        except ImportError:
            print("openai не установлен. Установите: pip install openai")
            self.client = None

    def select_technique(self, user_input: str) -> Optional[str]:
        """Deterministically choose a suitable technique key based on user_input keywords."""
        import random
        
        # 1. Prefer last detected dominant given from associations/story if available
        # We add some variety here by picking between a primary and secondary technique
        try:
            if getattr(self, 'last_dominant_given', None):
                lg = self.last_dominant_given
                mapping = {
                    'death': ['epitaph', 'socratic'],
                    'freedom': ['behavioral', 'paradox'],
                    'solitude': ['narrative', 'socratic'],
                    'nonsense': ['logotherapy', 'scaling']
                }
                if lg in mapping:
                    return random.choice(mapping[lg])
        except Exception:
            pass

        if not user_input:
            return None
        s = user_input.lower()
        
        # 2. Keyword-based detection for existential givens and states
        
        # Death (Смерть) -> epitaph
        if any(k in s for k in ("смерт", "умира", "умру", "конечн", "похорон", "кладбищ", "утрат", "death", "dying", "mortality", "funeral", "loss")):
            return "epitaph"
        
        # Freedom & Responsibility (Свобода и ответственность) -> behavioral
        if any(k in s for k in ("свобод", "выбор", "решен", "ответствен", "виноват", "вину", "freedom", "choice", "decision", "responsibility", "guilt")):
            return "behavioral"
            
        # Isolation (Одиночество) -> narrative
        if any(k in s for k in ("одинок", "одиноч", "изолир", "разрыв", "бросил", "никто не", "lonely", "loneliness", "isolat", "abandoned", "nobody")):
            return "narrative"
            
        # Meaninglessness (Бессмысленность) -> logotherapy
        if any(k in s for k in ("смысл", "бессмыс", "пустот", "зачем", "ради чего", "meaning", "meaningless", "purpose", "empty", "why bother")):
            return "logotherapy"

        # Anxiety/Panic -> mindfulness or grounding
        if any(k in s for k in ("тревог", "тревож", "паник", "страх", "паника", "anxiety", "panic", "afraid", "fear")):
            return random.choice(["mindfulness", "grounding"])
            
        # Somatic/Body focus -> somatic
        if any(k in s for k in ("тело", "телесн", "груди", "дыхан", "сердце", "живот", "сжимает", "трясет", "body", "somatic", "breath", "chest", "heart", "stomach", "shaking")):
            return "somatic"

        # Deep pain/trauma/crisis -> labeling (NOT scaling - it's inappropriate for deep pain)
        # Only extreme indicators, not common words like "very" or "pain"
        if any(k in s for k in (
            # Russian - extreme trauma/crisis only
            "невыносим", "не выношу", "разрывает", "сжирает", "уничтожает",
            "падаю в пропасть", "дна нет", "погибаю", "схожу с ума",
            "не могу дышать", "парализован", "окаменел", "мертв внутри",
            # English - extreme trauma/crisis only
            "unbearable", "can't bear", "tearing me apart", "consuming me",
            "destroying me", "falling apart", "no bottom", "going crazy",
            "can't breathe", "paralyzed", "numb inside", "dead inside"
        )):
            return "labeling"



        # Avoidance -> paradox
        if any(k in s for k in ("избег", "избегаю", "не делаю", "откладыв", "avoid", "avoiding", "avoidance", "procrastin")):
            return "paradox"

        # 3. Fallback heuristics
        if len(s.split()) < 6:
            # short messages — grounding or labeling
            return random.choice(["grounding", "labeling"])
            
        # default to a subtle intervention: socratic questioning
        return "socratic"    
    def _build_messages(self, user_input: str) -> list[dict]:
        """Построение сообщений для API."""
        # Add clean text instruction to avoid markdown
        clean_text_instr = "\n\nВАЖНО: Только чистый текст. Без звёздочек *, без жирного текста, без markdown-форматирования. HTML теги разрешены только если они явно нужны для структуры." if self.language == "ru" else "\n\nIMPORTANT: Clean text only. No asterisks *, no bold text, no markdown formatting. HTML tags allowed only when explicitly needed for structure."
        
        messages = [{"role": "system", "content": self.system_prompt + clean_text_instr}]
        
        # 1. Поиск по ассоциациям (Keyword-based RAG)

        assoc_context = []
        words = [w.strip().lower() for w in user_input.replace(',', ' ').replace(';', ' ').split() if len(w) > 3]
        if self.rag:
            for word in words:
                matches = self.rag.search_associations(word)
                if matches:
                    for m in matches[:2]:
                        if m.get('narratives', {}).get('free_form'):
                            # Build context in Russian (from RAG database)
                            context_ru = f"Человек с похожей ассоциацией ('{word}') на тему '{m['matched_givens']}': {m['narratives']['free_form']}"
                            
                            # Translate to English if session is in English
                            if self.language == "en":
                                context_en = self._translate_text(context_ru, target_lang='en')
                                assoc_context.append(context_en)
                            else:
                                assoc_context.append(context_ru)
        
        if assoc_context:
            header = "Context from others with similar associations:\n" if self.language == "en" else "Контекст из опыта других людей с похожими ассоциациями:\n"
            messages.append({
                "role": "system",
                "content": header + "\n---\n".join(assoc_context[:3])
            })


        # Suggest a context-appropriate therapeutic technique (deterministic heuristic)

        try:
            tech = self.select_technique(user_input)
            if tech:
                tech_label = tech
                # localized description
                try:
                    tech_desc = __import__('i18n').i18n.t(self.language, f"technique_{tech_label}")
                except Exception:
                    # fallback to english mapping defined locally
                    tech_desc = tech_label
                messages.append({
                    "role": "system",
                    "content": t(self.language, "incorporate_technique", tech_desc=tech_desc)
                })
        except Exception:
            pass
        
        # Добавляем RAG контекст (с опциональным переводом в язык сессии)
        if self.rag and self.use_rag:
            context = self.rag.get_context_for_query(user_input)
            if context:
                try:
                    code, prob = detect_language(context)
                except Exception:
                    code, prob = None, 0.0

                # Если контекст явно на другом языке и он отличается от сессионного, попробуем перевести
                translated_context = None
                try:
                    if code and self.language and self.language.startswith('en') and code.startswith('ru'):
                        translated_context = self._translate_text(context, target_lang='en')
                    elif code and self.language and self.language.startswith('ru') and code.startswith('en'):
                        translated_context = self._translate_text(context, target_lang='ru')
                except Exception:
                    translated_context = None

                if translated_context:
                    messages.append({
                        "role": "system",
                        "content": t(self.language, "rag_context", context=translated_context)
                    })
                else:
                    messages.append({
                        "role": "system",
                        "content": t(self.language, "rag_context", context=context)
                    })
        
        # Добавляем историю
        for msg in self.history[-10:]:  # последние 10 сообщений
            messages.append({"role": msg.role, "content": msg.content})
        
        # CRITICAL: Question control instruction must be LAST system message before user
        # This ensures it takes precedence over all other instructions
        try:
            import random
            ask_flag = random.random() < float(self.ask_question_prob)
        except Exception:
            ask_flag = False

        if ask_flag:
            messages.append({
                "role": "system",
                "content": t(self.language, "response_instruction_ask")
            })
        else:
            messages.append({
                "role": "system",
                "content": t(self.language, "response_instruction_no_ask")
            })

        # Добавляем текущий запрос
        messages.append({"role": "user", "content": user_input})
        
        return messages


    def _translate_text(self, text: str, target_lang: str) -> str:
        """Translate `text` into `target_lang` ('en' or 'ru') using the LLM client.

        Returns translated text or original text on failure.
        """
        if not self.client:
            return text

        # Simple instruction for faithful translation without commentary
        target_label = 'English' if target_lang.startswith('en') else 'Russian'
        system_instr = f"You are a precise translator. Translate the following text to {target_label}. Preserve meaning and formatting; do not add commentary or explanations. Return only the translation."
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_instr},
                    {"role": "user", "content": text}
                ],
                temperature=0.0,
                max_tokens=2000,
            )
            translated = resp.choices[0].message.content
            return translated
        except Exception:
            return text
    
    def generate_response(self, user_input: str, temporary_system_instruction: Optional[str] = None, use_analysis_model: bool = False, model: Optional[str] = None) -> str:
        """Универсальный метод генерации ответа (для команд и чата).
        
        Если передана temporary_system_instruction, она полностью заменяет стандартный промпт терапевта.
        """
        if not self.client:
            return "Ошибка: LLM клиент не инициализирован."
        
        if not user_input or not user_input.strip():
            return "Ошибка: пустой запрос."
        
        # Если передана временная инструкция - используем её вместо стандартного промпта терапевта
        if temporary_system_instruction:
            messages = [
                {"role": "system", "content": temporary_system_instruction},
                {"role": "user", "content": user_input}
            ]
        else:
            messages = self._build_messages(user_input)
        
        # Validate messages is not empty
        if not messages:
            print(f"[GEN ERROR] Messages is empty for input: {user_input[:50]}...")
            return "Ошибка: не удалось построить сообщения для API."
        
        # Validate each message has required fields after potential insertion
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
                print(f"[GEN ERROR] Invalid message at index {i}: {msg}")
                return f"Ошибка: неверный формат сообщения #{i}."
        
        # Выбираем модель: приоритет у явного аргумента model, затем analysis_model, затем self.model
        if model:
            selected_model = model
        elif use_analysis_model:
            selected_model = self.analysis_model
        else:
            selected_model = self.model
        
        try:
            print(f"[GEN DEBUG] Sending {len(messages)} messages to API, model: {selected_model}")
            response = self.client.chat.completions.create(
                model=selected_model,
                messages=messages,
                temperature=0.8, # Чуть выше для метафор
                max_tokens=2000
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[GEN ERROR] API call failed: {type(e).__name__}: {e}")
            return f"Ошибка: {e}"



    def chat(self, user_input: str) -> str:
        """Основной метод чата."""
        if not self.client:
            return "Ошибка: LLM клиент не инициализирован. Проверьте API ключ."
        
        messages = self._build_messages(user_input)
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=2000
            )
            
            assistant_message = response.choices[0].message.content
            
            # Сохраняем в историю
            self.history.append(Message(role="user", content=user_input))
            self.history.append(Message(role="assistant", content=assistant_message))
            
            return assistant_message
            
        except Exception as e:
            return f"Ошибка: {e}"
    
    def chat_stream(self, user_input: str) -> Generator[str, None, None]:
        """Чат со стримингом."""
        if not self.client:
            yield "Ошибка: LLM клиент не инициализирован."
            return
        
        if not user_input or not user_input.strip():
            yield "Ошибка: пустой запрос."
            return
        
        messages = self._build_messages(user_input)
        
        # Validate messages is not empty
        if not messages:
            print(f"[STREAM ERROR] Messages is empty for input: {user_input[:50]}...")
            yield "Ошибка: не удалось построить сообщения для API."
            return
        
        # Validate each message has required fields
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
                print(f"[STREAM ERROR] Invalid message at index {i}: {msg}")
                yield f"Ошибка: неверный формат сообщения #{i}."
                return
        
        try:
            print(f"[STREAM DEBUG] Sending {len(messages)} messages to API, model: {self.model}")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=2000,
                stream=True
            )
            
            full_response = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response += content
                    yield content
            
            # Сохраняем в историю
            self.history.append(Message(role="user", content=user_input))
            self.history.append(Message(role="assistant", content=full_response))
            
        except Exception as e:
            print(f"[STREAM ERROR] API call failed: {type(e).__name__}: {e}")
            yield f"Ошибка: {e}"


    def analyze_image(self, image_url: str, user_input: str = "Что изображено на этой картинке?") -> str:
        """Анализ изображения."""
        full = ""
        for chunk in self.analyze_image_stream(image_url, user_input):
            full += chunk
        return full
    
    def analyze_image_stream(self, image_url: str, user_input: str = "Что изображено на этой картинке?"):
        """Потоковый анализ изображения — отдаёт чанки через yield."""
        if not self.client:
            yield "Ошибка: LLM клиент не инициализирован."
            return
            
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_input},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url,
                        },
                    },
                ],
            }
        ]
        
        try:
            response = self.client.chat.completions.create(
                model=os.getenv("VISION_MODEL", "claude-sonnet-5"),  # Используем модель с поддержкой vision
                messages=messages,
                max_tokens=500,
                stream=True
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            yield f"Ошибка анализа изображения: {e}"
    
    def transcribe_audio(self, file_path: str) -> str:       
        if not self.client:
            return "Ошибка: LLM клиент не инициализирован."
        
        try:
            with open(file_path, "rb") as audio_file:
                transcription = self.client.audio.transcriptions.create(
                    model=os.getenv("WHISPER_MODEL", "whisper-1"), 
                    file=audio_file
                )
            return transcription.text
        except Exception as e:
            return f"Ошибка распознавания: {e}"

    def generate_speech(self, text: str, output_path: str) -> str:
        """Генерация речи из текста (TTS)."""
        if not self.client:
            return "Ошибка: LLM клиент не инициализирован."
        
        try:
            response = self.client.audio.speech.create(
                model=os.getenv("TTS_MODEL", "tts-1"),
                voice="fable",
                input=text
            )
            response.stream_to_file(output_path)
            return output_path
        except Exception as e:
            return f"Ошибка генерации речи: {e}"    
    def analyze_associations(self, associations: dict[str, list[str]]) -> str:
        """Анализ ассоциаций пользователя."""
        full = ""
        for chunk in self.analyze_associations_stream(associations):
            full += chunk
        return self._clean_llm_response(full.strip())
    
    def analyze_associations_stream(self, associations: dict[str, list[str]]):
        """Потоковый анализ ассоциаций — отдаёт чанки через yield."""
        print(f"[ANALYZE] Starting association analysis for {len(associations)} categories")
        if not self.rag:
            print("[ANALYZE] RAG not available, returning error")
            yield "Анализ ассоциаций пока недоступен."
            return

        print("[ANALYZE] Analyzing associations with RAG...")
        try:
            # Translate association words to Russian if session is in English
            translated_associations = {}
            if self.language == "en":
                print("[ANALYZE] Translating English associations to Russian for RAG search...")
                for given, words in associations.items():
                    translated_words = []
                    for word in words:
                        translated = self._translate_text(word, target_lang='ru')
                        translated_words.append(translated)
                        print(f"[ANALYZE]   '{word}' -> '{translated}'")
                    translated_associations[given] = translated_words
            else:
                translated_associations = associations
            
            analysis = self.rag.analyze_user_associations(translated_associations)
        except Exception as e:
            print(f"[ANALYZE] RAG analysis failed: {type(e).__name__}: {e}")
            yield f"Ошибка анализа ассоциаций: {e}"
            return
        print(f"[ANALYZE] RAG analysis complete, found {len(analysis.get('matched_patterns', []))} patterns")

        # Определяем доминирующую данность по количеству ассоциаций и совпадениям в базе
        givens_scores = {"freedom": 0, "nonsense": 0, "solitude": 0, "death": 0}
        
        # Добавляем вес от совпадений в базе (более значимый фактор)
        for pattern in analysis['matched_patterns']:
            if pattern['givens'] in givens_scores:
                givens_scores[pattern['givens']] += pattern['count'] * 2.0  # Increased weight for RAG matches
        
        # Добавляем вес от количества введенных слов (всегда, но с меньшим весом)
        for given, words in associations.items():
            if given in givens_scores:
                givens_scores[given] += len(words) * 0.5  # Lower weight for word count

        # Get dominant given, with fallback if all scores are 0
        if any(givens_scores.values()):
            dominant_given = max(givens_scores.items(), key=lambda x: x[1])[0]
        else:
            # Fallback: use the given with most words entered by user
            word_counts = {g: len(associations.get(g, [])) for g in givens_scores.keys()}
            dominant_given = max(word_counts.items(), key=lambda x: x[1])[0] if any(word_counts.values()) else "freedom"
            print(f"[ANALYZE] Fallback dominant given (by word count): {dominant_given}")

        # Store last detected dominant given for session-scoped technique selection
        try:
            self.last_dominant_given = dominant_given
        except Exception:
            self.last_dominant_given = None
        
        # Техники для каждой данности
        techniques = {
            "freedom": [
                "Техника 'Ответственность за выбор' (исследование альтернатив)",
                "Упражнение 'Я должен -> Я выбираю'",
                "Анализ 'Здесь и сейчас' (осознание авторства своей жизни)"
            ],
            "nonsense": [
                "Логотерапевтический диалог (поиск смыслов в прошлом)",
                "Техника 'Дерегуляция' (парадоксальная интенция)",
                "Исследование ценностей творчества, переживания и отношения"
            ],
            "solitude": [
                "Исследование межличностных отношений (Я-Ты vs Я-Оно)",
                "Работа с изоляцией (принятие отдельности)",
                "Упражнение 'Встреча с собой' (медитативное осознание)"
            ],
            "death": [
                "Упражнение 'Эпитафия' (взгляд на жизнь с конца)",
                "Техника 'Разотождествление' (я не есть мое тело/роль)",
                "Исследование тревоги смерти как источника жизненной энергии"
            ]
        }
        
        suggested_techniques = techniques.get(dominant_given, [])
        
        # Формируем промпт для интерпретации на языке сессии
        if self.language == "en":
            prompt = f"""You are Irvin Yalom. Conduct a deep existential analysis of the client's associations.

Client's associations:
- Freedom: {', '.join(associations.get('freedom', []))}
- Meaninglessness: {', '.join(associations.get('nonsense', []))}
- Isolation: {', '.join(associations.get('solitude', []))}
- Death: {', '.join(associations.get('death', []))}

Patterns from the database (experiences of others):
{json.dumps(analysis['matched_patterns'][:5], ensure_ascii=False, indent=2)}

Dominant given: {dominant_given.upper()}

Your task:
1. Start your response STRICTLY with the phrase: "Dominant conflict with the given: {dominant_given.upper()}" (on a separate line).
2. Provide a powerful, professional interpretation. Avoid superficial comfort.
3. Use a metaphor that weaves these disparate words into a single existential knot.
4. Speak of the given as the inevitable background of life that has now emerged especially vividly.
5. End with ONE question that doesn't require a quick answer, but invites long silence and contemplation.
6. Use double line breaks (\n\n) to separate paragraphs in your response.

Style: Dense, intellectual, but deeply human. No "psychological fluff," only existential truth.
Forbidden: "this might mean", "I suggest", "try to". Speak affirmatively and directly.
IMPORTANT: Write only clean text. No HTML tags, no <br> tags. Use only regular line breaks."""
        else:
            prompt = f"""Ты — Ирвин Ялом. Проведи глубокий экзистенциальный анализ ассоциаций клиента.
        
Ассоциации клиента:
- Свобода: {', '.join(associations.get('freedom', []))}
- Бессмысленность: {', '.join(associations.get('nonsense', []))}
- Одиночество: {', '.join(associations.get('solitude', []))}
- Смерть: {', '.join(associations.get('death', []))}

Паттерны из базы данных (опыт других людей):
{json.dumps(analysis['matched_patterns'][:5], ensure_ascii=False, indent=2)}

Доминирующая данность: {dominant_given.upper()}

Твоя задача:
1. Начни ответ СТРОГО с фразы: "Доминирующий конфликт с данностью: {dominant_given.upper()}" (отдельной строкой).
2. Дай мощную, профессиональную интерпретацию. Избегай поверхностных утешений. 
3. Используй метафору, которая связывает эти разрозненные слова в единый экзистенциальный узел.
4. Говори о данности как о неизбежном фоне жизни, который сейчас проступил особенно ярко.
5. Заверши ОДНИМ вопросом, который не требует быстрого ответа, а приглашает к долгому молчанию и созерцанию.
6. Используй двойные переносы строк (\n\n) для разделения абзацев в ответе.

Стиль: Плотный, интеллектуальный, но глубоко человечный. Никакой "психологической ваты", только экзистенциальная правда.
Запрещено: "это может означать", "я предлагаю", "попробуйте". Говори утвердительно и прямо."""
        if not self.client:
            yield "Ошибка: LLM клиент не инициализирован."
            return
            
        # Используем премиум модель для глубокого анализа
        # Fallback to main model if analysis_model fails
        try:
            response = self.client.chat.completions.create(
                model=self.analysis_model,
                messages=[
                    {"role": "system", "content": t(self.language, "irvin_yalom_assoc_analysis")},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=3000,
                stream=True
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            return
        except Exception as api_error:
            # Fallback to main model if analysis model fails
            print(f"[ANALYZE] Analysis model failed, trying main model: {api_error}")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": t(self.language, "irvin_yalom_assoc_analysis")},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=3000,
                stream=True
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            print(f"[ANALYZE] Error in analyze_associations_stream: {type(e).__name__}: {e}")
            yield f"Ошибка: {e}"    
    def _clean_llm_response(self, text: str) -> str:        
        """Очистка ответа LLM от тегов <br> и нормализация пробелов."""
        import re
        import html
        
        # 1. Декодируем HTML-сущности (например, <br> -> <br>)
        text = html.unescape(text)
        
        # 2. Заменяем <br>, <br/>, <br /> на перенос строки
        text = re.sub(r'<(br|BR)\s*/?>', '\n', text)

        # 3. Нормализация: убираем лишние пустые строки (более 2 подряд)
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()    
    
    def analyze_story(self, story: str) -> str:
        """Анализ истории."""
        full = ""
        for chunk in self.analyze_story_stream(story):
            full += chunk
        content = full.strip()
        if not content:
            return "Ошибка: LLM вернул пустой ответ. Попробуйте ещё раз."
        return content
    
    def analyze_story_stream(self, story: str):
        """Потоковый анализ истории — отдаёт чанки через yield."""
        print(f"[ANALYZE] Starting story analysis, story length: {len(story)} chars")
        if not self.rag:
            print("[ANALYZE] RAG not available for story analysis, returning error")
            yield "Анализ истории пока недоступен."
            return

        # Translate story to Russian if session is in English (RAG database is in Russian)
        story_for_rag = story
        if self.language == "en":
            print("[ANALYZE] Translating story to Russian for RAG search...")
            story_for_rag = self._translate_text(story, target_lang='ru')
            if story_for_rag != story:
                print(f"[ANALYZE] Story translated successfully")
            else:
                print("[ANALYZE] Story translation failed or not needed, using original")

        # Ищем похожие истории в базе
        print("[ANALYZE] Searching for similar narratives in RAG...")
        try:
            similar_stories = self.rag.search_similar_narratives(story_for_rag, n_results=3)
        except Exception as e:
            print(f"[ANALYZE] RAG search failed: {type(e).__name__}: {e}")
            similar_stories = []
        print(f"[ANALYZE] Found {len(similar_stories)} similar stories")
        
        context = ""
        if similar_stories:
            # Build context in Russian (from RAG database)
            context_ru = "Похожие истории из базы знаний:\n"
            for i, res in enumerate(similar_stories, 1):
                context_ru += f"[{i}] {res.content[:300]}...\n"
            
            # Translate context to English if session is in English
            if self.language == "en":
                context = self._translate_text(context_ru, target_lang='en')
                print(f"[ANALYZE] Translated RAG context to English")
            else:
                context = context_ru

        # Determine dominant given from RAG results, keyword detection, or LLM analysis
        detected = None
        try:
            # Try to infer from RAG results - count givens from similar stories
            if similar_stories:
                givens_scores = {"freedom": 0, "nonsense": 0, "solitude": 0, "death": 0}
                for story_result in similar_stories:
                    metadata = story_result.metadata
                    # Check if metadata has associations info
                    if metadata:
                        assoc_str = metadata.get("associations", "{}")
                        try:
                            import json
                            assoc = json.loads(assoc_str) if isinstance(assoc_str, str) else assoc_str
                            for given in ["freedom", "nonsense", "solitude", "death"]:
                                if assoc.get(given):
                                    givens_scores[given] += 1
                        except Exception:
                            pass
                    # Also check source type as hint
                    source_type = metadata.get("type", "") if metadata else ""
                    if source_type:
                        for given in ["freedom", "nonsense", "solitude", "death"]:
                            if given in source_type.lower():
                                givens_scores[given] += 0.5
                
                # Get dominant from RAG scores if we found anything
                if any(givens_scores.values()):
                    detected = max(givens_scores.items(), key=lambda x: x[1])[0]
                    print(f"[ANALYZE] Dominant given from RAG: {detected}")
            
            # Enhanced keyword detection with more comprehensive patterns
            if not detected:
                s = story.lower()
                
                # Death keywords (expanded)
                death_keywords = (
                    "смерт", "умира", "умру", "конечн", "похорон", "кладбищ", "утрат", "гроб", "похорон", "умер", "умерла",
                    "потерял", "потеряла", "не стало", "не стал", "конец жизни", "тлен", "могил", "прах",
                    "death", "dying", "mortality", "funeral", "loss", "passed away", "deceased", "grave", "buried"
                )
                
                # Freedom keywords (expanded)
                freedom_keywords = (
                    "свобод", "выбор", "решен", "ответствен", "виноват", "вину", "должен", "обязан", "право", "воля",
                    "самостоятельн", "независим", "контроль", "власть", "автоном", "самоопредел",
                    "freedom", "choice", "decision", "responsibility", "guilt", "must", "should", "autonomy", "control"
                )
                
                # Solitude keywords (expanded)
                solitude_keywords = (
                    "одинок", "одиноч", "изолир", "разрыв", "бросил", "никто не", "отверг", "отвергнут", "покинут",
                    "отстранен", "отделен", "в одиночку", "нет рядом", "отсутствие", "пустота внутри",
                    "lonely", "loneliness", "isolat", "abandoned", "nobody", "alone", "rejected", "apart", "separated"
                )
                
                # Meaninglessness keywords (expanded)
                nonsense_keywords = (
                    "смысл", "бессмыс", "пустот", "зачем", "ради чего", "непонятно", "абсурд", "тоска", "апатия",
                    "ничего не хочется", "нет цели", "нет смысла", "пусто", "отсутствие смысла", "вакуум",
                    "meaning", "meaningless", "purpose", "empty", "why bother", "absurd", "apathy", "void", "pointless"
                )
                
                # Score each category
                scores = {
                    "death": sum(1 for k in death_keywords if k in s),
                    "freedom": sum(1 for k in freedom_keywords if k in s),
                    "solitude": sum(1 for k in solitude_keywords if k in s),
                    "nonsense": sum(1 for k in nonsense_keywords if k in s)
                }
                
                print(f"[ANALYZE] Keyword scores: {scores}")
                
                if any(scores.values()):
                    detected = max(scores.items(), key=lambda x: x[1])[0]
                    print(f"[ANALYZE] Dominant given from keywords: {detected}")
            
            # Final fallback: use LLM to detect if still not detected
            if not detected and self.client:
                print(f"[ANALYZE] Attempting LLM-based detection...")
                try:
                    detection_prompt = f"""Analyze this story and determine which existential given is most prominent.

Story: "{story[:500]}..."

Choose ONE from: death, freedom, solitude, nonsense (meaninglessness).

Respond with ONLY the single word (death, freedom, solitude, or nonsense). No explanation."""

                    detection_response = self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": "You are an expert in existential psychology. Analyze stories and identify the dominant existential given."},
                            {"role": "user", "content": detection_prompt}
                        ],
                        temperature=0.0,
                        max_tokens=50
                    )
                    
                    llm_result = detection_response.choices[0].message.content.strip().lower()
                    print(f"[ANALYZE] LLM detection result: {llm_result}")
                    
                    # Validate the result
                    if llm_result in ["death", "freedom", "solitude", "nonsense"]:
                        detected = llm_result
                        print(f"[ANALYZE] Dominant given from LLM: {detected}")
                except Exception as llm_err:
                    print(f"[ANALYZE] LLM detection failed: {llm_err}")
            
            self.last_dominant_given = detected
            if not detected:
                print(f"[ANALYZE] WARNING: Could not detect dominant given")
        except Exception as e:
            print(f"[ANALYZE] Error detecting dominant given: {e}")
            self.last_dominant_given = None

        # Формируем промпт на языке сессии
        if self.language == "en":
            prompt = f"""You are Irvin Yalom. Conduct a deep existential analysis of the client's story.

Client's story:
"{story}"

{context}

Your task:
1. Start your response STRICTLY with the phrase: "Dominant conflict with the given: {detected.upper() if detected else 'UNDETERMINED'}" (on a separate line).
2. Provide a powerful, professional interpretation in Yalom's style.
3. Reflect the client's feelings through a deep metaphor.
4. Connect the story to a specific given, showing it as the root of current concern.
5. If there is context from the database (above), weave it in as confirmation of the universality of this suffering.
6. End with ONE question that will make the client fall silent and look inside themselves.
7. Use double line breaks (\n\n) to separate paragraphs in your response.

Style: Direct, empathetic, but devoid of sentimentality. Speak in first person. No "psychological fluff."
Forbidden: "it seems to me", "perhaps", "I would like to suggest". Speak as a therapist who sees the essence.
IMPORTANT: Write only clean text. No HTML tags, no <br> tags. Use only regular line breaks."""
        else:
            prompt = f"""Ты — Ирвин Ялом. Проведи глубокий экзистенциальный анализ истории клиента.

История клиента:
"{story}"

{context}

Твоя задача:
1. Начни ответ СТРОГО с фразы: "Доминирующий конфликт с данностью: {detected.upper() if detected else 'НЕ ОПРЕДЕЛЕНО'}" (отдельной строкой).
2. Дай мощную, профессиональную интерпретацию в стиле Ялома. 
3. Отрази чувства клиента через глубокую метафору.
4. Свяжи историю с определенной данностью, показав её как корень текущего беспокойства.
5. Если есть контекст из базы (выше), вплети его как подтверждение универсальности этого страдания.
6. Заверши ОДНИМ вопросом, который заставит клиента замолчать и заглянуть внутрь себя.
7. Используй двойные переносы строк (\n\n) для разделения абзацев в ответе.

Стиль: Прямой, эмпатичный, но лишенный сентиментальности. Говори от первого лица. Никакой "психологической ваты".
Запрещено: "мне кажется", "возможно", "я бы хотел предложить". Говори как терапевт, который видит суть.
ВАЖНО: Пиши только чистый текст. Никаких HTML-тегов, никаких <br>. Используй только обычные переносы строк."""
        if not self.client:
            yield "Ошибка: LLM клиент не инициализирован."
            return
            
        # Используем премиум модель для глубокого анализа истории
        # Fallback to main model if analysis_model fails
        try:
            response = self.client.chat.completions.create(
                model=self.analysis_model,
                messages=[
                    {"role": "system", "content": t(self.language, "irvin_yalom_story_analysis")},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=3000,
                stream=True
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            return
        except Exception as api_error:
            # Fallback to main model if analysis model fails
            print(f"[ANALYZE] Analysis model failed in analyze_story, trying main model: {api_error}")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": t(self.language, "irvin_yalom_story_analysis")},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=3000,
                stream=True
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            print(f"[ANALYZE] Error in analyze_story_stream: {type(e).__name__}: {e}")
            yield f"Ошибка: {e}"
    def reset(self):

        """Сброс истории диалога."""
        self.history = []
        print("�?стория диалога сброшена")


def main():
    """Тестирование бота."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Экзистенциальный терапевт-бот")
    parser.add_argument("--model", default="deepseek-v4-pro", help="Модель LLM для чата")

    parser.add_argument("--analysis-model", default="deepseek-v4-pro", help="Модель LLM для анализов")
    parser.add_argument("--no-rag", action="store_true", help="Отключить RAG")
    args = parser.parse_args()
    
    bot = ExistentialTherapistBot(
        model=args.model,
        analysis_model=args.analysis_model,
        use_rag=not args.no_rag
    )

    
    print("\n" + "="*50)
    print("Экзистенциальный терапевт-бот")
    print("Команды: 'quit' - выход, 'reset' - сброс, 'assoc' - анализ ассоциаций")
    print("="*50 + "\n")
    
    while True:
        try:
            user_input = input("Вы: ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() == 'quit':
                print("До свидания.")
                break
            
            if user_input.lower() == 'reset':
                bot.reset()
                continue
            
            if user_input.lower() == 'assoc':
                print("\nВведите ассоциации (через запятую):")
                freedom = input("Свобода: ").strip().split(',')
                nonsense = input("Бессмысленность: ").strip().split(',')
                solitude = input("Одиночество: ").strip().split(',')
                death = input("Смерть: ").strip().split(',')
                
                associations = {
                    "freedom": [a.strip().lower() for a in freedom if a.strip()],
                    "nonsense": [a.strip().lower() for a in nonsense if a.strip()],
                    "solitude": [a.strip().lower() for a in solitude if a.strip()],
                    "death": [a.strip().lower() for a in death if a.strip()]
                }
                
                print("\nТерапевт: ", end="")
                print(bot.analyze_associations(associations))
                continue
            
            print("\nТерапевт: ", end="")
            for chunk in bot.chat_stream(user_input):
                print(chunk, end="", flush=True)
            print("\n")
            
        except KeyboardInterrupt:
            print("\nДо свидания.")
            break


if __name__ == "__main__":
    main()
