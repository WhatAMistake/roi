"""Opt-in CometAPI reference-audio TTS; the existing OpenAI TTS is the fallback."""

import base64
import ipaddress
import logging
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import quote, urlsplit

import httpx

log = logging.getLogger(__name__)
BASE = "https://api.cometapi.com"
MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_REFERENCE_INPUT_BYTES = 100 * 1024 * 1024
MAX_REFERENCE_URI_LENGTH = 5 * 1024 * 1024
REFERENCE_URI_PREFIX = "data:audio/mp3;base64,"


class CloneError(Exception):
    def __init__(self, category, status=None, code=None):
        super().__init__(category)
        self.category = category
        self.status = status
        self.code = safe_code(code)


def safe_code(value):
    value = str(value or "")
    if not value:
        return None
    # Provider-supplied fields are untrusted and can echo input or credentials.
    if re.fullmatch(r"[0-9]{1,8}", value) or value in {
        "task_not_exist", "rate_limit", "rate_limit_exceeded",
        "invalid_api_key", "invalid_request_error", "model_not_found",
        "insufficient_quota", "server_error", "SAFETY.INPUT.IMAGE",
    }:
        return value
    return "redacted"


def classify_status(status):
    if status in (401, 403):
        return "authentication"
    if status == 429:
        return "rate_limit"
    if status in (404, 405, 501):
        return "unsupported"
    if status in (400, 422):
        return "parameters"
    if status >= 500:
        return "unavailable"
    return "api_error"


def check_response(response):
    if 200 <= response.status_code < 300:
        return
    code = None
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error")
            code = error.get("code") if isinstance(error, dict) else body.get("code")
    except ValueError:
        pass
    raise CloneError(classify_status(response.status_code), response.status_code, code)


def configuration(api_key=None, require_verified=True):
    """Return an actionable failure reason without making network requests."""
    model = os.getenv("TTS_CLONE_MODEL", "").strip()
    if model != "seed_audio":
        raise CloneError("unsupported_model")
    if os.getenv("TTS_CLONE_VOICE_ID", "").strip():
        raise CloneError("voice_id_not_supported")
    if not (api_key or os.getenv("OPENAI_API_KEY") or os.getenv("COMET_API_KEY")):
        raise CloneError("missing_key")
    reference = os.getenv("TTS_REFERENCE_PATH", "").strip()
    try:
        reference_ok = bool(reference) and Path(reference).is_file() and Path(reference).stat().st_size > 0
    except OSError:
        reference_ok = False
    if not reference_ok:
        raise CloneError("missing_reference")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise CloneError("missing_audio_tools")
    try:
        timeout = float(os.getenv("TTS_CLONE_TIMEOUT", "12"))
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError
    except ValueError:
        raise CloneError("invalid_timeout") from None
    if require_verified and os.getenv("TTS_CLONE_VERIFIED") != "1":
        raise CloneError("not_verified")
    return reference, timeout


def validate_audio(path, timeout=5):
    """Fully decode an MP3 and verify an audio stream with positive duration."""
    deadline = time.monotonic() + timeout
    try:
        info = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name:format=format_name,duration",
             "-of", "default=noprint_wrappers=1", str(path)],
            capture_output=True, text=True, timeout=timeout, check=True,
        ).stdout
        fields = dict(line.split("=", 1) for line in info.splitlines() if "=" in line)
        duration = float(fields.get("duration", "0"))
        if fields.get("codec_name") != "mp3" or "mp3" not in fields.get("format_name", "").split(",") or not math.isfinite(duration) or duration <= 0:
            raise ValueError
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
            capture_output=True, timeout=remaining, check=True,
        )
        return duration
    except (OSError, ValueError, subprocess.SubprocessError):
        raise CloneError("invalid_audio") from None


