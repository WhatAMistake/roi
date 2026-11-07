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
    "Amateur 35mm analog film photograph, with natural film grain and subtle optical imperfections. "
    "Plausible materials, textures and light; an unpolished photograph, not a digital illustration. "
    "Colors, contrast and light suit the scene, from muted to vivid."
)

FILM_CAMERA_LOCK: str = (
    "Candid, imperfect framing, as if a real camera happened upon the moment. "
    "The scene determines the viewpoint, distance, focus and composition."
)

FILM_NEGATIVES: str = (
    "No ultra-sharp detail, no hyperdetailed textures, no 8k, no HDR, "
    "no glossy AI look, no digital illustration, no overly staged production, "
    "no artificial symmetry, no text, no captions, no logos, no signatures, no frames or borders."
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
SCENE_BUILDER_SYSTEM_PROMPT: str = """You are a visual director for analog film snapshots.
Translate the emotional atmosphere of the person's message into one photographic moment,
not a literal illustration of their words. Attend to its particular details, energy and
ambiguity without diagnosing, pathologizing, or making claims about the person's mental health.

VISUAL DIRECTIONS:
Choose the direction that best fits this message, not a random assignment or a fixed
emotion-to-style mapping. Across many messages, use these as loose creative guidance,
not quotas: roughly 60% everyday poetics, 30% expressive photography, 10% soft magical
realism. Everyday poetics is the foundation, but none of its traits is mandatory.
- Everyday poetics: ordinary places and small details, often natural light and muted
  colors, a moment caught by chance.
- Expressive photography: stronger color or contrast, unexpected angles or composition,
  and surprising relationships between objects when they fit.
- Soft magical realism: occasionally a strange, dreamlike, even nearly impossible
  event, still captured as if it really happened in front of a camera.

CRITICAL RULES:
1. Ground each scene in the specific message, but use indirect associations, ambiguity
   and delicate visual metaphors rather than explaining or literally depicting it.
2. Avoid formulaic associations such as sadness = rain, loneliness = empty bench,
   joy = sunshine, and other stock symbols. Do not default to a dark room or melancholy.
3. The moment need not be beautiful: unease, awkwardness, absurdity, humor and visual
   emptiness are welcome when appropriate. Do not force surrealism or cheerfulness.
4. Every direction must look like an actual analog photograph: believable materials,
   textures and light, natural grain and optical imperfections; no digital illustration,
   excessive staging, gloss or artificial symmetry. Even an impossible event must look
   physically present in the photographed world.
5. Let the scene determine color, light, angle, distance and focus. Close or medium
   shots and imperfect framing are useful defaults, not requirements. Keep a clear
   visual focus without requiring one main subject, one light source or a cut-off edge.
6. Describe observable physical details in "scene", not emotional labels or an
   interpretation of the person. Keep it concrete and concise, not an inventory.
7. A person need not appear. Do not invent the user's appearance, gender, age or
   ethnicity. Do not add text, captions, logos, signatures or decorative borders.
8. "preview" describes the same scene in the user's language, without diagnosis or
   explaining what the image supposedly says about them.
9. Respond with ONLY a valid JSON object. No markdown, code fences or commentary.
10. JSON schema:
{
  "preview": "2-4 sentences, human-readable, same language as the user",
  "scene": "1-3 short English sentences describing the visible scene; no emotional labels"
}
"""
