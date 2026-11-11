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
                           _UPDATE_FRAGMENT_LIMIT)


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
