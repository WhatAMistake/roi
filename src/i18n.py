import logging
from typing import Dict

CONSENT_VERSION = "2026-11-08-music-v2"
logger = logging.getLogger(__name__)

LOCALES: Dict[str, Dict[str, str]] = {
    "ru": {
        "button_assoc": "Анализ ассоциаций",
        "button_analyze": "Анализ истории",
        "button_filmframe": "Снимок на плёнку",
        "button_help": "Помощь",
        "button_cancel": "Отмена",
        "consent_intro": "Доброго времени, {name}. Я — Рой, ИИ-собеседник с терапевтическим профилем.",
        "consent_notice": "Мы можем говорить о личном, поэтому мне требуется согласие на обработку сообщений. Подробнее в /privacy.\n\nЕсли вам уже есть 18 и вы согласны, можем начать.",
        "consent_yes": "Да, давай начнём",
        "consent_no": "Нет, пока не готов",
        "consent_accepted": "Спасибо! Теперь можно и представиться.",
        "consent_declined": "Понимаю. Тогда пока остановимся здесь — без согласия я не могу продолжить беседу. Если передумаете, просто нажмите ещё раз /start.",
        "consent_already": "Мы уже можем общаться. О чём вам хочется поговорить?",
        "consent_stale": "Эта кнопка уже устарела. Нажмите /start, откроется актуальное приглашение.",
        "consent_save_failed": "Не получилось сохранить ваш выбор. Пока начать не сможем — попробуйте нажать кнопку ещё раз чуть позже.",
        "consent_private": "Давайте продолжим в личном чате с Роем.",
        "privacy_revoke_prompt": "Отозвать согласие и удалить доступные привязанные к вам данные беседы и настроек? Это действие нельзя отменить. Копии в Telegram, у внешних сервисов, в резервных копиях и технических журналах могут сохраниться.",
        "privacy_revoke_yes": "Подтверждаю",
        "privacy_revoke_no": "Вернуться",
        "privacy_revoke_cancelled": "Отмена. Удаление не запускалось.",
        "privacy_revoke_stale": "Эта кнопка больше не действует. Используйте /privacy revoke ещё раз.",
        "privacy_revoke_busy": "Удаление ещё не завершено. Дождитесь результата или повторите /privacy revoke.",
        "privacy_revoke_failed": "Не удалось завершить удаление всех данных. Общение приостановлено; пожалуйста, повторите /privacy revoke позже.",
        "privacy_revoke_done": "Согласие отозвано, доступные привязанные к вам данные приложения удалены. Для новой беседы потребуется снова дать согласие. Копии в Telegram, у внешних сервисов, в резервных копиях и технических журналах могут сохраняться.",
        "consent_details": (
            f"Согласие на обработку данных для работы бота «Рой»\nВерсия: {CONSENT_VERSION}\n\n"
            "Нажимая «Да, давай начнём», я подтверждаю, что мне исполнилось 18 лет, и соглашаюсь на обработку отправленных мной сообщений и данных Telegram создателем бота для подготовки ответов и работы выбранных функций.\n\n"
            "Для этой цели бот получает идентификатор Telegram и сведения профиля, сообщения, а при использовании соответствующих функций — голосовые записи, фотографии и описания для генерации изображений. Содержимое передаётся внешним ИИ-сервисам для подготовки ответа, анализа, распознавания/синтеза речи или генерации изображения.\n\n"
            "Текущая версия хранит настройки, сведения об активности, недавние сообщения и автоматически подготовленные резюме в зашифрованном файле вместе с идентификаторами Telegram. Администратору доступны служебные сведения и сохранённые рабочие данные бота. При использовании генерации изображений описания и промпты могут попадать в технический журнал. Telegram и ИИ-провайдеры также могут хранить собственные копии по своим правилам. Отсутствие хранения и полная анонимность не обещаются.\n\n"
            "Рой может иногда автоматически отправлять авторские MP3 по теме беседы без отдельного подтверждения перед каждой отправкой. /music off отключает инициативную музыку, /music on включает её; вручную запросить музыку можно через /music. По умолчанию инициативная музыка ограничена одной отправкой за сессию и не чаще раза в семь дней; настройка сервера может изменить интервал. При подтверждённой неудаче попытку можно повторить. Telegram обрабатывает отправленные аудиофайлы по своим правилам.\n\n"
            "В отдельной незашифрованной базе хранятся HMAC-псевдоним идентификатора пользователя, ID композиций и статусы отправки или резервирования, состояние кнопок и сессии, настройка инициативной музыки и время последней попытки. Псевдонимизация не делает эти данные анонимными. История музыки нужна для предотвращения повторов и сохраняется после /reset. Автоматический срок удаления сохранённых настроек, резюме и музыкальной истории не установлен.\n\n"
            "Выбор добровольный. При отказе разговорные функции недоступны; к выбору можно вернуться через /start. /reset сбрасывает разговорный контекст, но не удаляет все данные и внешние копии. Это согласие не даёт отдельного разрешения на публикацию переписки или формирование исследовательского датасета.\n\n"
            "Не отправляйте документы, данные других людей и сведения, которые не готовы передать внешним сервисам. Рой не заменяет специалиста и не является экстренной службой.\n\n"
            "Это краткое описание текущей работы бота и фиксация выбора при подключении, а не подтверждение полного соответствия 152-ФЗ или письменное согласие на специальные категории данных.\n"
            "\nЧтобы отозвать согласие и запросить удаление доступных привязанных к вам данных бота, используйте /privacy revoke и подтвердите действие. Копии в Telegram, у внешних сервисов, в резервных копиях и технических журналах могут сохраниться.\n"
        ),
        "welcome": "\n\nЯ — Рой (или Рои), ИИ-собеседник в экзистенциальном подходе терапии. Помогаю исследовать вопросы смысла, свободы, одиночества и конечности жизни; впрочем, убеждён, что не каждому разговору нужен глубокий анализ.\n\nВы можете:\n• Просто писать мне о том, что вас беспокоит (можно и голосовым, отвечу им же)\n• Использовать анализ ассоциаций (/assoc) — это поможет понять актуальные темы\n• Использовать анализ истории (/lysis) — для глубокого разбора ситуации\n\n• Заглянуть в /help за остальными командами\n\nС чего начнём?",
        "help": "<b>Экзистенциальный терапевт Рой (Рои)</b>\n\nПо большей части я работаю в традиции Ирвина Ялома и Виктора Франкла.\n\n<b>Что я могу:</b>\n• Слушать и задавать вопросы\n• Фотографировать вас на плёнку\n• Помогать исследовать экзистенциальные темы\n• Анализировать ассоциации и подробные истории\n• Делиться музыкой — по запросу или когда это уместно\n• Анализировать изображения — пришлите фото для экзистенциального отклика\n\n<b>Команды:</b>\n/assoc — анализ ассоциаций\n/lysis — анализ истории\n\n<b>Для особых моментов:</b>\n/void — взгляд в пустоту\n/silence — минута молчания\n/meaning — случайный смысл момента\n/remarque — наблюдение по недавнему диалогу\n\n<b>Немного медиа:</b>\n/shoot — плёночное фото\n/music — как могли бы звучать данности\n\n<b>Служебные:</b>\n/reset — сбросить историю\n/switchlang — переключить язык (ru, en)\n/meaning_where — где смысл\n\n/privacy — о согласии и обработке данных\n/feedback — отправить отзыв разработчику\n\n<i>Важно: я не заменяю профессионального терапевта. Если вы в кризисе — обратитесь к специалисту, пожалуйста.</i>",        
        "reset_confirm": "История диалога сброшена. Начнём сначала?",
        "assoc_start": "Давайте исследуем ваши ассоциации.\n\n<b>Подберите ассоциации к слову Свобода (оптимально 5)</b>\n\n(через запятую или пробел)",
        "assoc_nonsense_prompt": "<b>Подберите ассоциации к слову Бессмысленность</b>",
        "assoc_solitude_prompt": "<b>Подберите ассоциации к слову Одиночество</b>",
        "assoc_death_prompt": "<b>И наконец, подберите ассоциации к слову Смерть</b>",
        "assoc_confirm": "Приняты по данности {label}: {words}",
        "assoc_summary_intro": "<b>Ваши ассоциации:</b>",
        "analyzing": "<i>Анализирую...</i>",
        "error_generation_incomplete": "Не удалось завершить генерацию ответа. Попробуйте ещё раз.",
        "error_delivery": "Не удалось доставить ответ. Попробуйте ещё раз.",
        "error_delivery_partial": "Не удалось доставить остальную часть ответа.",
        "analyze_start": "<b>Экзистенциальный анализ истории</b>\n\nРасскажите мне о ситуации, которая вас беспокоит. Опишите её подробно: что произошло, что вы чувствовали, о чём думали.\n\nЯ попробую найти в ней экзистенциальные темы и дать обратную связь.",
        "image_default_caption": "Проанализируй это изображение с экзистенциальной точки зрения.",
        "action_cancelled": "Действие отменено. Продолжаем диалог.",
        "listening": "Слушаю...",
        "transcribe_failed": "<b>Не удалось распознать голос:</b> {error}",
        "you_said": "<i>Вы сказали:</i> \"{text}\"",
        "story_too_short": "Пожалуйста, опишите ситуацию подробнее (хотя бы пару предложений).",
        "story_too_long": "Ваша история довольно подробная, но мне сложно охватить такой объём сразу. Пожалуйста, попробуйте выделить самое главное.",
        "voice_delivery_failed": "Не удалось создать или отправить голосовое сообщение. Попробуйте ещё раз.",
        "voice_length_failed": "Не удалось подготовить полный ответ подходящей длины для озвучивания. Попробуйте ещё раз.",
        "askprob_current": "Текущая мягкая настройка инициативности вопросов: {prob} (ориентир 0.0–1.0: ниже — сдержаннее, выше — больше открытости к вопросам; не жёсткая частота и не запрет вопросов о безопасности).",
        "askprob_set": "Установлена локальная настройка инициативности вопросов: {prob} (мягкий ориентир, а не строгая квота).",
        "askprob_reset": "Локальная настройка сброшена. Глобальная инициативность вопросов: {prob}",
        "askprob_invalid": "Неверный формат. Используйте /askprob 0.2 (мягкий ориентир инициативности вопросов от 0.0 до 1.0) или /askprob reset"
        ,
        "lang_current": "Текущий язык: {lang}",
        "lang_set": "Язык изменён.",
        "lang_invalid": "Неверный код языка. Доступно: ru, en. Используйте /lang en или /lang ru"
        ,
        "reply_register_minimal": "Длина этого ответа: одна короткая живая фраза, если контекст и безопасность позволяют. Не навязывай технику или упражнение.",
        "reply_register_short": "Длина этого ответа: 1–2 предложения. Не раздувай.",
        "reply_register_long": "Клиент рассказал развёрнуто — можно ответить несколькими абзацами, если это держит мысль. Не обрывай на полуслове и не раздувай водой.",
        "continue_truncated_reply": "Продолжи предыдущий ответ ровно с того места, где он оборвался. Не повторяй уже написанный текст. Допиши текущее предложение и заверши мысль.",

        "meta_prompt": "Опираясь на недавний контекст нашего разговора, дай короткое, естественное наблюдательное замечание о том, что сейчас происходит в беседе (1–3 предложения). Это может быть простое наблюдение, короткое различение, замеченное противоречие или ненавязчивая гипотеза. Не нужно придумывать метафору, афоризм или обязательно заканчивать глубоким вопросом. Если разговор был обычным или бытовым, откликнись просто и соразмерно, не преувеличивая его психологическое значение.",
        "meta_analyzing": "<b>Ищу слова...</b>",
        "silence_start": "<i>Тишина... (60 секунд)</i>",
        "silence_end": "Минута прошла. Можем остаться в тишине ещё немного или вернуться к беседе.",
        "silence_remaining": "<i>Тишина... Осталось {remaining} сек.</i>",
        "sticker_prompt": "Клиент прислал стикер (эмодзи: {emoji}).\n\nТвоя задача:\n1. Не пытайся угадать, что на картинке.\n2. Дай очень короткий, теплый и эмпатичный отклик (1 предложение).\n3. Просто покажи, что ты рядом и принимаешь эту эмоцию.\n\nПримеры:\n- \"Иногда слов действительно недостаточно.\"\n- \"Я здесь. Я слышу эту эмоцию.\"\n- \"Этот образ говорит о многом.\"\n\nСтиль: Ирвин Ялом. Тихий, принимающий, без пафоса.",
        "void_msg": "<i>Пустота — это зеркало, в котором мы видим своё отражение, но не узнаём его.</i>",
        "meaning_prompt": "Поделись одним живым и глубоким наблюдением. Никаких поучений и советов ('нужно', 'важно', 'не забывайте'), никаких клише про 'путь к цели' и 'радость момента'. Будь краток, точен и каждый раз меняй тему (от быта до мимолетных чувств). Ровно 1 предложение без вступлений.",
        "meaning_system": "Ты — проницательный наблюдатель. Твоя мысль о жизни необычна, но понятна; пиши как человек, который внезапно что-то понял, глядя на мир.",
        "meaning_enabled": "Ежедневные смыслы включены. Теперь раз в 24 часа я буду делиться с вами мимолетным смыслом момента.",        
        "meaning_disabled": "Ежедневные смыслы отключены. Я больше не буду тревожить вас ими, пока вы не попросите об этом снова через /meaning_is.",
        "error_llm": "Простите, в моих мыслях сейчас туман (ошибка связи). Давайте попробуем вернуться к этому чуть позже?",
        "error_flood": "Я был в глубоком раздумье и пропустил часть ваших слов. О чем из этого нам стоит поговорить сейчас?",
        "feedback_prompt": "Пожалуйста, используйте команду вместе с текстом сообщения.\n\nНапример: <b>/feedback Спасибо за бота!</b>",
        "feedback_thanks": "Спасибо. Ваше послание отправлено.",
        "feedback_admin_msg": "<b>Новый отзыв от {user}:</b>\n\n{text}",
        "update_notification_header": "<b>Обновление</b>\n\n",
        "update_notification_footer": "",

    },
    "en": {        
        "button_assoc": "Association analysis",
        "button_analyze": "Story analysis",
        "button_filmframe": "Snapshot",
        "button_help": "Help",
        "button_cancel": "Cancel",
        "consent_intro": "Hello, {name}. I'm Roi, an AI conversational companion with theurapeutic profile.",
        "consent_notice": "One small thing before we begin. We may talk about personal things, so I need your consent to proceed. Details: /privacy.\n\nIf you’re 18 or older and agree, we can begin.",
        "consent_yes": "Yes, let’s begin",
        "consent_no": "Not right now",
        "consent_accepted": "Great, now we can get acquainted.",
        "consent_declined": "I understand. We’ll stop here for now — I can’t continue the conversation without consent. If you change your mind, just use /start once again.",
        "consent_already": "We’re already ready to talk. What’s on your mind?",
        "consent_stale": "This button is out of date. Use /start for the current invitation.",
        "consent_save_failed": "I couldn’t save your choice. Let’s wait before starting—please try the button again a little later.",
        "consent_private": "Let’s continue in a private chat with Roi.",
        "privacy_revoke_prompt": "Revoke consent and delete the accessible conversation data and settings linked to you? This cannot be undone. Copies held by Telegram, external services, backups and technical logs may remain.",
        "privacy_revoke_yes": "Confirm",
        "privacy_revoke_no": "Go back",
        "privacy_revoke_cancelled": "Cancelled. Deletion was not started.",
        "privacy_revoke_stale": "This button is no longer valid. Use /privacy revoke again.",
        "privacy_revoke_busy": "Deletion is not yet complete. Please wait or retry /privacy revoke.",
        "privacy_revoke_failed": "I couldn't finish deleting all data. Conversation is paused; please retry /privacy revoke later.",
        "privacy_revoke_done": "Consent has been revoked and accessible application data linked to you deleted. A new conversation requires fresh consent. Copies held by Telegram, external services, backups and technical logs may remain.",
        "consent_details": (
            f"Consent to data processing for the Roi bot\nVersion: {CONSENT_VERSION}\n\n"
            "By choosing “Yes, let’s begin”, I confirm that I am at least 18 and consent to the bot creator processing my submitted messages and Telegram data to prepare replies and provide the features I use.\n\n"
            "The bot receives my Telegram identifier, profile details and messages. Voice messages, photographs and descriptions for image generation are processed if I use those features. Content is sent to external AI services for replies, analysis, transcription, speech synthesis or image generation.\n\n"
            "The current version stores settings, activity information, recent messages and automatically generated summaries in an encrypted file alongside Telegram identifiers. The administrator has access to operational records and stored bot data. Image-generation descriptions and prompts may enter a technical log. Telegram and AI providers may retain their own copies under their own policies. No promise of zero storage or full anonymity is made.\n\n"
            "Roi may occasionally send MP3 files automatically based on the conversation, without separate confirmation before each send. /music off disables automatic music, /music on enables it, and /music requests music manually. By default, automatic music is limited to one delivery per session and no more than once every seven days; server configuration may change the interval. A confirmed failure may be retried. Telegram processes delivered audio under its own terms.\n\n"
            "A separate unencrypted database stores an HMAC-pseudonymized user identifier, track IDs and delivery or reservation statuses, button and session state, the automatic-music preference and the last attempt time. Pseudonymization does not make this data anonymous. Music history prevents repeats and survives /reset. No automatic retention deadline is configured for saved settings, summaries or music history.\n\n"
            "My choice is voluntary. Declining keeps conversational features closed; /start lets me reconsider. /reset clears conversation context but is not complete deletion of all records or external copies. This consent does not separately authorize publishing conversations or building a research dataset.\n\n"
            "Do not send identity documents, information about other people or content you are unwilling to share with external services. Roi does not replace a professional and is not an emergency service.\n\n"
            "This is a short description of the bot’s current operation and an onboarding decision record, not a certification of 152-FZ compliance or written consent for special-category data.\n"
            "\nTo revoke consent and request deletion of accessible bot data linked to you, use /privacy revoke and confirm. Copies held by Telegram, external services, backups and technical logs may remain.\n"
        ),
        "welcome": "\n\nI'm Roi, an existential AI-interlocutor. I help explore questions of meaning, freedom, loneliness, and mortality; though not every conversation needs a deep analysis.\n\nYou can:\n• Write to me about what's bothering you\n• Use association analysis (/assoc) to identify existential themes\n• Use story analysis (/lysis) — for a deep dive into your situation\n\n• Inspect the other options using /help.\n\nSo, where shall we start?",
        "help": "<b>Existential interlocutor Roi</b>\n\nI mostly work in the tradition of Irvin Yalom and Viktor Frankl.\n\n<b>What I can do:</b>\n• Listen and ask questions\n• Help explore existential themes\n• Analyze your associations and detailed stories\n• Share some pieces of my music collection (via /music)\n• Create film photos of your inner state — just describe it\n• Analyze images — send a photo for an existential response\n\n<b>Options:</b>\n/assoc — association analysis\n/lysis — analyze story\n\n<b>For special moments:</b>\n/void — gaze into the void\n/silence — minute of silence\n/meaning — random meaning of the moment\n/remarque — observation on recent dialogue\n\n<b>A bit of media:</b>\n/shoot — your state snapshot\n/music — how the givens may sound\n\n<b>Service:</b>\n/reset — reset history\n/switchlang — switch language (ru, en)\n/meaning_where — where is meaning\n\n/privacy — consent and data handling\n/feedback — send feedback to developer\n\n<i>Important: I'm not a replacement for a professional therapist. If you're in crisis — please, seek professional help.</i>",

        "reset_confirm": "Conversation history cleared. Start again?",
        "assoc_start": "Let's explore your associations.\n\n<b>Pick associations for the word Freedom (optimally 5)</b>\n\n(separated with commas or spaces)",
        "assoc_nonsense_prompt": "<b>Pick associations for Meaninglessness</b>",
        "assoc_solitude_prompt": "<b>Pick associations for Solitude</b>",
        "assoc_death_prompt": "<b>And finally, pick associations for Death</b>",
        "assoc_confirm": "Associations for given {label}: {words}",
        "assoc_summary_intro": "<b>Your associations:</b>",
        "analyzing": "Analyzing...",
        "error_generation_incomplete": "The response could not be generated completely. Please try again.",
        "error_delivery": "The response could not be delivered. Please try again.",
        "error_delivery_partial": "The rest of the response could not be delivered.",
        "analyze_start": "<b>Existential story analysis</b>\n\nTell me a situation that troubles you. Describe it in detail: what happened, what you felt, what you thought.\n\nI'll try to find existential themes and give feedback.",
        "image_default_caption": "Analyze this image from an existential perspective.",
        "action_cancelled": "Action cancelled. Back to dialog.",
        "listening": "Listening...",
        "transcribe_failed": "<b>Failed to transcribe audio:</b> {error}",
        "you_said": "<i>You said:</i> \"{text}\"",
        "story_too_short": "Please describe the situation in more detail (at least a couple of sentences).",
        "story_too_long": "Your story is very detailed; please focus on the essential parts so I can respond effectively.",
        "voice_delivery_failed": "Could not generate or send the voice message. Please try again.",
        "voice_length_failed": "Could not prepare a complete response short enough for speech. Please try again.",
        "askprob_current": "Current soft question-initiative preference: {prob} (0.0–1.0: lower is more sparing, higher is more open to asking; not a fixed frequency or a ban on safety questions).",
        "askprob_set": "Per-user question-initiative preference set to: {prob} (soft guidance, not a strict quota).",
        "askprob_reset": "Per-user setting cleared. Global question initiative: {prob}",
        "askprob_invalid": "Invalid value. Use /askprob 0.2 (soft question-initiative preference from 0.0 to 1.0) or /askprob reset"
        ,
        "lang_current": "Current language: {lang}",
        "lang_set": "Language is all set.",
        "lang_invalid": "Invalid language code. Available: ru, en. Use /lang en or /lang ru"
        ,
        "reply_register_minimal": "Reply in one short, natural phrase if context and safety allow. Do not force a technique or exercise.",
        "reply_register_short": "Length for this reply: 1–2 sentences. Do not pad.",
        "reply_register_long": "The client wrote at length — a few paragraphs are fine if they carry the thought. Do not cut off mid-sentence, and do not pad.",
        "continue_truncated_reply": "Continue the previous reply from the exact point it stopped. Do not repeat any already written text. Finish the current sentence and the thought.",

        "meta_prompt": "Based on the recent context of our conversation, offer a brief, natural observational remark about what is happening in the dialogue right now (1–3 sentences). It can be a simple observation, a short distinction, a noticed contradiction, or a tentative hypothesis. Do not force a metaphor, an aphorism, or a mandatory deep question at the end. If the exchange was ordinary or casual, keep the remark plain and proportionate without overstating its psychological significance.",
        "meta_analyzing": "<b>Seeking for the words...</b>",
        "silence_start": "<i>Silence... (60 seconds)</i>",
        "silence_end": "In this silence, we were truly honest with each other. What did you feel in this void?",
        "silence_remaining": "<i>Silence... {remaining} sec. remaining.</i>",
        "sticker_prompt": "The client sent a sticker (emoji: {emoji}).\n\nYour task:\n1. Do not try to guess what is in the picture.\n2. Give a very short, warm and empathetic response (1 sentence).\n3. Simply show that you are there and accept this emotion.\n\nExamples:\n- \"Sometimes words really aren't enough.\"\n- \"I'm here. I hear this emotion.\"\n- \"This image says a lot.\"\n\nStyle: Irvin Yalom. Quiet, accepting, without grandiosity.",
        "void_msg": "<i>\"Emptiness is a mirror in which we see our reflection but do not recognize it.\"</i>",
        "meaning_prompt": "Share one vivid and deep observation. No preaching or advice ('should', 'important', 'don't forget'), and no clichés about 'the journey' or 'joy of the moment'. Be concise, precise, and change the theme every time (from everyday life to fleeting feelings). Exactly 1 sentence without introductions.",
        "meaning_system": "You are a perceptive observer. Your thought about life is unusual but understandable; write like a person who suddenly realized something while looking at the world.",
        "meaning_enabled": "Daily meanings enabled. Now, once every 24 hours, I will share a fleeting meaning of the moment with you.",        
        "meaning_disabled": "Daily meanings disabled. I will no longer disturb you with them until you ask again via /meaning_is.",
        "error_llm": "Forgive me, my thoughts are clouded right now (connection error). Shall we try to return to this a bit later?",
        "error_flood": "I was in deep contemplation and missed some of your words. Which of these should we talk about now?",
        "feedback_prompt": "Please use the command along with your message text.\nFor example: <b>/feedback Thank you for the bot!</b>",
        "feedback_thanks": "Thank you. Your message has been sent.",
        "feedback_admin_msg": "📩 <b>New feedback from {user}:</b>\n\n{text}",
        "update_notification_header": "<b>Update</b>\n\n",
        "update_notification_footer": ""

    }
}


def t(code: str, key: str, **kwargs) -> str:
    locale = LOCALES.get(code, LOCALES.get("en"))
    # Only log known dictionary keys/languages, never arbitrary input or values.
    safe_code = code if code in LOCALES else "<unknown>"
    safe_key = key if key in LOCALES["en"] or key in LOCALES["ru"] else "<unknown>"
    if code not in LOCALES:
        logger.warning("i18n unknown locale; using English")
    if key not in locale:
        logger.warning("i18n missing translation: locale=%s key=%s", safe_code, safe_key)
    template = locale.get(key, LOCALES["en"].get(key, key))
    try:
        return template.format(**kwargs)
    except Exception as exc:
        logger.warning("i18n formatting failed: locale=%s key=%s type=%s",
                       safe_code, safe_key, type(exc).__name__)
        return template
