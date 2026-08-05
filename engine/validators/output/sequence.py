"""Output validation for sequence tokenization — BUILD_PLAN.md §6:
no unexpected tokens/padding overrun, length matches source, MSA depth above
a minimum threshold if MSA was requested (MSA is stubbed in Phase 3, so that
check is a no-op until MSA generation is implemented).
"""

from engine.pipelines.sequence_pipeline.featurize import VOCAB_SIZE


def validate_tokens(tokens: dict) -> tuple[bool, str | None]:
    token_ids = tokens.get("token_ids", [])
    if not token_ids:
        return False, "empty token sequence"
    if any(t < 0 or t >= VOCAB_SIZE for t in token_ids):
        return False, "token id out of vocab range"

    attention_mask = tokens.get("attention_mask", [])
    if len(attention_mask) != len(token_ids):
        return False, "attention_mask length mismatch"

    source_length = tokens.get("source_length", 0)
    unpadded_length = sum(attention_mask)
    expected = min(source_length + 2, len(token_ids))  # +2 for BOS/EOS, capped by truncation
    if unpadded_length != expected:
        return False, f"unpadded token length {unpadded_length} does not match source length {source_length} (+BOS/EOS)"

    return True, None


def validate_msa(msa: dict | None, min_depth: int) -> tuple[bool, str | None]:
    if msa is None:
        return False, "no MSA generated (stubbed in this pipeline version)"
    depth = msa.get("depth", 0)
    if depth < min_depth:
        return False, f"MSA depth {depth} below minimum {min_depth}"
    return True, None
