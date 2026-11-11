"""Offline tests for the durable, fragment-based update analysis."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from code_reviewer import (UpdateCache, collect_update_diff, split_update_diff,
                           _UPDATE_FRAGMENT_LIMIT, _validate_findings, _group_findings)


def put(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def response(payload):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))])


def mock_therapist(category="change", fail=None):
    client = MagicMock()
    def create(**kwargs):
        prompt = kwargs["messages"][1]["content"]
        if kwargs["messages"][0]["content"].startswith("Analyze"):
            if fail and fail(prompt):
                raise TimeoutError("private")
            line = next(line[1:] for line in prompt.splitlines()[3:]
                        if line.startswith(("+", "-")) and line[1:].strip())
            return response({"findings": [{
                "category": category, "component": "Messages",
                "description": "Сообщение изменено", "evidence": line.strip()[:12],
            }]})
        return response({"ru": "• Сообщение изменено", "en": "• Message updated"})
    client.chat.completions.create.side_effect = create
    return SimpleNamespace(client=client, model="mock")


def setup_change(root, old, new, rel="src/i18n.py"):
    path = put(root, rel, old)
    cache = UpdateCache(root)
    cache.prepare()
    put(root, rel, new)
    return cache, cache.prepare()


@pytest.mark.parametrize("size", [30000, 100000])
def test_large_diff_is_fully_covered_and_bounded(tmp_path, size):
    old = "".join(f"old message {i:05d} {'x' * 20}\n" for i in range(size // 40))
    new = "".join(f"new message {i:05d} {'y' * 20}\n" for i in range(size // 40))
    cache, draft = setup_change(tmp_path, old, new)
    data = cache._read()
    change = collect_update_diff(tmp_path, {"src/i18n.py": data["src/i18n.py"]},
                                 data["_update_state"]["snapshots"], draft)
    fragments = split_update_diff(change)
    complete = change["files"][0]["lines"][2:]
    covered = [line for fragment in fragments for line in fragment["diff"].splitlines()[3:]]
    # Boundary lines may be repeated as context, but every original line remains.
    remaining = iter(covered)
    assert all(any(candidate == line for candidate in remaining)
               for line in complete if not line.startswith("@@"))
    assert max(len(fragment["diff"]) for fragment in fragments) <= _UPDATE_FRAGMENT_LIMIT
    therapist = mock_therapist()
    result = cache.generate(therapist, draft)
    assert result["notes"]["en"] and not result["analysis"]["failures"]
    assert therapist.client.chat.completions.create.call_count == len(fragments) + 1


def test_one_long_line_is_not_truncated(tmp_path):
    cache, draft = setup_change(tmp_path, "old line\n", "new " + "x" * 60000)
    state = cache._read()
    change = collect_update_diff(tmp_path, {"src/i18n.py": state["src/i18n.py"]},
                                 state["_update_state"]["snapshots"], draft)
    fragments = split_update_diff(change)
    pieces = [line[1:] for fragment in fragments for line in fragment["diff"].splitlines()[3:]
              if line.startswith("+")]
    assert "".join(pieces) == "new " + "x" * 60000
    assert all(len(fragment["diff"]) <= _UPDATE_FRAGMENT_LIMIT for fragment in fragments)
    assert cache.generate(mock_therapist(), draft)["notes"]["ru"]


def test_multiple_files_new_deleted_commands_and_prompts(tmp_path):
    put(tmp_path, "src/telegram_bot.py", 'Command("old")\n')
    put(tmp_path, "prompts/dialog.md", "old prompt\n")
    put(tmp_path, "app/deleted.py", "deleted file\n")
    cache = UpdateCache(tmp_path)
    cache.prepare()
    put(tmp_path, "src/telegram_bot.py", 'Command("new")\n')
    put(tmp_path, "prompts/dialog.md", "new prompt\n")
    (tmp_path / "app/deleted.py").unlink()
    put(tmp_path, "app/new.py", "new file\n")
    draft = cache.prepare()
    model = mock_therapist()
    result = cache.generate(model, draft)
    assert result["analysis"]["file_count"] == 4
    assert result["analysis"]["added"] == 3
    assert result["analysis"]["removed"] == 3
    prompts = [c.kwargs["messages"][1]["content"] for c in
               model.client.chat.completions.create.call_args_list[:-1]]
    combined = "\n".join(prompts)
    assert '-Command("old")' in combined and '+Command("new")' in combined
    assert "-old prompt" in combined and "+new prompt" in combined
    assert "--- /dev/null" in combined and "+++ /dev/null" in combined
    assert "existing commands new" in model.client.chat.completions.create.call_args_list[0].kwargs["messages"][0]["content"]


def test_internal_and_uncertain_do_not_become_user_features(tmp_path):
    cache, draft = setup_change(tmp_path, "old helper\n", "new helper\n")
    model = mock_therapist("internal")
    result = cache.generate(model, draft)
    assert not any(result["notes"].values())
    assert result["analysis"]["findings"][0]["category"] == "internal"
    assert model.client.chat.completions.create.call_count == 1


def test_missing_snapshot_does_not_block_other_fragments(tmp_path):
    put(tmp_path, "src/i18n.py", "old message\n")
    put(tmp_path, "src/telegram_bot.py", "old text\n")
    cache = UpdateCache(tmp_path)
    cache.prepare()
    data = cache._read()
    del data["_update_state"]["snapshots"]["src/i18n.py"]
    cache._write(data)
    put(tmp_path, "src/i18n.py", "new message\n")
    put(tmp_path, "src/telegram_bot.py", "new text\n")
    model = mock_therapist()
    result = cache.generate(model, cache.prepare())
    assert result["analysis"]["missing"] == ["src/i18n.py"]
    assert len(result["analysis"]["findings"]) == 1
    assert not any(result["notes"].values())
    assert model.client.chat.completions.create.call_count == 1


def test_git_recovery_checks_saved_hash(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    put(tmp_path, "src/i18n.py", "git old text\n")
    subprocess.run(["git", "add", "src/i18n.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    cache = UpdateCache(tmp_path)
    cache.prepare()
    data = cache._read()
    data["_update_state"]["snapshots"] = {}
    cache._write(data)
    put(tmp_path, "src/i18n.py", "new text\n")
    result = cache.generate(mock_therapist(), cache.prepare())
    assert result["notes"]["ru"]
    assert not result["analysis"]["missing"]


def test_wrong_backup_hash_is_not_used(tmp_path):
    cache, draft = setup_change(tmp_path, "real old text\n", "new text\n")
    data = cache._read()
    del data["_update_state"]["snapshots"]["src/i18n.py"]
    cache._write(data)
    put(tmp_path, "data/code_backup/src_i18n.py", "wrong old text\n")
    model = mock_therapist()
    result = cache.generate(model, draft)
    assert result["analysis"]["missing"] == ["src/i18n.py"]
    assert not any(result["notes"].values())
    model.client.chat.completions.create.assert_not_called()


def test_failed_fragment_resumes_after_restart_without_repeating_success(tmp_path):
    cache, draft = setup_change(tmp_path, "old\n", "new line\n" * 2000)
    failing = mock_therapist(fail=lambda text: "new line" in text and "-old" not in text)
    first = cache.generate(failing, draft)
    assert first["analysis"]["failures"] and not any(first["notes"].values())
    saved = len(first["analysis"]["fragments"])
    assert saved > 0
    restored = UpdateCache(tmp_path)
    model = mock_therapist()
    result = restored.generate(model, restored.prepare())
    assert result["notes"]["ru"]
    assert model.client.chat.completions.create.call_count == result["analysis"]["fragment_count"] - saved + 1


def test_invalid_json_and_fabricated_evidence_are_retried(tmp_path):
    cache, draft = setup_change(tmp_path, "old text\n", "new text\n")
    model = mock_therapist()
    model.client.chat.completions.create.side_effect = [
        response({"findings": [{"category": "feature", "component": "x",
                                "description": "invented", "evidence": "not in diff"}]}),
        response({"findings": [{"category": "change", "component": "x",
                                "description": "Text changed", "evidence": "new text"}]}),
        response({"ru": "• Текст изменён", "en": "• Text changed"}),
    ]
    result = cache.generate(model, draft)
    assert result["notes"]["en"]
    assert model.client.chat.completions.create.call_count == 3


def test_empty_findings_are_saved_and_do_not_block_other_fragments(tmp_path):
    put(tmp_path, "src/i18n.py", "old text\n")
    put(tmp_path, "src/telegram_bot.py", "old message\n")
    cache = UpdateCache(tmp_path)
    cache.prepare()
    put(tmp_path, "src/i18n.py", " \n")
    put(tmp_path, "src/telegram_bot.py", "new message\n")
    model = mock_therapist()
    model.analysis_model = "analysis"
    model.client.chat.completions.create.side_effect = [
        response({"findings": []}),
        response({"findings": [{"category": "change", "component": "Messages",
                                "description": "Сообщение изменено", "evidence": "new message"}]}),
        response({"ru": "• Сообщение изменено", "en": "• Message updated"}),
    ]
    result = cache.generate(model, cache.prepare())
    assert result["notes"] == {"ru": "• Сообщение изменено", "en": "• Message updated"}
    assert not result["analysis"]["failures"]
    assert [] in result["analysis"]["fragments"].values()
    calls = model.client.chat.completions.create.call_args_list
    assert [call.kwargs["model"] for call in calls] == ["analysis", "analysis", "mock"]


@pytest.mark.parametrize("content,finish,diagnostic", [
    ('{"findings":', "stop", "Invalid JSON response"),
    ('{"findings":[]}', "length", "Incomplete model response"),
    ('{"findings":[{"category":[]}]}', "stop", "Invalid finding category"),
    ('{"findings":[{"category":"fix","component":"x","description":"y",'
     '"evidence":"fabricated"}]}', "stop", "Evidence not found in changed lines"),
])
def test_bad_model_response_is_bounded_and_diagnostic(tmp_path, content, finish, diagnostic):
    cache, draft = setup_change(tmp_path, "old text\n", "new text\n")
    model = mock_therapist()
    model.client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])
    model.client.chat.completions.create.side_effect = None
    result = cache.generate(model, draft)
    assert diagnostic in result["reason"]
    assert diagnostic in result["analysis"]["failures"][0]
    assert not any(result["notes"].values())
    assert model.client.chat.completions.create.call_count == 3


def test_errors_never_include_model_output_or_provider_exception(tmp_path, capsys):
    cache, draft = setup_change(tmp_path, "old text\n", "new text\n")
    model = mock_therapist()
    secret = "password=private-personal-data"
    model.client.chat.completions.create.side_effect = RuntimeError(secret)
    result = cache.generate(model, draft)
    assert "LLM request failed" in result["reason"]
    assert secret not in result["reason"] + str(result["analysis"]["failures"]) + capsys.readouterr().out


def test_malformed_json_diagnostic_does_not_include_model_text(tmp_path):
    cache, draft = setup_change(tmp_path, "old text\n", "new text\n")
    model = mock_therapist()
    secret = "password=private-personal-data"
    model.client.chat.completions.create.side_effect = None
    model.client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=secret))])
    result = cache.generate(model, draft)
    assert "Invalid JSON response" in result["reason"]
    assert secret not in result["reason"] + str(result["analysis"]["failures"])


def test_empty_findings_are_reused_after_another_fragment_fails(tmp_path):
    put(tmp_path, "src/i18n.py", "old text\n")
    put(tmp_path, "src/telegram_bot.py", "old message\n")
    cache = UpdateCache(tmp_path)
    cache.prepare()
    put(tmp_path, "src/i18n.py", " \n")
    put(tmp_path, "src/telegram_bot.py", "new message\n")
    model = mock_therapist()
    model.client.chat.completions.create.side_effect = [
        response({"findings": []}), *[TimeoutError() for _ in range(3)],
    ]
    first = cache.generate(model, cache.prepare())
    assert first["analysis"]["failures"] and not any(first["notes"].values())
    assert [] in first["analysis"]["fragments"].values()
    retry = mock_therapist()
    completed = cache.generate(retry, cache.prepare())
    assert completed["notes"]["ru"] and completed["notes"]["en"]
    assert retry.client.chat.completions.create.call_count == 2


def test_release_note_failure_is_diagnostic_and_does_not_invent_notes(tmp_path):
    cache, draft = setup_change(tmp_path, "old text\n", "new text\n")
    model = mock_therapist()
    model.client.chat.completions.create.side_effect = [
        response({"findings": [{"category": "change", "component": "Text",
                                "description": "Text changed", "evidence": "new text"}]}),
        *[response({"ru": "• Текст изменён"}) for _ in range(3)],
    ]
    result = cache.generate(model, draft)
    assert "Missing or invalid release note language" in result["reason"]
    assert not any(result["notes"].values())
    assert model.client.chat.completions.create.call_count == 4


def test_context_only_fragment_accepts_empty_findings_but_rejects_evidence():
    fragment = {"path": "src/i18n.py", "diff": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n context"}
    assert _validate_findings({"findings": []}, fragment) == []
    with pytest.raises(ValueError, match="Evidence not found"):
        _validate_findings({"findings": [{"category": "change", "component": "x",
                                         "description": "y", "evidence": "context"}]}, fragment)


def test_evidence_accepts_diff_marker_but_not_context_or_wrong_marker():
    fragment = {"path": "src/i18n.py", "diff": (
        "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old text\n+new text\n context")}
    finding = {"category": "change", "component": "x", "description": "y",
               "evidence": "+new text"}
    assert _validate_findings({"findings": [finding]}, fragment)[0]["evidence"] == "+new text"
    for evidence in ("-new text", "+context", "+no"):
        with pytest.raises(ValueError, match="Evidence not found"):
            _validate_findings({"findings": [{**finding, "evidence": evidence}]}, fragment)


def test_long_line_short_final_segment_is_not_repeated():
    # The last segment is short but must not be copied as boundary context.
    change = {"files": [{"path": "x", "lines": [
        "--- a/x", "+++ b/x", "@@ -1 +1,3 @@", "+" + "x" * 650,
        "+" + "y" * 300, "+" + "z" * 300,
    ]}]}
    fragments = split_update_diff(change, limit=800)
    additions = [line[1:] for fragment in fragments for line in fragment["diff"].splitlines()[3:]
                 if line.startswith("+")]
    assert "".join(additions) == "x" * 650 + "y" * 300 + "z" * 300


def finding(component, description, evidence, *, path="src/telegram_bot.py", category="fix"):
    return {"component": component, "description": description, "evidence": evidence,
            "path": path, "category": category}


def test_groups_equivalent_findings_and_normalizes_components():
    original = [
        finding("telegram_bot", "Исправлена обработка ошибок Telegram 429", "line one"),
        finding("telegram_bot.py", "Обработка ошибки Telegram 429 исправлена", "line two"),
        finding("telegram_bot.py — обработка ошибок рассылки",
                "Исправлена обработка ошибок Telegram 429", "line three"),
        finding("telegram_bot.py", "Исправлена отправка длинных сообщений", "line four"),
    ]
    grouped = _group_findings(original)
    assert len(grouped) == 2
    assert {item["component"] for item in grouped} == {"src/telegram_bot.py"}
    assert grouped[0]["path"] == "src/telegram_bot.py"
    assert grouped[0]["sources"] == original[:3]
    assert grouped[1]["description"] == original[3]["description"]
    assert original[0]["component"] == "telegram_bot"  # No mutation of saved fragments.


def test_grouping_is_conservative_about_files_categories_and_distinct_effects():
    original = [
        finding("bot", "Исправлена обработка ошибок Telegram 429", "one"),
        finding("bot", "Исправлена обработка ошибок Telegram 500", "two"),
        finding("bot", "Исправлена обработка ошибок Telegram 429", "three",
                path="app/telegram_bot.py"),
        finding("bot", "Исправлена обработка ошибок Telegram 429", "four", category="uncertain"),
        finding("bot", "Исправлена обработка ошибок Telegram 429 без повтора", "five"),
        finding("bot", "Исправлено сообщение", "six"),
        finding("bot", "Исправлено сообщение об ошибке", "seven"),
    ]
    grouped = _group_findings(original)
    assert len(grouped) == len(original)
    assert grouped[2]["component"] == "app/telegram_bot.py"


def test_identical_generic_descriptions_keep_all_distinct_evidence():
    first = finding("telegram_bot", "Исправлено сообщение", "message one")
    second = finding("telegram_bot.py", "Исправлено сообщение", "message two")
    grouped = _group_findings([first, second])
    assert len(grouped) == 1
    assert grouped[0]["sources"] == [first, second]


def test_identical_wording_with_distinct_functional_context_stays_separate():
    grouped = _group_findings([
        finding("telegram_bot.py — отправка", "Исправлена обработка ошибок Telegram 429", "one"),
        finding("telegram_bot.py — приём", "Исправлена обработка ошибок Telegram 429", "two"),
    ])
    assert len(grouped) == 2
    assert {item["component"] for item in grouped} == {"src/telegram_bot.py"}
    assert grouped[0]["description"].endswith("(отправка)")
    assert grouped[1]["description"].endswith("(приём)")


def test_small_code_edits_stay_in_analysis_without_inventing_effect():
    grouped = _group_findings([
        finding("bot", "Переименована переменная retry_delay", "one", category="change"),
        finding("bot", "Изменено условие оператора if", "two", category="change"),
        finding("bot", "Изменено условие повтора отправки сообщений", "three", category="fix"),
        finding("bot", "Переименована переменная retry_delay для отображения имени",
                "four", category="change"),
    ])
    assert [item["category"] for item in grouped] == ["internal", "uncertain", "fix", "change"]
    assert grouped[0]["sources"][0]["category"] == "change"


def test_generation_synthesizes_grouped_findings_and_keeps_sources(tmp_path):
    cache, draft = setup_change(tmp_path, "old line\n", "new line one\nnew line two\n",
                                rel="src/telegram_bot.py")
    model = mock_therapist()
    model.client.chat.completions.create.side_effect = [
        response({"findings": [
            {"category": "fix", "component": "telegram_bot",
             "description": "Исправлена обработка ошибок Telegram 429", "evidence": "new line one"},
            {"category": "fix", "component": "telegram_bot.py — обработка рассылки",
             "description": "Обработка ошибки Telegram 429 исправлена", "evidence": "new line two"},
            {"category": "change", "component": "telegram_bot.py",
             "description": "Переименована переменная retry_delay", "evidence": "new line two"},
        ]}),
        response({"ru": "• Исправлена обработка ошибок", "en": "• Error handling fixed"}),
    ]
    result = cache.generate(model, draft)
    findings = result["analysis"]["findings"]
    assert len(findings) == 2 and len(findings[0]["sources"]) == 2
    assert findings[0]["component"] == "src/telegram_bot.py"
    assert findings[1]["category"] == "internal"
    assert len(next(iter(result["analysis"]["fragments"].values()))) == 3
    payload = json.loads(
        model.client.chat.completions.create.call_args_list[-1].kwargs["messages"][1]["content"])
    assert len(payload) == 1
    assert payload[0]["component_context"] == ["обработка рассылки"]
    assert "evidence" not in json.dumps(payload)
    assert result["notes"]["ru"] and result["notes"]["en"]


def test_existing_command_cannot_be_announced_as_new(tmp_path):
    cache, draft = setup_change(
        tmp_path, 'Command("already_here")\nold response\n',
        'Command("already_here")\nnew response\n', "src/telegram_bot.py")
    model = mock_therapist()
    model.client.chat.completions.create.side_effect = [
        response({"findings": [{"category": "feature", "component": "Commands",
                                "description": "Command changed", "evidence": "new response"}]}),
        *[response({"ru": "• Новая команда /already_here",
                    "en": "• New command /already_here"}) for _ in range(3)],
    ]
    result = cache.generate(model, draft)
    assert not any(result["notes"].values())
    assert "Не удалось" in result["reason"]


def test_rename_is_compared_as_deleted_and_added(tmp_path):
    old = put(tmp_path, "app/old.py", "some functionality\n")
    cache = UpdateCache(tmp_path)
    cache.prepare()
    old.unlink()
    put(tmp_path, "app/new.py", "some functionality\n")
    draft = cache.prepare()
    result = cache.generate(mock_therapist("internal"), draft)
    assert result["paths"] == ["app/new.py", "app/old.py"]
    assert result["analysis"]["added"] == result["analysis"]["removed"] == 1


def test_confirmed_or_manually_edited_draft_is_never_regenerated(tmp_path):
    cache, draft = setup_change(tmp_path, "old\n", "new\n")
    draft["status"] = "review"
    draft["notes"]["ru"] = "Ручная правка"
    cache.update(draft)
    model = mock_therapist()
    assert cache.generate(model, UpdateCache(tmp_path).prepare())["notes"]["ru"] == "Ручная правка"
    model.client.chat.completions.create.assert_not_called()


def test_dry_run_does_not_touch_production_cache(tmp_path, monkeypatch, capsys):
    from scripts import test_changelog
    cache, draft = setup_change(tmp_path, "old text\n", "new text\n")
    before = cache.path.read_bytes()
    monkeypatch.setattr(test_changelog, "project_root", tmp_path)
    assert test_changelog.main([]) == 0
    assert cache.path.read_bytes() == before
    assert "fragments:" in capsys.readouterr().out
