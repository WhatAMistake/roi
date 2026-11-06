import sys
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from therapist_bot import ExistentialTherapistBot


def fake_stream(text="A tentative reflection."):
    return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason="stop")])])


def make_bot(hypothesis=None):
    obj = ExistentialTherapistBot(api_key="TEST", use_rag=False, model="chat", analysis_model="analysis")
    obj.client = MagicMock()
    obj.client.chat.completions.create.side_effect = lambda **kw: fake_stream()
    obj.rag = MagicMock()
    obj.rag.crowd_service = object()
    obj.rag.analyze_user_associations.return_value = {"matched_patterns": []}
    obj.rag.search_similar_narratives.return_value = []
    obj.rag.search_similar_associations.return_value = [object()] if hypothesis else []
    obj.rag.aggregate_given_evidence.return_value = SimpleNamespace(to_dict=lambda: hypothesis)
    return obj


def test_associations_no_evidence_does_not_invent_dominant():
    obj = make_bot()
    associations = {"freedom": ["one"], "nonsense": ["two"], "solitude": ["three"], "death": ["four"]}
    assert obj.analyze_associations(associations) == "A tentative reflection."
    assert obj.last_dominant_given is None
    call = obj.client.chat.completions.create.call_args.kwargs
    assert call["model"] == "analysis" and call["reasoning_effort"] == "high"
    assert set(call) == {"model", "messages", "reasoning_effort", "stream"}
    prompt = call["messages"][-1]["content"]
    assert "не навязывай" in prompt.lower()
    assert "доминирующую данность" in prompt.lower()
    assert "Доминирующий конфликт с данностью:" not in prompt
    for word in ("one", "two", "three", "four"):
        assert word in prompt


def test_story_preserves_competing_crowd_hypotheses_as_uncertain():
    hypothesis = {"scores": {"freedom": 0.8, "death": 0.79},
                  "normalized_scores": {"freedom": 0.5, "death": 0.5},
                  "primary": None, "secondary": None, "confidence": "weak",
                  "evidence": [{"given": "death", "term": "end"}], "details": {}}
    obj = make_bot(hypothesis)
    obj.last_dominant_given = "freedom"
    assert obj.analyze_story("A complex story") == "A tentative reflection."
    assert obj.last_dominant_given is None
    prompt = obj.client.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
    assert '"confidence": "weak"' in prompt
    assert '"death": 0.79' in prompt
    assert "конкурирующие гипотезы" in prompt
    assert "корневой конфликт" in prompt
    obj.rag.aggregate_given_evidence.assert_called_once()


def test_analysis_does_not_restart_on_another_model_after_partial_stream():
    obj = make_bot()
    obj.client.chat.completions.create.side_effect = lambda **kw: iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Partial"), finish_reason=None)])])
    result = list(obj.analyze_associations_stream({}))
    assert result[0] == "Partial"
    assert result[-1].startswith("Ошибка:")
    assert obj.client.chat.completions.create.call_count == 1
