import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from therapist_bot import ExistentialTherapistBot
from i18n import t


def test_default_ask_question_prob():
    bot = ExistentialTherapistBot(api_key="TEST", use_rag=False)
    assert bot.ask_question_prob == 0.37


def test_presence_penalty_passed_to_completions():
    bot = ExistentialTherapistBot(api_key="TEST", use_rag=False)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Рефрейминг и наблюдение."),
                finish_reason="stop",
            )
        ]
    )
    bot.client = mock_client

    reply = bot.chat("Я чувствую, что застрял в выборе.")
    assert reply == "Рефрейминг и наблюдение."
    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs.get("presence_penalty") == 0.4


def test_no_ask_instruction_does_not_demand_reflection_and_mentions_reframing():
    instr_ru = t("ru", "response_instruction_no_ask")
    assert "Только отражение" not in instr_ru
    assert "рефрейминг" in instr_ru.lower()

    instr_en = t("en", "response_instruction_no_ask")
    assert "Only reflection" not in instr_en
    assert "reframing" in instr_en.lower()
