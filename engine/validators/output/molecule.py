"""Output validation for molecule representations — BUILD_PLAN.md §6.

Checks the tensor itself, not the input: a pipeline can run cleanly and still
produce a bad output (an empty/disconnected graph, an out-of-range token id).
"""

from rdkit import Chem

from engine.pipelines.molecule_pipeline.featurize import VOCAB_SIZE


def validate_graph(canonical_smiles: str, graph: dict) -> tuple[bool, str | None]:
    num_nodes = graph.get("num_nodes", 0)
    if num_nodes == 0:
        return False, "graph has zero nodes"

    node_features = graph.get("node_features", [])
    if len(node_features) != num_nodes:
        return False, f"node_features length {len(node_features)} != num_nodes {num_nodes}"

    edge_index = graph.get("edge_index", [])
    referenced = {idx for pair in edge_index for idx in pair}
    if num_nodes > 1 and not referenced:
        return False, "no edges in a multi-atom molecule (disconnected graph)"
    if any(idx < 0 or idx >= num_nodes for idx in referenced):
        return False, "edge_index references an out-of-range atom"

    # Round-trip: the canonical SMILES this graph was derived from must
    # re-parse to a molecule with the same atom count.
    reparsed = Chem.MolFromSmiles(canonical_smiles)
    if reparsed is None:
        return False, f"canonical SMILES does not re-parse: {canonical_smiles!r}"
    if reparsed.GetNumAtoms() != num_nodes:
        return False, f"round-trip atom count mismatch: {reparsed.GetNumAtoms()} != {num_nodes}"

    return True, None


def validate_tokens(tokens: dict) -> tuple[bool, str | None]:
    token_ids = tokens.get("token_ids", [])
    if not token_ids:
        return False, "empty token sequence"
    if any(t < 0 or t >= VOCAB_SIZE for t in token_ids):
        return False, "token id out of vocab range"
    if len(tokens.get("attention_mask", [])) != len(token_ids):
        return False, "attention_mask length mismatch"
    return True, None
