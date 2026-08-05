"""canonical MoleculeRecord -> representations — BUILD_PLAN.md §4b/§11.

Two representations, each independently opt-in via the `representations` job
parameter: a graph batch (node/edge features) for GNN-based models
(Chemprop, ProteinMPNN-style), and a tokenized canonical-SMILES sequence for
SMILES-LM-based models (REINVENT).
"""

_ATOM_VOCAB = ["C", "N", "O", "S", "P", "F", "Cl", "Br", "I", "H", "*"]

# Token vocab covers canonical-SMILES characters RDKit actually emits; anything
# outside it maps to UNK rather than raising, since new atom types shouldn't
# break tokenization the way they would parsing.
PAD, BOS, EOS, UNK = 0, 1, 2, 3
_VOCAB_CHARS = "()[]=#-+\\/@.%0123456789" + "".join(_ATOM_VOCAB) + "abcdefghijklmnopqrstuvwxyz"
TOKEN_VOCAB = {ch: i + 4 for i, ch in enumerate(_VOCAB_CHARS)}
VOCAB_SIZE = len(TOKEN_VOCAB) + 4


def to_graph(record) -> dict:
    mol = record.mol
    node_features = []
    for atom in mol.GetAtoms():
        symbol = atom.GetSymbol()
        one_hot = [1.0 if symbol == s else 0.0 for s in _ATOM_VOCAB]
        node_features.append(one_hot + [float(atom.GetDegree()), float(atom.GetFormalCharge())])

    edge_index = []
    edge_features = []
    for bond in mol.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        bond_order = float(bond.GetBondTypeAsDouble())
        edge_index.append([i, j])
        edge_index.append([j, i])
        edge_features.append([bond_order])
        edge_features.append([bond_order])

    return {
        "representation_type": "molecule_graph",
        "num_nodes": mol.GetNumAtoms(),
        "node_features": node_features,
        "edge_index": edge_index,
        "edge_features": edge_features,
    }


def to_tokens(record, max_len: int = 128) -> dict:
    token_ids = [BOS] + [TOKEN_VOCAB.get(ch, UNK) for ch in record.canonical_smiles]
    token_ids.append(EOS)
    source_length = len(token_ids) - 2  # excludes BOS/EOS, matches record.canonical_smiles length
    token_ids = token_ids[:max_len]
    attention_mask = [1] * len(token_ids)
    pad_len = max_len - len(token_ids)
    token_ids += [PAD] * pad_len
    attention_mask += [0] * pad_len

    return {
        "representation_type": "molecule_tokens",
        "token_ids": token_ids,
        "attention_mask": attention_mask,
        "source_length": source_length,
    }
