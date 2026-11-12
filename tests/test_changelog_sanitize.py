"""Tests for user-facing changelog sanitization.

Run: py -m pytest tests/test_changelog_sanitize.py -v
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))

from code_reviewer import (  # noqa: E402
    _extract_completion_text,
    _sanitize_changelog,
    generate_changelog_with_llm,
    UpdateCache,
)


def test_legacy_changelog_loader_preserves_therapist_module():
    import therapist_bot
    from scripts.test_changelog import load_therapist_bot

    assert load_therapist_bot() is therapist_bot.ExistentialTherapistBot
    assert sys.modules["therapist_bot"] is therapist_bot


def test_strips_think_tags_and_keeps_short_bullets():
    raw = (
        "<think>\n"
        "The diff in src/telegram_bot.py adds a helper and changes hashes.\n"
        "I should mention the internal refactor of _format_update_preview.\n"
        "</think>\n"
        "- Теперь можно править текст обновления перед рассылкой\n"
        "- Исправлен сбой при отправке длинного ответа\n"
    )
    cleaned = _sanitize_changelog(raw)
    assert "think" not in cleaned.lower()
    assert "telegram_bot.py" not in cleaned
    assert "_format_update_preview" not in cleaned
    assert "править текст обновления" in cleaned
    assert "длинного ответа" in cleaned
    assert cleaned.count("•") == 2


def test_uses_final_answer_after_reasoning_dump():
    raw = (
        "Looking at the diff I notice src/code_reviewer.py now tracks snapshots.\n"
        "This is a large internal change with hashes and backups.\n\n"
        "Changelog:\n"
        "- Daily meanings arrive more reliably\n"
    )
    cleaned = _sanitize_changelog(raw)
    assert cleaned == "• Daily meanings arrive more reliably"
    assert "code_reviewer.py" not in cleaned
    assert "hashes" not in cleaned


def test_drops_technical_or_empty_output():
    assert _sanitize_changelog("NONE") == ""
    assert _sanitize_changelog("НЕТ") == ""
    assert _sanitize_changelog("Internal improvements to hashing") == ""
    assert _sanitize_changelog("Updated src/telegram_bot.py helper `_clip()`") == ""
    assert _sanitize_changelog("• Key: sk-1234567890abcdefghijklmnop") == ""
    assert _sanitize_changelog("• password=topsecret") == ""


def test_drops_unmarked_reasoning_dump():
    raw = (
        "Looking at the files I see many internal helpers changed.\n"
        "The hash snapshot logic is different now.\n"
        "I should not invent user-facing features from this."
    )
    assert _sanitize_changelog(raw) == ""


def test_long_bullets_are_preserved_without_truncation():
    bullets = ["• " + ("Длинное описание изменения " * 10) + str(i) for i in range(4)]
    assert _sanitize_changelog("\n".join(bullets)) == "\n".join(bullets)


def test_oversized_release_note_is_rejected_not_truncated():
    import pytest
    from code_reviewer import _validate_notes
    with pytest.raises(ValueError, match="Release note too long"):
        _validate_notes({"ru": "• " + "т" * 3500, "en": "• Valid English text"})


def test_extract_completion_ignores_reasoning_fields():
    message = SimpleNamespace(
        content="• Fixed the help text",
        reasoning="long hidden chain of thought about diffs",
        reasoning_content="more hidden thoughts",
    )
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    assert _extract_completion_text(response) == "• Fixed the help text"

    multipart = SimpleNamespace(
        content=[
            {"type": "thinking", "text": "I will inspect the unified diff now."},
            {"type": "text", "text": "- Snapshot button is easier to find"},
        ]
    )
    response = SimpleNamespace(choices=[SimpleNamespace(message=multipart)])
    assert _extract_completion_text(response) == "- Snapshot button is easier to find"


def test_generate_changelog_sanitizes_model_output(tmp_path):
    source = tmp_path / "src" / "i18n.py"
    source.parent.mkdir()
    source.write_text("old help text\n", encoding="utf-8")
    cache = UpdateCache(tmp_path)
    cache.prepare()
    old = cache._read()["src/i18n.py"]
    source.write_text("new help text\n", encoding="utf-8")
    from code_reviewer import calculate_file_hash
    client = MagicMock()
    def response(content):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    client.chat.completions.create.side_effect = [
        response('{"findings":[{"category":"change","component":"Help",'
                 '"description":"Help text changed","evidence":"new help text"}]}'),
        response('{"ru":"• Текст помощи изменён","en":"• Help text is clearer"}'),
    ]
    therapist = SimpleNamespace(client=client, model="test-model")
    result = generate_changelog_with_llm(
        therapist,
        [("src/i18n.py", old, calculate_file_hash(source))],
        tmp_path,
        lang="en",
    )

    assert result
    assert "i18n.py" not in result
    assert "<think>" not in result
    assert "Help text is clearer" in result
    assert result.count("•") == 1


def test_durable_hash_draft_survives_restart_and_preserves_later_changes(tmp_path):
    source = tmp_path / "src" / "telegram_bot.py"
    source.parent.mkdir()
    source.write_text("old\n", encoding="utf-8")
    cache = UpdateCache(tmp_path)
    assert cache.prepare() is None
    source.write_text("first\n", encoding="utf-8")
    draft = cache.prepare()
    assert draft["paths"] == ["src/telegram_bot.py"]
    draft["notes"] = {"ru": "• Первое", "en": "• First"}
    draft["status"] = "review"
    cache.update(draft)
    source.write_text("second\n", encoding="utf-8")
    restored = UpdateCache(tmp_path).prepare()
    assert restored["notes"] == draft["notes"]
    cache.resolve(restored)
    assert cache.prepare()["paths"] == ["src/telegram_bot.py"]


def test_deleted_and_new_feature_files_are_detected(tmp_path):
    source = tmp_path / "src" / "telegram_bot.py"
    source.parent.mkdir()
    source.write_text("old\n", encoding="utf-8")
    cache = UpdateCache(tmp_path)
    assert cache.prepare() is None
    source.unlink()
    feature = tmp_path / "app" / "features" / "filmframe" / "handlers.py"
    feature.parent.mkdir(parents=True)
    feature.write_text("new\n", encoding="utf-8")
    assert cache.prepare()["paths"] == [
        "app/features/filmframe/handlers.py", "src/telegram_bot.py"
    ]


def test_generation_failure_is_not_a_completed_update(tmp_path):
    source = tmp_path / "src" / "telegram_bot.py"
    source.parent.mkdir()
    source.write_text("old\n", encoding="utf-8")
    cache = UpdateCache(tmp_path)
    assert cache.prepare() is None
    source.write_text("new\n", encoding="utf-8")
    draft = cache.prepare()
    client = MagicMock()
    client.chat.completions.create.side_effect = TimeoutError()
    result = cache.generate(SimpleNamespace(client=client, model="test"), draft)
    assert result["status"] == "review"
    assert "TimeoutError" in result["reason"]
    assert cache.prepare()["id"] == draft["id"]


def test_generation_uses_structured_diff_before_both_languages(tmp_path):
    source = tmp_path / "src" / "telegram_bot.py"
    source.parent.mkdir()
    source.write_text("old\n", encoding="utf-8")
    cache = UpdateCache(tmp_path)
    cache.prepare()
    source.write_text("new\n", encoding="utf-8")
    draft = cache.prepare()
    client = MagicMock()
    def response(content):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))])
    client.chat.completions.create.side_effect = [
        response('{"findings":[{"category":"change","component":"Text",'
                 '"description":"Text changed","evidence":"new"}]}'),
        response('{"ru":"• Новый текст","en":"• New text"}'),
    ]
    result = cache.generate(SimpleNamespace(client=client, model="test"), draft)
    assert result["notes"] == {"ru": "• Новый текст", "en": "• New text"}
    assert client.chat.completions.create.call_count == 2
    prompt = client.chat.completions.create.call_args_list[0].kwargs["messages"][1]["content"]
    assert "-old" in prompt and "+new" in prompt


def test_missing_old_snapshot_requires_manual_review(tmp_path):
    source = tmp_path / "src" / "telegram_bot.py"
    source.parent.mkdir()
    source.write_text("new\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    (data / "code_hashes.json").write_text(
        '{"src/telegram_bot.py":"old-hash"}', encoding="utf-8"
    )
    cache = UpdateCache(tmp_path)
    draft = cache.prepare()
    client = MagicMock()
    result = cache.generate(SimpleNamespace(client=client, model="test"), draft)
    assert "снимка" in result["reason"]
    client.chat.completions.create.assert_not_called()


def test_oversized_diff_is_split_not_rejected(tmp_path):
    source = tmp_path / "src" / "telegram_bot.py"
    source.parent.mkdir()
    source.write_text("old\n", encoding="utf-8")
    cache = UpdateCache(tmp_path)
    cache.prepare()
    source.write_text("new " * 4000, encoding="utf-8")
    draft = cache.prepare()
    client = MagicMock()
    def response(**kwargs):
        prompt = kwargs["messages"][1]["content"]
        line = next(line[1:] for line in prompt.splitlines()[3:] if line.startswith("+"))
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content=__import__("json").dumps({"findings": [{
                "category": "internal", "component": "Text", "description": "Internal change",
                "evidence": line[:8].strip(),
            }]}),
        ))])
    client.chat.completions.create.side_effect = response
    result = cache.generate(SimpleNamespace(client=client, model="test"), draft)
    assert "лимит" not in result["reason"]
    assert result["analysis"]["fragment_count"] > 1
    assert client.chat.completions.create.call_count == result["analysis"]["fragment_count"]


def test_incomplete_model_output_is_not_accepted(tmp_path):
    source = tmp_path / "src" / "telegram_bot.py"
    source.parent.mkdir()
    source.write_text("old\n", encoding="utf-8")
    cache = UpdateCache(tmp_path)
    cache.prepare()
    source.write_text("new\n", encoding="utf-8")
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(
            content='{"ru":"• Новый текст","en":"• New text"}'
        ))]
    )
    draft = cache.generate(SimpleNamespace(client=client, model="test"), cache.prepare())
    assert "ValueError" in draft["reason"]
    assert not any(draft["notes"].values())


def test_retry_does_not_overwrite_concurrent_admin_edit(tmp_path):
    source = tmp_path / "src" / "telegram_bot.py"
    source.parent.mkdir()
    source.write_text("old\n", encoding="utf-8")
    cache = UpdateCache(tmp_path)
    cache.prepare()
    source.write_text("new\n", encoding="utf-8")
    draft = cache.prepare()
    client = MagicMock()

    def respond(**kwargs):
        edited = cache.pending()
        edited["notes"]["ru"] = "Edited"
        cache.update(edited)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(
                content='{"ru":"• Новый текст","en":"• New text"}'
            )
        )])

    client.chat.completions.create.side_effect = respond
    result = cache.generate(SimpleNamespace(client=client, model="test"), draft)
    assert result["notes"]["ru"] == "Edited"
    assert cache.pending()["notes"]["ru"] == "Edited"
