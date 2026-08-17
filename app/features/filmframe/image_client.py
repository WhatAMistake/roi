"""Async image generation client using OpenAI-compatible images API."""

import asyncio
import logging
from typing import Optional
from openai import OpenAI, APIConnectionError, APITimeoutError
from .config import FILM_FRAME_MODEL, FILM_FRAME_IMAGE_API_BASE, FILM_FRAME_IMAGE_API_KEY

logger = logging.getLogger("filmframe.image_client")

# Retry config for flaky upstream (CometAPI drops connections intermittently)
_MAX_RETRIES = 3
_RETRY_BACKOFF = [5, 15, 30]  # seconds between retries


class ImageClient:
    """Thin adapter for image generation via OpenAI-compatible endpoint."""

    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None):
        self.api_key = api_key or FILM_FRAME_IMAGE_API_KEY
        self.api_base = api_base or FILM_FRAME_IMAGE_API_BASE
        self.model = FILM_FRAME_MODEL
        # Disable SDK's own retries: they fire too fast against a flaky endpoint.
        # We handle retries ourselves with backoff below.
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            max_retries=0,
        )

    async def generate_image(self, prompt: str, size: str = "1024x1024") -> dict:
        """Generate an image from a text prompt.

        Returns a dict with keys: 'url' (str|None), 'b64_json' (str|None),
        'seed' (int|None), 'latency_ms' (int).

        Runs the blocking OpenAI call in a thread pool to avoid blocking the event loop.
        Retries on connection errors with backoff.
        """
        start = asyncio.get_event_loop().time()

        def _call():
            return self.client.images.generate(
                model=self.model,
                prompt=prompt,
                n=1,
                size=size,
                response_format="b64_json",
            )

        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await asyncio.get_event_loop().run_in_executor(None, _call)
                break
            except (APIConnectionError, APITimeoutError) as e:
                last_exc = e
                elapsed = int((asyncio.get_event_loop().time() - start) * 1000)
                logger.warning(
                    f"Image generation attempt {attempt}/{_MAX_RETRIES} failed "
                    f"after {elapsed}ms: {type(e).__name__}: {e}"
                )
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BACKOFF[attempt - 1]
                    logger.info(f"Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    raise
            except Exception as e:
                elapsed_ms = int((asyncio.get_event_loop().time() - start) * 1000)
                logger.error(f"Image generation failed after {elapsed_ms}ms: {e}")
                raise
        else:
            raise last_exc  # type: ignore[misc]

        elapsed_ms = int((asyncio.get_event_loop().time() - start) * 1000)
        data = response.data[0] if response.data else None

        result = {
            "url": getattr(data, "url", None) if data else None,
            "b64_json": getattr(data, "b64_json", None) if data else None,
            "seed": None,
            "latency_ms": elapsed_ms,
        }

        # Try to extract seed from revised_prompt or other metadata
        if data:
            revised = getattr(data, "revised_prompt", "") or ""
            if "seed" in revised.lower():
                import re
                m = re.search(r'seed[:\s]+(\d+)', revised, re.IGNORECASE)
                if m:
                    result["seed"] = int(m.group(1))

        return result