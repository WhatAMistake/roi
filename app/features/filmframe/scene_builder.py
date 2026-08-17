"""Scene builder: uses LLM to translate inner state into a physical film scene."""

import json
import logging
import re
from typing import Any, Optional

from openai import OpenAI

from .config import (
    SCENE_BUILDER_SYSTEM_PROMPT,
    FILM_FRAME_SCENE_LLM_MODEL,
    build_image_prompt,
)

logger = logging.getLogger("filmframe.scene_builder")

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_PREVIEW_RE = re.compile(
    r'["\']preview["\']\s*:\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')',
    re.DOTALL,
)
_SCENE_RE = re.compile(
    r'["\']scene["\']\s*:\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')',
    re.DOTALL,
)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if the model wrapped JSON in them."""
    text = text.strip()
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _extract_json_object(text: str) -> Optional[str]:
    """Find the outermost JSON object in free-form model output."""
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    quote = ""

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_string = False
            continue

        if ch in ('"', "'"):
            in_string = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _decode_json_string(raw: str) -> str:
    """Decode a JSON string literal, falling back to a light unescape."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        body = raw[1:-1] if len(raw) >= 2 and raw[0] in "\"'" else raw
        return (
            body.replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\'", "'")
            .replace("\\\\", "\\")
        )


def _extract_field(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    return _decode_json_string(match.group(1)).strip()


def _extract_truncated_field(field_name: str, text: str) -> str:
    """Recover a string field value even when the closing quote was cut off."""
    marker = f'"{field_name}"'
    idx = text.find(marker)
    if idx < 0:
        marker = f"'{field_name}'"
        idx = text.find(marker)
        if idx < 0:
            return ""

    colon = text.find(":", idx + len(marker))
    if colon < 0:
        return ""

    i = colon + 1
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] not in "\"'":
        return ""

    quote = text[i]
    i += 1
    chars: list[str] = []
    escape = False
    while i < len(text):
        ch = text[i]
        if escape:
            chars.append(ch)
            escape = False
        elif ch == "\\":
            chars.append(ch)
            escape = True
        elif ch == quote:
            break
        else:
            chars.append(ch)
        i += 1

    value = "".join(chars).strip()
    # If truncated mid-sentence, cut back to a clean boundary.
    if value and value[-1] not in ".!?…":
        for sep in (". ", "! ", "? ", "… "):
            cut = value.rfind(sep)
            if cut > 40:
                return value[: cut + 1].strip()
        # Prefer ending on a comma/clause if no sentence end exists.
        cut = max(value.rfind(", "), value.rfind("; "))
        if cut > 40:
            return value[:cut].strip()
    return value


def _looks_like_json_blob(text: str) -> bool:
    stripped = text.strip()
    return (
        stripped.startswith("{")
        or stripped.startswith("```")
        or '"preview"' in stripped
        or "'preview'" in stripped
    )


def parse_scene_payload(raw: Any) -> dict:
    """Parse model output into preview/scene without leaking raw JSON to users."""
    if raw is None:
        raise ValueError("Empty scene-builder response")

    if isinstance(raw, dict):
        preview = str(raw.get("preview", "") or "").strip()
        scene = str(raw.get("scene", "") or "").strip()
        if not preview and not scene:
            raise ValueError("Scene payload missing preview/scene")
        if not preview:
            preview = scene
        if not scene:
            scene = preview
        return {"preview": preview, "scene": scene}

    text = str(raw).strip()
    if not text:
        raise ValueError("Empty scene-builder response")

    cleaned = _strip_code_fences(text)
    candidates = [cleaned]
    extracted = _extract_json_object(cleaned)
    if extracted and extracted not in candidates:
        candidates.append(extracted)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return parse_scene_payload(data)

    # Truncated / almost-JSON responses: pull string fields directly.
    preview = _extract_field(_PREVIEW_RE, cleaned) or _extract_truncated_field("preview", cleaned)
    scene = _extract_field(_SCENE_RE, cleaned) or _extract_truncated_field("scene", cleaned)
    if preview or scene:
        if not preview:
            preview = scene
        if not scene:
            scene = preview
        logger.warning("Scene builder used field extraction on non-JSON payload")
        return {"preview": preview, "scene": scene}

    # Last resort: plain prose only. Never show raw JSON/markdown blobs.
    if _looks_like_json_blob(text):
        raise ValueError("Could not parse scene-builder JSON payload")

    plain = cleaned.strip()
    return {"preview": plain[:500], "scene": plain}


class SceneBuilder:
    """Builds a film-photograph scene description from user's inner state text."""

    def __init__(self, api_key: str, api_base: str, model: Optional[str] = None):
        self.client = OpenAI(api_key=api_key, base_url=api_base)
        self.model = model or FILM_FRAME_SCENE_LLM_MODEL

    def _request(self, user_description: str, *, force_json_mode: bool) -> str:
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SCENE_BUILDER_SYSTEM_PROMPT},
                {"role": "user", "content": user_description},
            ],
            "temperature": 0.8,
            "max_tokens": 2000,
        }
        if force_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("Scene builder returned empty content")
        return content

    def build_scene(self, user_description: str) -> dict:
        """Convert user's state description into a scene.

        Returns a dict with keys: 'preview' (str), 'scene' (str), 'image_prompt' (str).
        """
        raw = None
        last_error: Optional[Exception] = None

        for force_json_mode in (True, False):
            try:
                raw = self._request(user_description, force_json_mode=force_json_mode)
                logger.info(
                    "Scene builder raw response (json_mode=%s, model=%s): %s",
                    force_json_mode,
                    self.model,
                    raw[:500],
                )
                result = parse_scene_payload(raw)
                break
            except Exception as e:
                last_error = e
                logger.warning(
                    "Scene builder attempt failed (json_mode=%s): %s",
                    force_json_mode,
                    e,
                )
                result = None
        else:
            logger.error("Scene builder failed after retries: %s", last_error)
            raise RuntimeError(f"Scene builder failed: {last_error}") from last_error

        preview = result["preview"].strip()
        scene = result["scene"].strip()

        if not preview or not scene:
            raise RuntimeError("Scene builder returned empty preview/scene")

        # Guard against accidentally showing JSON leftovers.
        if _looks_like_json_blob(preview):
            raise RuntimeError("Scene builder preview still looks like raw JSON")

        image_prompt = build_image_prompt(scene)
        return {
            "preview": preview,
            "scene": scene,
            "image_prompt": image_prompt,
        }

    def apply_edit(self, current_scene: str, edit_instruction: str) -> dict:
        """Apply an edit instruction to update the scene.

        Returns the same dict structure as build_scene.
        """
        prompt = (
            f"CURRENT SCENE:\n{current_scene}\n\n"
            f"EDIT INSTRUCTION:\n{edit_instruction}\n\n"
            f"Update the scene description based on the edit instruction. "
            f"Keep all the same rules: only physical characteristics, no emotional labels, no cliché symbols."
        )
        return self.build_scene(prompt)
