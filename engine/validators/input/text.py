"""Input validation for JSON/plain-text files — BUILD_PLAN.md §6, input gate."""

import json


def validate(content: bytes) -> tuple[bool, str | None]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False, "not valid utf-8 text"

    stripped = text.strip()
    if not stripped:
        return False, "empty file"

    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
        except json.JSONDecodeError as e:
            return False, f"invalid JSON: {e}"

    return True, None
