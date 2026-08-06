"""Output validation for tabular tokenization — BUILD_PLAN.md §6: no
out-of-vocab token ids, attention_mask length matches token_ids, and the
unpadded length matches the row's actual column count (+BOS/EOS). Same shape
of check as sequence's validate_tokens, against tabular_pipeline's vocab.
"""

from engine.pipelines.tabular_pipeline.featurize import VOCAB_SIZE


def validate_tokens(tokens: dict) -> tuple[bool, str | None]:
    token_ids = tokens.get("token_ids", [])
    if not token_ids:
        return False, "empty token sequence"
    if any(t < 0 or t >= VOCAB_SIZE for t in token_ids):
        return False, "token id out of vocab range"

    attention_mask = tokens.get("attention_mask", [])
    if len(attention_mask) != len(token_ids):
        return False, "attention_mask length mismatch"

    num_columns = tokens.get("num_columns", 0)
    unpadded_length = sum(attention_mask)
    expected = min(num_columns + 2, len(token_ids))  # +2 for BOS/EOS, capped by truncation
    if unpadded_length != expected:
        return False, f"unpadded token length {unpadded_length} does not match column count {num_columns} (+BOS/EOS)"

    return True, None
