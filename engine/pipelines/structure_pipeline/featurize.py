"""canonical StructureRecord -> representations — BUILD_PLAN.md §4b/§11.

Two representations: a residue graph (sequential + spatial CA-CA edges) for
ProteinMPNN-style GNNs, and an SE(3) frame tensor (rotation + translation per
residue, built from the N/CA/C backbone via Gram-Schmidt — the same
rigid-from-3-points construction AlphaFold/RFdiffusion-class models use) for
structure diffusion/generation models.
"""

import numpy as np

_AA_VOCAB = [
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
]

CA_DISTANCE_CUTOFF = 8.0  # Å — standard spatial-edge cutoff for residue graphs


def _rigid_from_3_points(n, ca, c):
    n, ca, c = np.array(n), np.array(ca), np.array(c)
    v1 = c - ca
    v2 = n - ca
    e1 = v1 / np.linalg.norm(v1)
    u2 = v2 - e1 * np.dot(e1, v2)
    e2 = u2 / np.linalg.norm(u2)
    e3 = np.cross(e1, e2)
    rotation = np.stack([e1, e2, e3], axis=1)  # columns are the frame's basis vectors
    return rotation, ca


def to_graph(record) -> dict:
    residues = [r for r in record.residues if r.ca is not None]
    node_features = [[1.0 if r.name == aa else 0.0 for aa in _AA_VOCAB] for r in residues]

    coords = np.array([r.ca for r in residues]) if residues else np.zeros((0, 3))
    edge_index = []
    for i in range(len(residues)):
        if i + 1 < len(residues):
            edge_index += [[i, i + 1], [i + 1, i]]  # sequential backbone connectivity
        for j in range(i + 2, len(residues)):
            if np.linalg.norm(coords[i] - coords[j]) <= CA_DISTANCE_CUTOFF:
                edge_index += [[i, j], [j, i]]

    return {
        "representation_type": "structure_graph",
        "num_nodes": len(residues),
        "node_features": node_features,
        "edge_index": edge_index,
    }


def to_se3_frames(record) -> dict:
    usable = [r for r in record.residues if r.has_full_backbone]
    rotations, translations = [], []
    for r in usable:
        rotation, translation = _rigid_from_3_points(r.n, r.ca, r.c)
        rotations.append(rotation.tolist())
        translations.append(translation.tolist())

    return {
        "representation_type": "structure_frames",
        "num_residues": len(usable),
        "source_residue_count": len(record.residues),
        "rotations": rotations,
        "translations": translations,
    }
