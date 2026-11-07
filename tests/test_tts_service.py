import base64
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import tts_service as tts


@pytest.fixture
def configured(monkeypatch, tmp_path):
    ref = tmp_path / "reference.mp3"
    ref.write_bytes(b"reference")
    values = {
        "TTS_PROVIDER": "clone",
        "TTS_CLONE_MODEL": "seed_audio",
        "TTS_CLONE_VOICE_ID": "",
        "TTS_REFERENCE_PATH": str(ref),
        "TTS_CLONE_VERIFIED": "1",
        "TTS_CLONE_TIMEOUT": "12",
        "OPENAI_API_KEY": "test-key",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TTS_MODEL", raising=False)
    monkeypatch.delenv("TTS_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("TTS_FALLBACK_VOICE", raising=False)
    monkeypatch.setattr(tts.shutil, "which", lambda _: "tool")
    monkeypatch.setattr(tts, "_public_https", lambda _: True)
    monkeypatch.setattr(tts, "reference_data_uri", lambda path, timeout:
                        tts.REFERENCE_URI_PREFIX + base64.b64encode(Path(path).read_bytes()).decode())
    return tmp_path


class Response:
    def __init__(self, status=200, body=None, content=b"audio"):
        self.status_code = status
        self.body = body or {}
        self.content = content

    def json(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_bytes(self):
        yield self.content


def fake_session(monkeypatch, post=None, task=None, content=b"audio"):
    session = MagicMock()
    session.__enter__.return_value = session
    session.post.return_value = post or Response(body={"id": "task-1"})
    session.get.return_value = task or Response(body={"status": "SUCCEEDED", "output": ["https://example.com/output.mp3"]})
    session.stream.return_value = Response(content=content)
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(tts.httpx, "Client", factory)
    return session, factory


def test_reference_speech_success_sends_one_generation(configured, monkeypatch):
    session, _ = fake_session(monkeypatch)
    monkeypatch.setattr(tts, "validate_audio", lambda *args, **kwargs: 1.25)
    client = MagicMock()
    output = configured / "result.mp3"
    tts.generate_speech(client, "Hello", str(output))
    assert output.read_bytes() == b"audio"
    assert session.post.call_count == 1
    payload = session.post.call_args.kwargs["json"]
    assert payload["model"] == "seed_audio"
    assert payload["voice"] == {"type": "reference-audio", "audioUri":
                                tts.REFERENCE_URI_PREFIX + base64.b64encode(b"reference").decode()}
    assert "voice_id" not in payload
    client.audio.speech.create.assert_not_called()


@pytest.mark.parametrize("failure", ["provider", "timeout", "invalid_audio"])
def test_clone_failure_falls_back(configured, monkeypatch, failure, caplog):
    session, _ = fake_session(monkeypatch)
    if failure == "provider":
        session.post.return_value = Response(429, {"error": {"code": "rate_limit"}})
    elif failure == "timeout":
        session.post.side_effect = httpx.ReadTimeout("secret should not be logged")
    else:
        def invalid(*args, **kwargs):
            raise tts.CloneError("invalid_audio")
        monkeypatch.setattr(tts, "validate_audio", invalid)
    client = MagicMock()
    tts.generate_speech(client, "Hello", str(configured / "response.mp3"))
    client.audio.speech.create.assert_called_once_with(model="tts-1", voice="fable", input="Hello")
    client.audio.speech.create.return_value.stream_to_file.assert_called_once()
    assert session.post.call_count == 1
    assert "fallback" in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.parametrize("provider,verified", [("fable", "1"), ("clone", "0")])
def test_disabled_or_unverified_never_contacts_clone(configured, monkeypatch, provider, verified):
    monkeypatch.setenv("TTS_PROVIDER", provider)
    monkeypatch.setenv("TTS_CLONE_VERIFIED", verified)
    _, factory = fake_session(monkeypatch)
    client = MagicMock()
    tts.generate_speech(client, "Hello", str(configured / "response.mp3"))
    factory.assert_not_called()
    client.audio.speech.create.assert_called_once()


def test_old_tts_model_has_precedence(configured, monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "fable")
    monkeypatch.setenv("TTS_MODEL", "legacy-model")
    monkeypatch.setenv("TTS_FALLBACK_MODEL", "different-model")
    client = MagicMock()
    tts.generate_speech(client, "Hello", str(configured / "response.mp3"))
    assert client.audio.speech.create.call_args.kwargs["model"] == "legacy-model"


@pytest.mark.parametrize("status,category", [(400, "parameters"), (401, "authentication"),
                                              (404, "unsupported"), (429, "rate_limit"),
                                              (503, "unavailable")])
def test_http_error_classification(status, category):
    with pytest.raises(tts.CloneError) as error:
        tts.check_response(Response(status, {"code": "model_not_found"}))
    assert error.value.category == category
    assert error.value.status == status
    assert error.value.code == "model_not_found"


def test_unknown_error_code_is_redacted():
    with pytest.raises(tts.CloneError) as error:
        tts.check_response(Response(400, {"code": "a-possible-private-value"}))
    assert error.value.code == "redacted"


def test_decode_validation_rejects_invalid_or_zero_duration(monkeypatch, tmp_path):
    path = tmp_path / "file.mp3"
    path.write_bytes(b"invalid")
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="codec_name=mp3\nformat_name=mp3\nduration=0\n"))
    with pytest.raises(tts.CloneError, match="invalid_audio"):
        tts.validate_audio(path)
    def broken(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "ffprobe")
    monkeypatch.setattr(tts.subprocess, "run", broken)
    with pytest.raises(tts.CloneError, match="invalid_audio"):
        tts.validate_audio(path)


def test_unsupported_voice_id_never_calls_provider(configured, monkeypatch):
    monkeypatch.setenv("TTS_CLONE_VOICE_ID", "some-id")
    _, factory = fake_session(monkeypatch)
    client = MagicMock()
    tts.generate_speech(client, "Hello", str(configured / "result.mp3"))
    factory.assert_not_called()
    client.audio.speech.create.assert_called_once()


def test_download_http_error_falls_back(configured, monkeypatch):
    session, _ = fake_session(monkeypatch)
    session.stream.return_value = Response(503)
    client = MagicMock()
    tts.generate_speech(client, "Hello", str(configured / "result.mp3"))
    client.audio.speech.create.assert_called_once()


def test_unexpected_provider_error_falls_back_without_leaking(configured, monkeypatch, caplog):
    session, _ = fake_session(monkeypatch)
    session.post.side_effect = RuntimeError("private information")
    client = MagicMock()
    tts.generate_speech(client, "Hello", str(configured / "result.mp3"))
    client.audio.speech.create.assert_called_once()
    assert "private information" not in caplog.text


def test_data_uri_encodes_local_mp3_without_logging(monkeypatch, tmp_path, caplog):
    reference = tmp_path / "reference.mp3"
    reference.write_bytes(b"private voice bytes")
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="codec_name=mp3\nformat_name=mp3\nduration=20\n"))
    monkeypatch.setattr(tts, "validate_audio", lambda *a, **k: 20.0)
    uri = tts.reference_data_uri(reference, 12)
    assert uri == tts.REFERENCE_URI_PREFIX + base64.b64encode(reference.read_bytes()).decode()
    assert uri not in caplog.text
    assert "private voice bytes" not in caplog.text


