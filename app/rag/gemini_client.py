"""Gemini 2.5 Flash client wrapper for structured drafting tasks."""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import get_settings


class GeminiClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if not self.settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("google-genai is required for Gemini draft generation.") from exc

        client = genai.Client(api_key=self.settings.gemini_api_key)
        response = client.models.generate_content(
            model=self.settings.draft_model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=self.settings.draft_max_tokens,
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        return _parse_json(response.text or "")


def _parse_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
