"""Unit tests for engine/pipelines/structure_pipeline — BUILD_PLAN.md §11 Phase 11."""

from engine.pipelines import structure_pipeline as sp

# 3 residues with full backbone + one water — mirrors tests/sample_data/structure_batch/good_structure.pdb
PDB_WITH_WATER = b"""ATOM      1  N   ALA A   1      11.104   6.134  -6.504  1.00 20.00           N
ATOM      2  CA  ALA A   1      11.639   6.071  -5.147  1.00 20.00           C
ATOM      3  C   ALA A   1      13.147   6.297  -5.129  1.00 20.00           C
ATOM      4  O   ALA A   1      13.732   6.412  -4.043  1.00 20.00           O
ATOM      5  N   GLY A   2      13.751   6.366  -6.298  1.00 20.00           N
ATOM      6  CA  GLY A   2      15.196   6.590  -6.400  1.00 20.00           C
ATOM      7  C   GLY A   2      15.900   5.375  -5.813  1.00 20.00           C
ATOM      8  O   GLY A   2      17.104   5.318  -5.678  1.00 20.00           O
ATOM      9  N   SER A   3      15.317   4.334  -5.328  1.00 20.00           N
ATOM     10  CA  SER A   3      15.876   3.106  -4.716  1.00 20.00           C
ATOM     11  C   SER A   3      17.386   3.212  -4.573  1.00 20.00           C
ATOM     12  O   SER A   3      18.023   2.412  -3.884  1.00 20.00           O
HETATM   13  O   HOH A   4      25.000   6.000  -5.000  1.00 20.00           O
END
"""

# GLY missing its N atom — an incomplete backbone
PDB_INCOMPLETE = b"""ATOM      1  N   ALA A   1      11.104   6.134  -6.504  1.00 20.00           N
ATOM      2  CA  ALA A   1      11.639   6.071  -5.147  1.00 20.00           C
ATOM      3  C   ALA A   1      13.147   6.297  -5.129  1.00 20.00           C
ATOM      4  O   ALA A   1      13.732   6.412  -4.043  1.00 20.00           O
ATOM      5  CA  GLY A   2      15.196   6.590  -6.400  1.00 20.00           C
ATOM      6  C   GLY A   2      15.900   5.375  -5.813  1.00 20.00           C
END
"""


def test_water_and_heteroatoms_are_stripped():
    records = sp.adapter_for("x.pdb").parse(PDB_WITH_WATER)
    assert len(records[0].residues) == 3  # HOH excluded
    assert records[0].one_letter_sequence == "AGS"


def test_se3_frames_default_representation():
    results = sp.run(PDB_WITH_WATER, "x.pdb")
    assert {r["representation_type"] for r in results} == {"structure_frames"}
    frames = results[0]["tensor"]
    assert frames["num_residues"] == 3
    assert frames["source_residue_count"] == 3
    assert len(frames["translations"]) == 3
    assert len(frames["rotations"]) == 3
    assert len(frames["rotations"][0]) == 3 and len(frames["rotations"][0][0]) == 3  # 3x3


def test_incomplete_backbone_residue_excluded_from_frames_but_counted_in_source():
    results = sp.run(PDB_INCOMPLETE, "x.pdb", representations=["frames"])
    frames = results[0]["tensor"]
    assert frames["num_residues"] == 1  # only ALA has full N/CA/C
    assert frames["source_residue_count"] == 2  # both ALA and GLY were parsed


def test_graph_representation_sequential_and_spatial_edges():
    results = sp.run(PDB_WITH_WATER, "x.pdb", representations=["graph"])
    graph = results[0]["tensor"]
    assert graph["num_nodes"] == 3
    edge_pairs = {tuple(e) for e in graph["edge_index"]}
    assert (0, 1) in edge_pairs and (1, 0) in edge_pairs  # sequential
    assert (1, 2) in edge_pairs and (2, 1) in edge_pairs


def test_mmcif_and_pdb_share_extraction_logic():
    """adapters/mmcif.py reuses adapters/pdb.py's _extract — this just checks
    the mmCIF path produces the same shape of result a PDB parse would."""
    mmcif = b"""data_TEST
#
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
ATOM 1 N N . ALA A 1 1 ? 11.104 6.134 -6.504 1.00 20.00 1 ALA A N 1
ATOM 2 C CA . ALA A 1 1 ? 11.639 6.071 -5.147 1.00 20.00 1 ALA A CA 1
ATOM 3 C C . ALA A 1 1 ? 13.147 6.297 -5.129 1.00 20.00 1 ALA A C 1
ATOM 4 O O . ALA A 1 1 ? 13.732 6.412 -4.043 1.00 20.00 1 ALA A O 1
#
"""
    records = sp.adapter_for("x.cif").parse(mmcif)
    assert records[0].one_letter_sequence == "A"
