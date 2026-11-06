import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from therapist_bot import ExistentialTherapistBot


def bot():
    obj = ExistentialTherapistBot(api_key="TEST", use_rag=False)
    obj.client = MagicMock()
    return obj


def test_models_from_environment_with_analysis_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "chat-model")
    monkeypatch.delenv("OPENAI_ANALYSIS_MODEL", raising=False)
    obj = bot()
    assert (obj.model, obj.analysis_model) == ("chat-model", "chat-model")
    monkeypatch.setenv("OPENAI_ANALYSIS_MODEL", "analysis-model")
    assert bot().analysis_model == "analysis-model"
    assert ExistentialTherapistBot(model="override", analysis_model="override-analysis", api_key="TEST", use_rag=False).analysis_model == "override-analysis"


def test_chat_and_analysis_requests_have_only_supported_baseline_parameters():
    obj = bot()
    obj.client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Hello"), finish_reason="stop")])
    obj.chat("ок")
    obj.generate_response("hello", temporary_system_instruction="test", use_analysis_model=True)
    calls = obj.client.chat.completions.create.call_args_list
    assert calls[0].kwargs["model"] == obj.model
    assert calls[0].kwargs["reasoning_effort"] == "medium"
    assert calls[1].kwargs["model"] == obj.analysis_model
    assert calls[1].kwargs["reasoning_effort"] == "high"
    for call in calls:
        assert set(call.kwargs) == {"model", "messages", "reasoning_effort"}


def test_real_prompts_are_consistent_with_identity_and_question_policy():
    for language in ("ru", "en"):
        obj = ExistentialTherapistBot(api_key="TEST", use_rag=False, language=language)
        assert "ask_flag" not in obj.system_prompt
        assert "Ялом" in obj.system_prompt if language == "ru" else "Yalom" in obj.system_prompt
        assert "Франкл" in obj.system_prompt if language == "ru" else "Frankl" in obj.system_prompt
