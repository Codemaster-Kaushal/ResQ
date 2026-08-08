"""Defensive JSON extraction from model output (TRD §5).

Providers are asked for strict JSON and frequently return something close to it:
fenced in ``` blocks, prefixed with "Here is the JSON:", or trailed by an explanation.
Everything here exists to salvage the object without ever raising something the router
would not catch.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.base import ProviderError

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _balanced_object(text: str) -> str | None:
    """Return the first complete ``{...}`` block, respecting nesting and strings."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def extract_json_object(raw: str) -> dict[str, Any]:
    """Pull one JSON object out of a model response, or raise ProviderError."""
    if not raw or not raw.strip():
        raise ProviderError("provider returned an empty response")

    candidates: list[str] = []

    fenced = _FENCE.search(raw)
    if fenced:
        candidates.append(fenced.group(1).strip())

    candidates.append(raw.strip())

    balanced = _balanced_object(raw)
    if balanced:
        candidates.append(balanced)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ProviderError("provider response contained no JSON object")
