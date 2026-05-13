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
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=self.settings.draft_max_tokens,
            temperature=0.2,
            response_mime_type="application/json",
        )
        response = client.models.generate_content(
            model=self.settings.draft_model,
            contents=user_prompt,
            config=config,
        )
        raw = response.text or ""
        try:
            if not raw.strip():
                raise json.JSONDecodeError("Gemini returned an empty response.", raw, 0)
            return _parse_json(raw)
        except json.JSONDecodeError as exc:
            retry_prompt = (
                f"{user_prompt}\n\n"
                f"The previous response was empty or not valid JSON ({exc}). Regenerate the answer as one compact, "
                "strictly valid JSON object only. Do not use markdown fences or commentary. "
                "Escape all quotes and newlines inside string values."
            )
            retry_config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=self.settings.draft_max_tokens,
                temperature=0,
                response_mime_type="application/json",
            )
            retry = client.models.generate_content(
                model=self.settings.draft_model,
                contents=retry_prompt,
                config=retry_config,
            )
            retry_raw = retry.text or ""
            if not retry_raw.strip():
                raise json.JSONDecodeError("Gemini returned an empty response twice.", retry_raw, 0)
            return _parse_json(retry_raw)


def _parse_json(raw: str) -> dict[str, Any]:
    # Strip markdown code fences Gemini sometimes adds.
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    # Attempt 1: standard parse on cleaned text.
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt 2: extract outermost {...} block (handles preamble/postamble text).
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # Attempt 3: repair trailing commas before } or ] then re-parse.
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Could not parse Gemini response as JSON", raw, 0)
