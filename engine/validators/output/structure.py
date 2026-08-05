"""Output validation for structure representations — BUILD_PLAN.md §6: no
NaN/Inf coordinates, bond lengths/angles within physically plausible ranges,
residue count matches source structure.
"""

import math

MIN_CA_DISTANCE = 2.5  # Å
MAX_CA_DISTANCE = 4.5  # Å — generous bounds around the ~3.8 Å typical peptide span


def _finite(vec) -> bool:
    return all(math.isfinite(v) for v in vec)


def validate_frames(frames: dict) -> tuple[bool, str | None]:
    translations = frames.get("translations", [])
    rotations = frames.get("rotations", [])

    if not translations:
        return False, "no residues with a complete backbone (N/CA/C) to build frames from"

    num_residues = frames.get("num_residues")
    source_count = frames.get("source_residue_count")
    if num_residues != source_count:
        return False, (
            f"residue count mismatch: {num_residues} frames built "
            f"from {source_count} source residues (likely missing backbone atoms)"
        )

    for t in translations:
        if not _finite(t):
            return False, "NaN/Inf coordinate in a residue translation"
    for rot in rotations:
        for row in rot:
            if not _finite(row):
                return False, "NaN/Inf value in a residue rotation matrix"

    for i in range(len(translations) - 1):
        dx = [a - b for a, b in zip(translations[i], translations[i + 1])]
        dist = math.sqrt(sum(v * v for v in dx))
        if not (MIN_CA_DISTANCE <= dist <= MAX_CA_DISTANCE):
            return False, f"implausible CA-CA distance between residues {i} and {i + 1}: {dist:.2f} Å"

    return True, None


def validate_graph(graph: dict) -> tuple[bool, str | None]:
    num_nodes = graph.get("num_nodes", 0)
    if num_nodes == 0:
        return False, "graph has zero nodes"

    node_features = graph.get("node_features", [])
    if len(node_features) != num_nodes:
        return False, f"node_features length {len(node_features)} != num_nodes {num_nodes}"

    edge_index = graph.get("edge_index", [])
    referenced = {idx for pair in edge_index for idx in pair}
    if num_nodes > 1 and not referenced:
        return False, "no edges (disconnected residue graph)"
    if any(idx < 0 or idx >= num_nodes for idx in referenced):
        return False, "edge_index references an out-of-range residue"

    return True, None