def reference_data_uri(reference, timeout):
    """Validate a local MP3 and, when needed, trim its first 20 seconds in private."""
    deadline = time.monotonic() + timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise CloneError("timeout")
        return seconds

    try:
        source = Path(reference)
        if source.stat().st_size > MAX_REFERENCE_INPUT_BYTES:
            raise CloneError("reference_too_large")
        info = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name:format=format_name,duration",
             "-of", "default=noprint_wrappers=1", str(source)],
            capture_output=True, text=True, timeout=remaining(), check=True,
        ).stdout
        fields = dict(line.split("=", 1) for line in info.splitlines() if "=" in line)
        duration = float(fields.get("duration", "0"))
        if (fields.get("codec_name") != "mp3"
                or "mp3" not in fields.get("format_name", "").split(",")
                or not math.isfinite(duration) or duration < 1):
            raise CloneError("invalid_reference")
        with tempfile.TemporaryDirectory() as tmp:
            prepared = source
            if duration > 25:
                prepared = Path(tmp) / "reference.mp3"
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-xerror", "-i", str(source),
                     "-t", "20", "-map", "0:a:0", "-vn", "-c:a", "libmp3lame",
                     "-b:a", "128k", "-f", "mp3", "-y", str(prepared)],
                    capture_output=True, timeout=remaining(), check=True,
                )
            try:
                actual_duration = validate_audio(prepared, timeout=remaining())
            except CloneError:
                raise CloneError("invalid_reference") from None
            if actual_duration < 1 or actual_duration > 25.5:
                raise CloneError("invalid_reference")
            # Check the size before reading/encoding, including Base64 overhead.
            size = prepared.stat().st_size
            if size <= 0 or len(REFERENCE_URI_PREFIX) + 4 * ((size + 2) // 3) > MAX_REFERENCE_URI_LENGTH:
                raise CloneError("reference_too_large")
            data = prepared.read_bytes()
            if not data or len(REFERENCE_URI_PREFIX) + 4 * ((len(data) + 2) // 3) > MAX_REFERENCE_URI_LENGTH:
                raise CloneError("reference_too_large")
            remaining()
            return REFERENCE_URI_PREFIX + base64.b64encode(data).decode("ascii")
    except subprocess.TimeoutExpired:
        raise CloneError("timeout") from None
    except (OSError, ValueError, subprocess.SubprocessError):
        raise CloneError("invalid_reference") from None


def _public_https(url):
    """Reject local/private destinations and redirects for provider output."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (OSError, ValueError):
        return False


def clone_speech(text, output_path, api_key=None, require_verified=True, observer=None):
    """Submit exactly one generation task, then poll and download within a deadline."""
    reference, timeout = configuration(api_key, require_verified)
    key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("COMET_API_KEY")
    if not text or len(text) > 2048:
        raise CloneError("parameters")
    deadline = time.monotonic() + timeout

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise CloneError("timeout")
        return value

    def report(response):
        if observer:
            observer(response.status_code)
        check_response(response)

    try:
        audio_uri = reference_data_uri(reference, timeout=remaining())
        with httpx.Client(follow_redirects=False, trust_env=False) as session:
            headers = {"Authorization": "Bearer " + key}
            response = session.post(
                BASE + "/runwayml/v1/text_to_speech", headers=headers,
                json={"model": "seed_audio", "promptText": text,
                      "voice": {"type": "reference-audio", "audioUri": audio_uri},
                      "outputFormat": "mp3"},
                timeout=remaining(),
            )
            report(response)
            task = response.json()
            task_id = task.get("id")
            if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
                raise CloneError("invalid_response", response.status_code)
            for attempt in range(5):
                if task.get("status") == "SUCCEEDED":
                    break
                if task.get("status") in ("FAILED", "CANCELLED"):
                    raise CloneError("task_failed", response.status_code, task.get("failureCode"))
                if attempt:
                    time.sleep(min(1, remaining()))
                response = session.get(BASE + "/runwayml/v1/tasks/" + quote(task_id),
                                       headers=headers, timeout=remaining())
                if response.status_code == 400:
                    try:
                        if response.json().get("code") == "task_not_exist":
                            if observer:
                                observer(response.status_code)
                            continue
                    except (ValueError, AttributeError):
                        pass
                report(response)
                task = response.json()
            if task.get("status") != "SUCCEEDED":
                raise CloneError("timeout")
            outputs = task.get("output")
            if not isinstance(outputs, list) or not outputs or not isinstance(outputs[0], str) or not _public_https(outputs[0]):
                raise CloneError("invalid_output_url")
            with session.stream("GET", outputs[0], timeout=remaining()) as audio:
                if observer:
                    observer(audio.status_code)
                # Streaming bodies have not been read; do not parse error JSON.
                if not 200 <= audio.status_code < 300:
                    raise CloneError(classify_status(audio.status_code), audio.status_code)
                data = bytearray()
                for chunk in audio.iter_bytes():
                    remaining()
                    data.extend(chunk)
                    if len(data) > MAX_AUDIO_BYTES:
                        raise CloneError("invalid_audio")
            if not data:
                raise CloneError("invalid_audio")
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "speech.mp3"
                path.write_bytes(data)
                duration = validate_audio(path, timeout=min(5, remaining()))
                remaining()
                Path(output_path).write_bytes(data)
            return duration
    except httpx.TimeoutException:
        raise CloneError("timeout") from None
    except httpx.HTTPError:
        raise CloneError("unavailable") from None
    except (ValueError, TypeError, AttributeError, OSError):
        raise CloneError("invalid_response") from None


def generate_speech(client, text, output_path, api_key=None):
    provider = os.getenv("TTS_PROVIDER", "fable").strip().lower()
    if provider == "clone":
        try:
            clone_speech(text, output_path, api_key=api_key)
            log.info("TTS used reference-audio model seed_audio")
            return output_path
        except CloneError as exc:
            log.warning("TTS fallback to fable: %s (HTTP %s, code %s)",
                        exc.category, exc.status, exc.code)
        except Exception as exc:
            # Never expose provider exception text, URLs or input text.
            log.warning("TTS fallback to fable: unexpected %s", type(exc).__name__)
    elif provider != "fable":
        log.warning("TTS fallback to fable: unknown provider")
    # TTS_MODEL remains authoritative when set, preserving the old configuration.
    model = os.getenv("TTS_MODEL") or os.getenv("TTS_FALLBACK_MODEL") or "tts-1"
    voice = os.getenv("TTS_FALLBACK_VOICE") or "fable"
    response = client.audio.speech.create(model=model, voice=voice, input=text)
    response.stream_to_file(output_path)
    log.info("TTS used fallback model=%s voice=%s", model, voice)
    return output_path
