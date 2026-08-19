import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from therapist_bot import ExistentialTherapistBot


def _bot() -> ExistentialTherapistBot:
    return ExistentialTherapistBot(api_key="TEST", use_rag=False, language="ru")


def test_acks_are_minimal_and_skip_technique():
    bot = _bot()
    for text in ("ок", "угу", "не знаю", "ok", "idk"):
        assert bot.classify_reply_register(text) == "minimal"
        assert bot.select_technique(text) is None


def test_short_weight_is_not_shrunk():
    bot = _bot()
    assert bot.classify_reply_register("умер папа") == "normal"
    assert bot.select_technique("умер папа") == "epitaph"
    assert bot.classify_reply_register("мне плохо") == "normal"
    assert bot.select_technique("мне плохо") == "labeling"


def test_generic_fear_is_not_grounding():
    bot = _bot()
    for text in ("мне страшно", "мне тревожно уже несколько дней"):
        assert bot.classify_reply_register(text) != "minimal"
        for _ in range(20):
            assert bot.select_technique(text) in {"socratic", "labeling"}


def test_panic_can_use_grounding():
    bot = _bot()
    text = "паника, не могу дышать, меня трясет"
    assert bot.classify_reply_register(text) == "normal"
    picks = {bot.select_technique(text) for _ in range(30)}
    assert picks <= {"grounding", "mindfulness", "somatic"}
    assert "grounding" in picks


def test_short_noncrisis_skips_technique():
    bot = _bot()
    text = "сегодня просто устал немного"
    assert bot.classify_reply_register(text) == "short"
    assert bot.select_technique(text) is None


def test_long_story_uses_socratic_or_narrative():
    bot = _bot()
    text = (
        "Сегодня весь день ходил по квартире и думал о работе. "
        "Кажется, я застрял в одном и том же круге уже много месяцев. "
        "Хочется что-то изменить, но каждый раз останавливаюсь на полушаге и возвращаюсь назад."
    )
    assert bot.classify_reply_register(text) == "long"
    picks = {bot.select_technique(text) for _ in range(20)}
    assert picks <= {"socratic", "narrative"}


def test_avoids_repeating_last_technique():
    bot = _bot()
    bot.last_techniques.append("socratic")
    text = (
        "Сегодня весь день ходил по квартире и думал о работе. "
        "Кажется, я застрял в одном и том же круге уже много месяцев. "
        "Хочется что-то изменить, но каждый раз останавливаюсь на полушаге и возвращаюсь назад."
    )
    for _ in range(10):
        assert bot.select_technique(text) == "narrative"


def test_minimal_build_skips_technique_rag_and_ask():
    bot = _bot()
    bot.ask_question_prob = 1.0
    bot.rag = object()
    bot.use_rag = True
    messages = bot._build_messages("ок")
    joined = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert "reply_register_minimal" not in joined
    assert "одна короткая живая фраза" in joined
    assert "ВОЗМОЖНАЯ ТЕХНИКА" not in joined
    assert "Контекст из базы знаний" not in joined
    assert "НЕЛЬЗЯ задавать вопрос" in joined
    assert "Задайте ОДИН" not in joined


def test_normal_build_can_include_technique():
    bot = _bot()
    messages = bot._build_messages("умер папа")
    joined = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert "ВОЗМОЖНАЯ ТЕХНИКА" in joined
    assert "эпитафия" in joined.lower() or "epitaph" in joined.lower()
    assert list(bot.last_techniques)[-1] == "epitaph"
