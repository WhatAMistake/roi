"""
Existential therapist bot.
LLM + RAG + System Prompt integration.
"""


import os
import json
from pathlib import Path
from typing import Optional, Generator
from dataclasses import dataclass

from dotenv import load_dotenv
from i18n import t
from lang_utils import detect_language

load_dotenv()



@dataclass
class Message:
    role: str
    content: str


class ExistentialTherapistBot:
    """Existential therapist bot."""

    
    def __init__(
        self,
        model: str = "gpt-4o-mini",  # основная модель для чата
        analysis_model: str = "claude-3-opus-latest",  # модель для анализов
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
        self.system_prompt = self._load_system_prompt()
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
        """Load system prompt from file."""

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
        """Default prompt if file not found."""

        return """Ты — эмпатичный экзистенциальный психотерапевт в традиции Ирвина Ялома. Твоё имя — Рой (или Рои).
    Помогай клиенту исследовать экзистенциальные данности: смерть, свободу, одиночество, бессмысленность.
    Не давай советов, задавай открытые вопросы, используй феноменологическое слушание.

    Важно: не задавай уточняющие вопросы автоматически в конце каждого ответа. Задавай вопрос только если он действительно помогает продвижению терапии (прояснить противоречие, открыть новый ракурс или прояснить ключевой момент). В остальных случаях делай отражение и краткое исследование без вопроса. Можно задавать глубокий вопрос в ~20–35% ответов, но только если он уместен и не звучит формально."""
    def _init_rag(self, data_dir: Optional[str]):
        """Initialize RAG."""
        try:
            from rag import ExistentialRAG
            self.rag = ExistentialRAG(data_dir=data_dir)
            print("RAG initialized")
        except Exception as e:
            print(f"RAG unavailable: {e}")
            self.rag = None

    
    def _init_llm(self):
        """Initialize LLM client."""
        try:
            from openai import OpenAI
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base
            )
            print(f"LLM client initialized: {self.model}")
        except ImportError:
            print("openai not installed. Install: pip install openai")
            self.client = None


    def select_technique(self, user_input: str) -> Optional[str]:
        """Choose technique based on user input keywords."""
        import random
        
        # Prefer last detected dominant given if available
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
        
        # Death -> epitaph
        if any(k in s for k in ("смерт", "умира", "умру", "конечн", "похорон", "кладбищ", "утрат", "death", "dying", "mortality", "funeral", "loss")):
            return "epitaph"
        
        # Freedom -> behavioral
        if any(k in s for k in ("свобод", "выбор", "решен", "ответствен", "виноват", "вину", "freedom", "choice", "decision", "responsibility", "guilt")):
            return "behavioral"
            
        # Isolation -> narrative
        if any(k in s for k in ("одинок", "одиноч", "изолир", "разрыв", "бросил", "никто не", "lonely", "loneliness", "isolat", "abandoned", "nobody")):
            return "narrative"
            
        # Meaninglessness -> logotherapy
        if any(k in s for k in ("смысл", "бессмыс", "пустот", "зачем", "ради чего", "meaning", "meaningless", "purpose", "empty", "why bother")):
            return "logotherapy"

        # Anxiety -> mindfulness or grounding
        if any(k in s for k in ("тревог", "тревож", "паник", "страх", "паника", "anxiety", "panic", "afraid", "fear")):
            return random.choice(["mindfulness", "grounding"])
            
        # Somatic -> somatic
        if any(k in s for k in ("тело", "телесн", "груди", "дыхан", "сердце", "живот", "сжимает", "трясет", "body", "somatic", "breath", "chest", "heart", "stomach", "shaking")):
            return "somatic"

        # Intense emotions -> labeling or scaling
        if any(k in s for k in ("очень", "сильно", "невыносим", "больно", "злюсь", "гнев", "ярость", "грусть", "печаль", "very", "intense", "unbearable", "pain", "angry", "rage", "sadness")):
            return random.choice(["labeling", "scaling"])

        # Avoidance -> paradox
        if any(k in s for k in ("избег", "избегаю", "не делаю", "откладыв", "avoid", "avoiding", "avoidance", "procrastin")):
            return "paradox"

        # Fallback
        if len(s.split()) < 6:
            return random.choice(["grounding", "labeling"])
            
        return "socratic"

    def _build_messages(self, user_input: str) -> list[dict]:
        """Build messages for API call."""
        clean_text_instr = "\n\nВАЖНО: Только чистый текст. Без звёздочек *, без жирного текста, без markdown-форматирования. HTML теги разрешены только если они явно нужны для структуры." if self.language == "ru" else "\n\nIMPORTANT: Clean text only. No asterisks *, no bold text, no markdown formatting. HTML tags allowed only when explicitly needed for structure."
        
        messages = [{"role": "system", "content": self.system_prompt + clean_text_instr}]
        
        # Keyword-based RAG for associations
        assoc_context = []
        words = [w.strip().lower() for w in user_input.replace(',', ' ').replace(';', ' ').split() if len(w) > 3]
        if self.rag:
            for word in words:
                matches = self.rag.search_associations(word)
                if matches:
                    for m in matches[:2]:
                        if m.get('narratives', {}).get('free_form'):
                            assoc_context.append(f"Person with similar association ('{word}') on topic '{m['matched_givens']}': {m['narratives']['free_form']}")
        
        if assoc_context:
            messages.append({
                "role": "system",
                "content": "Context from others with similar associations:\n" + "\n---\n".join(assoc_context[:3])
            })

        # Randomization for asking questions
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

        # Add therapeutic technique suggestion
        try:
            tech = self.select_technique(user_input)
            if tech:
                tech_label = tech
                try:
                    tech_desc = __import__('i18n').i18n.t(self.language, f"technique_{tech_label}")
                except Exception:
                    tech_desc = tech_label
                messages.append({
                    "role": "system",
                    "content": t(self.language, "incorporate_technique", tech_desc=tech_desc)
                })
        except Exception:
            pass
        
        # Add RAG context with optional translation
        if self.rag and self.use_rag:
            context = self.rag.get_context_for_query(user_input)
            if context:
                try:
                    code, prob = detect_language(context)
                except Exception:
                    code, prob = None, 0.0

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
        
        # Add history (last 10 messages)
        for msg in self.history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
        
        messages.append({"role": "user", "content": user_input})
        
        return messages


    def _translate_text(self, text: str, target_lang: str) -> str:
        """Translate text to target language using LLM."""
        if not self.client:
            return text

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

    
    def generate_response(self, user_input: str, temporary_system_instruction: Optional[str] = None, use_analysis_model: bool = False) -> str:
        """Generate response for chat or commands."""
        if not self.client:
            return "Error: LLM client not initialized."
        
        if not user_input or not user_input.strip():
            return "Error: empty query."
        
        if temporary_system_instruction:
            messages = [
                {"role": "system", "content": temporary_system_instruction},
                {"role": "user", "content": user_input}
            ]
        else:
            messages = self._build_messages(user_input)
        
        if not messages:
            print(f"[GEN ERROR] Messages is empty for input: {user_input[:50]}...")
            return "Error: failed to build messages for API."
        
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
                print(f"[GEN ERROR] Invalid message at index {i}: {msg}")
                return f"Error: invalid message format #{i}."
        
        model = self.analysis_model if use_analysis_model else self.model
        
        try:
            print(f"[GEN DEBUG] Sending {len(messages)} messages to API, model: {model}")
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.8,
                max_tokens=1000
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[GEN ERROR] API call failed: {type(e).__name__}: {e}")
            return f"Error: {e}"




    def chat(self, user_input: str) -> str:
        """Main chat method."""
        if not self.client:
            return "Error: LLM client not initialized. Check API key."
        
        messages = self._build_messages(user_input)
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=1000
            )
            
            assistant_message = response.choices[0].message.content
            self.history.append(Message(role="user", content=user_input))
            self.history.append(Message(role="assistant", content=assistant_message))
            
            return assistant_message
            
        except Exception as e:
            return f"Error: {e}"

    
    def chat_stream(self, user_input: str) -> Generator[str, None, None]:
        """Chat with streaming."""
        if not self.client:
            yield "Error: LLM client not initialized."
            return
        
        if not user_input or not user_input.strip():
            yield "Error: empty query."
            return
        
        messages = self._build_messages(user_input)
        
        if not messages:
            print(f"[STREAM ERROR] Messages is empty for input: {user_input[:50]}...")
            yield "Error: failed to build messages for API."
            return
        
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
                print(f"[STREAM ERROR] Invalid message at index {i}: {msg}")
                yield f"Error: invalid message format #{i}."
                return
        
        try:
            print(f"[STREAM DEBUG] Sending {len(messages)} messages to API, model: {self.model}")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=1000,
                stream=True
            )
            
            full_response = ""
            for chunk in response:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response += content
                    yield content
            
            self.history.append(Message(role="user", content=user_input))
            self.history.append(Message(role="assistant", content=full_response))
            
        except Exception as e:
            print(f"[STREAM ERROR] API call failed: {type(e).__name__}: {e}")
            yield f"Error: {e}"



    def analyze_image(self, image_url: str, user_input: str = "What is shown in this image?") -> str:
        """Analyze image."""
        if not self.client:
            return "Error: LLM client not initialized."
            
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
                model="gpt-4o",
                messages=messages,
                max_tokens=500,
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error analyzing image: {e}"

    
    def transcribe_audio(self, file_path: str) -> str:       
        if not self.client:
            return "Error: LLM client not initialized."
        
        try:
            with open(file_path, "rb") as audio_file:
                transcription = self.client.audio.transcriptions.create(
                    model="whisper-1", 
                    file=audio_file
                )
            return transcription.text
        except Exception as e:
            return f"Error transcribing: {e}"


    def generate_speech(self, text: str, output_path: str) -> str:
        """Generate speech from text (TTS)."""
        if not self.client:
            return "Error: LLM client not initialized."
        
        try:
            response = self.client.audio.speech.create(
                model="tts-1",
                voice="fable",
                input=text
            )
            response.stream_to_file(output_path)
            return output_path
        except Exception as e:
            return f"Error generating speech: {e}"

    def analyze_associations(self, associations: dict[str, list[str]]) -> str:
        """Анализ ассоциаций пользователя."""
        print(f"[ANALYZE] Starting association analysis for {len(associations)} categories")
        if not self.rag:
            print("[ANALYZE] RAG not available, returning error")
            return "Анализ ассоциаций пока недоступен."

        print("[ANALYZE] Analyzing associations with RAG...")
        try:
            analysis = self.rag.analyze_user_associations(associations)
        except Exception as e:
            print(f"[ANALYZE] RAG analysis failed: {type(e).__name__}: {e}")
            return f"Ошибка анализа ассоциаций: {e}"
        print(f"[ANALYZE] RAG analysis complete, found {len(analysis.get('matched_patterns', []))} patterns")
        
        # Определяем доминирующую данность по количеству ассоциаций и совпадений
        givens_scores = {"freedom": 0, "nonsense": 0, "solitude": 0, "death": 0}
        
        # Считаем количество введенных слов
        for given, words in associations.items():
            if given in givens_scores:
                givens_scores[given] += len(words)
        
        # Добавляем вес от совпадений в базе
        for pattern in analysis['matched_patterns']:
            if pattern['givens'] in givens_scores:
                givens_scores[pattern['givens']] += pattern['count'] * 0.5
        
        dominant_given = max(givens_scores.items(), key=lambda x: x[1])[0]
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
        
        # Формируем промпт для интерпретации
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