def test_long_reference_is_trimmed_and_original_preserved(monkeypatch, tmp_path):
    reference = tmp_path / "reference.mp3"
    reference.write_bytes(b"original")
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout="codec_name=mp3\nformat_name=mp3\nduration=40\n")
        Path(command[-1]).write_bytes(b"trimmed")
        return SimpleNamespace()
    monkeypatch.setattr(tts.subprocess, "run", run)
    monkeypatch.setattr(tts, "validate_audio", lambda *a, **k: 20.0)
    uri = tts.reference_data_uri(reference, 12)
    assert base64.b64decode(uri.split(",", 1)[1]) == b"trimmed"
    assert commands[1][commands[1].index("-t") + 1] == "20"
    assert reference.read_bytes() == b"original"


@pytest.mark.parametrize("duration,codec", [("0", "mp3"), ("20", "aac"), ("nan", "mp3")])
def test_reference_invalid_before_network(monkeypatch, tmp_path, duration, codec):
    reference = tmp_path / "reference.mp3"
    reference.write_bytes(b"invalid")
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout=f"codec_name={codec}\nformat_name=mp3\nduration={duration}\n"))
    with pytest.raises(tts.CloneError, match="invalid_reference"):
        tts.reference_data_uri(reference, 12)


def test_reference_size_limit_before_read(monkeypatch, tmp_path):
    reference = tmp_path / "reference.mp3"
    reference.write_bytes(b"x" * 8)
    monkeypatch.setattr(tts, "MAX_REFERENCE_URI_LENGTH", len(tts.REFERENCE_URI_PREFIX) + 4)
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="codec_name=mp3\nformat_name=mp3\nduration=20\n"))
    monkeypatch.setattr(tts, "validate_audio", lambda *a, **k: 20.0)
    with pytest.raises(tts.CloneError, match="reference_too_large"):
        tts.reference_data_uri(reference, 12)


