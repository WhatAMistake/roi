"""Film-frame feature: configuration constants loaded from environment."""

import os
from dotenv import load_dotenv

load_dotenv()

# Feature gate
FILM_FRAME_ENABLED: bool = os.getenv("FILM_FRAME_ENABLED", "false").strip().lower() == "true"

# Unlimited IDs: comma-separated user IDs that ignore daily limits (empty = everyone uses limits)
_raw_allowed = os.getenv("FILM_FRAME_ALLOWED_USER_IDS", "").strip()
FILM_FRAME_ALLOWED_USER_IDS: set[int] = set()
if _raw_allowed:
    for part in _raw_allowed.split(","):
        part = part.strip()
        if part:
            try:
                FILM_FRAME_ALLOWED_USER_IDS.add(int(part))
            except ValueError:
                pass

# Image model
FILM_FRAME_MODEL: str = os.getenv("FILM_FRAME_MODEL", "seedream-5-0-pro-260628")

# Image API base URL (uses OPENAI_API_BASE / COMET_API_KEY via existing client)
FILM_FRAME_IMAGE_API_BASE: str = os.getenv("FILM_FRAME_IMAGE_API_BASE", os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"))
FILM_FRAME_IMAGE_API_KEY: str = os.getenv("FILM_FRAME_IMAGE_API_KEY", os.getenv("COMET_API_KEY", os.getenv("OPENAI_API_KEY", "")))

# Limits
FILM_FRAME_PER_USER_DAILY_LIMIT: int = int(os.getenv("FILM_FRAME_PER_USER_DAILY_LIMIT", "3"))
FILM_FRAME_GLOBAL_DAILY_LIMIT: int = int(os.getenv("FILM_FRAME_GLOBAL_DAILY_LIMIT", "50"))

# LLM model for scene building (reuses OPENAI_API_BASE/KEY)
FILM_FRAME_SCENE_LLM_MODEL: str = os.getenv("FILM_FRAME_SCENE_LLM_MODEL", os.getenv("OPENAI_MODEL", "deepseek-v4-pro"))

# Image prompt parts — assembled style-first (models overweight the start).
FILM_STYLE_LOCK: str = (
    "Amateur 35mm point-and-shoot snapshot on Kodak Portra 400. "
    "Soft focus, visible film grain, muted pastel colors, slight haze, "
    "natural available light, imperfect exposure, shallow depth of field."
)

FILM_CAMERA_LOCK: str = (
    "Handheld eye-level crop, candid imperfect framing, one main subject close or medium, "
    "background mostly out of focus, something cut off by the frame edge. "
    "Low information density, casual consumer photo, not a wide establishing shot."
)

FILM_NEGATIVES: str = (
    "No ultra-sharp detail, no hyperdetailed textures, no 8k, no HDR, "
    "no cinematic color grade, no glossy AI look, no studio lighting, "
    "no perfect symmetry, no deep-focus landscape, no text, no watermarks, "
    "no logos, no signatures, no frames or borders."
)

# Backward-compatible combined suffix (style + camera + negatives).
FILM_TECHNICAL_SUFFIX: str = f"{FILM_STYLE_LOCK} {FILM_CAMERA_LOCK} {FILM_NEGATIVES}"


def build_image_prompt(scene: str) -> str:
    """Assemble final image prompt: style first, then scene, camera, negatives."""
    scene_text = (scene or "").strip()
    parts = [FILM_STYLE_LOCK]
    if scene_text:
        parts.append(f"Scene: {scene_text}")
    parts.append(FILM_CAMERA_LOCK)
    parts.append(FILM_NEGATIVES)
    return " ".join(parts).strip()


# Scene-building system prompt
SCENE_BUILDER_SYSTEM_PROMPT: str = """You are a visual director for casual amateur film snapshots.
Translate a person's inner state into ONE simple physical moment a cheap 35mm camera could catch.

CRITICAL RULES:
1. Describe ONLY observable physical things: subject, place hint, light source, weather, what is soft/out of focus, what is cut off by the frame.
2. Keep the scene SIMPLE and INTIMATE:
   - exactly one main subject
   - one environment hint (not a full landscape tour)
   - one light source
   - one imperfection (soft focus, haze, cut-off edge, slight blur)
3. Prefer close or medium shot. Avoid wide establishing shots, deep-focus vistas, and busy multi-object scenes.
4. Frame like a handheld point-and-shoot photo: imperfect crop, soft background, low detail, candid.
5. NEVER use emotional labels: sad, lonely, anxious, depressive, melancholic, existential, hopeless.
6. Never use cliché symbols: crying person = loneliness, grave/skull = death, mountain peak = freedom, crossroads = choice.
7. Meaning comes from the scene itself, not adjectives.
8. A person does NOT have to be in the frame.
9. Do NOT invent the user's appearance, gender, age, or ethnicity.
10. Each scene must be grounded in the user's description. Never default to a generic dark room at night.
11. Do NOT describe ultra-sharp detail, perfect symmetry, glossy cinematic production, or HDR clarity.
12. "scene" must be 1-3 short English sentences, concrete and low-detail — not a long inventory of everything in view.
13. Respond with ONLY a valid JSON object. No markdown, no code fences, no commentary.
14. JSON schema:
{
  "preview": "2-4 sentences, human-readable, same language as the user",
  "scene": "1-3 short English sentences: one subject, place hint, light, imperfection; no emotional labels"
}
"""