Стиль: Плотный, интеллектуальный, но глубоко человечный. Никакой "психологической ваты", только экзистенциальная правда.
Запрещено: "это может означать", "я предлагаю", "попробуйте". Говори утвердительно и прямо."""
        
        if not self.client:
            return "Ошибка: LLM клиент не инициализирован."
            
        try:
            # Используем премиум модель для глубокого анализа
            # Fallback to main model if analysis_model fails
            model_to_use = self.analysis_model
            try:
                response = self.client.chat.completions.create(
                    model=model_to_use,
                    messages=[{"role": "system", "content": prompt}],
                    temperature=0.7,
                    max_tokens=1500
                )
            except Exception as api_error:
                # Fallback to main model if analysis model fails
                print(f"[ANALYZE] Analysis model failed, trying main model: {api_error}")
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": prompt}],
                    temperature=0.7,
                    max_tokens=1500
                )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[ANALYZE] Error in analyze_associations: {type(e).__name__}: {e}")
            return f"Ошибка: {e}"
    def analyze_story(self, story: str) -> str:
        """Анализ истории пользователя."""
        print(f"[ANALYZE] Starting story analysis, story length: {len(story)} chars")
        if not self.rag:
            print("[ANALYZE] RAG not available for story analysis, returning error")
            return "Анализ истории пока недоступен."

        # Ищем похожие истории в базе
        print("[ANALYZE] Searching for similar narratives in RAG...")
        try:
            similar_stories = self.rag.search_similar_narratives(story, n_results=3)
        except Exception as e:
            print(f"[ANALYZE] RAG search failed: {type(e).__name__}: {e}")
            similar_stories = []
        print(f"[ANALYZE] Found {len(similar_stories)} similar stories")
        
        context = ""
        if similar_stories:
            context = "Похожие истории из базы знаний:\n"
            for i, res in enumerate(similar_stories, 1):
                context += f"[{i}] {res.content[:300]}...\n"
        
        # Simple heuristic to detect dominant given from story text for technique selection
        try:
            s = story.lower()
            if any(k in s for k in ("смерт", "умира", "умру", "конечн", "death", "dying", "mortality")):
                detected = "death"
            elif any(k in s for k in ("свобод", "выбор", "ответственность", "freedom", "choice")):
                detected = "freedom"
            elif any(k in s for k in ("одинок", "одиноч", "изолир", "lonely", "isolation")):
                detected = "solitude"
            elif any(k in s for k in ("смысл", "бессмыс", "пустот", "meaning", "meaningless")):
                detected = "nonsense"
            else:
                detected = None
            self.last_dominant_given = detected
        except Exception:
            self.last_dominant_given = None

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

Стиль: Прямой, эмпатичный, но лишенный сентиментальности. Говори от первого лица. Никакой "психологической ваты".
Запрещено: "мне кажется", "возможно", "я бы хотел предложить". Говори как терапевт, который видит суть."""
        
        if not self.client:
            return "Ошибка: LLM клиент не инициализирован."
            
        try:
            # Используем премиум модель для глубокого анализа истории
            # Fallback to main model if analysis_model fails
            try:
                response = self.client.chat.completions.create(
                    model=self.analysis_model,
                    messages=[{"role": "system", "content": prompt}],
                    temperature=0.7,
                    max_tokens=1500
                )
            except Exception as api_error:
                # Fallback to main model if analysis model fails
                print(f"[ANALYZE] Analysis model failed in analyze_story, trying main model: {api_error}")
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": prompt}],
                    temperature=0.7,
                    max_tokens=1500
                )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[ANALYZE] Error in analyze_story: {type(e).__name__}: {e}")
            return f"Ошибка: {e}"
    def reset(self):

        """Сброс истории диалога."""
        self.history = []
        print("История диалога сброшена")


def main():
    """Тестирование бота."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Экзистенциальный терапевт-бот")
    parser.add_argument("--model", default="gpt-4o-mini", help="Модель LLM для чата")
    parser.add_argument("--analysis-model", default="claude-3-opus-latest", help="Модель LLM для анализов")
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
