import sys
from pathlib import Path
from unittest.mock import MagicMock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from therapist_bot import ExistentialTherapistBot


def bot():
    obj = ExistentialTherapistBot(api_key="TEST", use_rag=False)
    obj.client = MagicMock()
    return obj


def test_register_distinguishes_small_talk_and_distress():
    obj = bot()
    assert obj.classify_reply_register("ок") == "minimal"
    assert obj.classify_reply_register("умер папа") == "normal"
    assert obj.classify_reply_register("паника, не могу дышать") == "normal"


def test_keywords_and_previous_hypothesis_never_prescribe_techniques():
    obj = bot()
    obj.last_dominant_given = "death"
    for text in ("суицид", "хочу умереть", "умер папа", "я одинок", "у меня паника"):
        system = "\n".join(m["content"] for m in obj._build_messages(text) if m["role"] == "system")
        assert "ВОЗМОЖНАЯ ТЕХНИКА" not in system
        assert "Эпитафия" not in system


def test_question_preference_is_soft_even_at_extremes_and_in_crisis():
    obj = bot()
    instructions = []
    for prob in (0.0, 1.0):
        obj.ask_question_prob = prob
        messages = obj._build_messages("Я думаю о самоубийстве")
        policy = messages[-2]["content"]
        instructions.append(policy)
        assert "независимо от этой настройки" in policy
        assert "не заканчивай им ответ автоматически" in policy
        assert "НЕЛЬЗЯ" not in policy
    assert instructions[0] != instructions[1]


def test_natural_reply_without_question_is_saved():
    obj = bot()
    obj.ask_question_prob = 1
    obj.client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Я рядом"), finish_reason="stop")])
    assert obj.chat("ок") == "Я рядом"
    assert [m.content for m in obj.history] == ["ок", "Я рядом"]


def test_reset_clears_hypotheses_and_history():
    obj = bot()
    obj.history.append(SimpleNamespace(role="user", content="private"))
    obj.last_dominant_given = "death"
    obj.last_secondary_given = "freedom"
    obj.reset()
    assert obj.history == []
    assert obj.last_dominant_given is None
    assert obj.last_secondary_given is None
