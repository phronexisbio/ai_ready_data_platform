"""canonical SequenceRecord -> representations — BUILD_PLAN.md §4b/§11.

MSA generation is stubbed per BUILD_PLAN.md §10 Phase 3 ("MSA generation
stubbed initially") — real MSA (MMseqs2) lands in a later phase; only
tokenization is implemented now.
"""

_PROTEIN_VOCAB = "ACDEFGHIKLMNPQRSTVWYXBZJUO*"
_NUCLEOTIDE_VOCAB = "ACGTUN"
PAD, BOS, EOS, UNK = 0, 1, 2, 3

_PROTEIN_TOKEN_VOCAB = {ch: i + 4 for i, ch in enumerate(_PROTEIN_VOCAB)}
_NUCLEOTIDE_TOKEN_VOCAB = {ch: i + 4 for i, ch in enumerate(_NUCLEOTIDE_VOCAB)}
VOCAB_SIZE = max(len(_PROTEIN_TOKEN_VOCAB), len(_NUCLEOTIDE_TOKEN_VOCAB)) + 4


def _vocab_for(alphabet: str) -> dict:
    return _NUCLEOTIDE_TOKEN_VOCAB if alphabet == "nucleotide" else _PROTEIN_TOKEN_VOCAB


def to_tokens(record, max_len: int = 512) -> dict:
    vocab = _vocab_for(record.alphabet)
    token_ids = [BOS] + [vocab.get(ch, UNK) for ch in record.sequence]
    token_ids.append(EOS)
    source_length = len(token_ids) - 2  # excludes BOS/EOS, matches record.sequence length
    token_ids = token_ids[:max_len]
    attention_mask = [1] * len(token_ids)
    pad_len = max_len - len(token_ids)
    token_ids += [PAD] * pad_len
    attention_mask += [0] * pad_len

    return {
        "representation_type": "sequence_tokens",
        "token_ids": token_ids,
        "attention_mask": attention_mask,
        "source_length": source_length,
    }


def to_msa(record) -> dict | None:
    """Stub — MMseqs2-based MSA generation is a later phase."""
    return None
