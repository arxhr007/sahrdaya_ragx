"""Rough token accounting for response metadata.

This is a character-count heuristic, not a real tokenizer -- it exists only so the
API can report approximate sizes back to the client. Nothing throttles on it.
"""


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