def test_rejected_data_uri_falls_back_without_exposing_it(configured, monkeypatch, caplog):
    session, _ = fake_session(monkeypatch, post=Response(400, {"error": {"code": "bad-private-data"}}))
    client = MagicMock()
    tts.generate_speech(client, "Hello", str(configured / "result.mp3"))
    client.audio.speech.create.assert_called_once_with(model="tts-1", voice="fable", input="Hello")
    assert session.post.call_count == 1
    assert tts.REFERENCE_URI_PREFIX not in caplog.text
    assert "bad-private-data" not in caplog.text


def test_reference_preparation_failure_never_contacts_provider(configured, monkeypatch):
    def invalid(*args, **kwargs):
        raise tts.CloneError("invalid_reference")
    monkeypatch.setattr(tts, "reference_data_uri", invalid)
    _, factory = fake_session(monkeypatch)
    client = MagicMock()
    tts.generate_speech(client, "Hello", str(configured / "result.mp3"))
    factory.assert_not_called()
    client.audio.speech.create.assert_called_once()


def test_probe_offline_never_calls_clone(monkeypatch, tmp_path, capsys):
    import importlib.util
    script = Path(__file__).resolve().parent.parent / "scripts" / "probe_tts_clone.py"
    spec = importlib.util.spec_from_file_location("probe_tts_clone", script)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    monkeypatch.setattr(probe, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(probe, "configuration", lambda **kwargs: ("private-reference", 12))
    prepare = MagicMock(return_value="data:audio/mp3;base64,private")
    clone = MagicMock()
    monkeypatch.setattr(probe, "reference_data_uri", prepare)
    monkeypatch.setattr(probe, "clone_speech", clone)
    monkeypatch.setattr(sys, "argv", ["probe_tts_clone.py"])
    assert probe.main() == 0
    prepare.assert_called_once_with("private-reference", 12)
    clone.assert_not_called()
    output = capsys.readouterr().out
    assert "HTTP=not_called" in output
    assert "private" not in output


def test_probe_live_makes_one_task_and_reports_submission(monkeypatch, tmp_path, capsys):
    import importlib.util
    script = Path(__file__).resolve().parent.parent / "scripts" / "probe_tts_clone.py"
    spec = importlib.util.spec_from_file_location("probe_tts_clone_live", script)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    monkeypatch.setattr(probe, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(probe, "configuration", lambda **kwargs: ("private-reference", 12))
    monkeypatch.setattr(probe, "reference_data_uri", lambda *a, **k: "data:audio/mp3;base64,private")
    def clone(*args, **kwargs):
        kwargs["observer"](200)
        kwargs["observer"](200)
        return 1.25
    mock_clone = MagicMock(side_effect=clone)
    monkeypatch.setattr(probe, "clone_speech", mock_clone)
    monkeypatch.setattr(sys, "argv", ["probe_tts_clone.py", "--live"])
    assert probe.main() == 0
    mock_clone.assert_called_once()
    output = capsys.readouterr().out
    assert "submission_http=200" in output
    assert "valid_mp3" in output
    assert "private" not in output
