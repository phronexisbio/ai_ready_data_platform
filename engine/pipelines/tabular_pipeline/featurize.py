"""canonical TabularRecord -> representations — BUILD_PLAN.md §4b/§11.

Tabular assay/metadata rows don't naturally fit BUILD_PLAN §11's other three
container types (graph batch, dense image tensor, SE(3) frames) — token-ID
sequence is the one that does. Each "column=value" cell becomes one token via
a stable hash (the feature-hashing trick), so the same cell always maps to
the same token id without needing a trained/fitted vocabulary — deliberately
simple, matching this pipeline's 0.1.0 scope the same way sequence_pipeline's
tokenizer started before anything fancier was needed.
"""

import hashlib

PAD, BOS, EOS, UNK = 0, 1, 2, 3
VOCAB_SIZE = 50_000


def _hash_token(column: str, value: str) -> int:
    digest = hashlib.sha256(f"{column}={value}".encode("utf-8")).digest()
    return 4 + int.from_bytes(digest[:4], "big") % (VOCAB_SIZE - 4)


def to_tokens(record, max_len: int = 64) -> dict:
    token_ids = [BOS] + [_hash_token(c, v) for c, v in zip(record.columns, record.values)]
    token_ids.append(EOS)
    num_columns = len(token_ids) - 2  # excludes BOS/EOS, matches record.columns length
    token_ids = token_ids[:max_len]
    attention_mask = [1] * len(token_ids)
    pad_len = max_len - len(token_ids)
    token_ids += [PAD] * pad_len
    attention_mask += [0] * pad_len

    return {
        "representation_type": "tabular_tokens",
        "token_ids": token_ids,
        "attention_mask": attention_mask,
        "num_columns": num_columns,
    }
