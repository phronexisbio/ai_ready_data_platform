"""Unit tests for engine/validators/output/* — BUILD_PLAN.md §6 output gate,
Phase 11. Each catches a tensor that "ran without erroring" but is still bad
— the whole point of having an output gate separate from the input gate.
"""

from rdkit import Chem

from engine.validators.output import image as image_output
from engine.validators.output import molecule as molecule_output
from engine.validators.output import sequence as sequence_output
from engine.validators.output import structure as structure_output
from engine.validators.output import tabular as tabular_output

ASPIRIN_CANONICAL = Chem.MolToSmiles(Chem.MolFromSmiles("CC(=O)OC1=CC=CC=C1C(=O)O"))


def _good_aspirin_graph() -> dict:
    from engine.pipelines import molecule_pipeline as mp

    return mp.run(ASPIRIN_CANONICAL.encode(), "x.smi", representations=["graph"])[0]["tensor"]


def test_molecule_graph_good_and_zero_node_corruption():
    ok, _ = molecule_output.validate_graph(ASPIRIN_CANONICAL, _good_aspirin_graph())
    assert ok

    corrupted = {"num_nodes": 0, "node_features": [], "edge_index": []}
    ok, reason = molecule_output.validate_graph(ASPIRIN_CANONICAL, corrupted)
    assert not ok and "zero nodes" in reason


def test_molecule_graph_disconnected_corruption():
    good = _good_aspirin_graph()
    corrupted = dict(good)
    corrupted["edge_index"] = []  # strip all edges from a multi-atom molecule
    ok, reason = molecule_output.validate_graph(ASPIRIN_CANONICAL, corrupted)
    assert not ok and "disconnected" in reason


def test_molecule_tokens_out_of_vocab_corruption():
    from engine.pipelines.molecule_pipeline.featurize import VOCAB_SIZE

    bad = {"token_ids": [0, VOCAB_SIZE + 100], "attention_mask": [1, 1]}
    ok, reason = molecule_output.validate_tokens(bad)
    assert not ok and "out of vocab range" in reason


def test_sequence_tokens_good_and_length_mismatch_corruption():
    from engine.pipelines.sequence_pipeline.canonical import SequenceRecord
    from engine.pipelines.sequence_pipeline.featurize import to_tokens

    record = SequenceRecord(name="x", sequence="ACDE", alphabet="protein")
    tokens = to_tokens(record)
    ok, _ = sequence_output.validate_tokens(tokens)
    assert ok

    corrupted = dict(tokens)
    corrupted["source_length"] = 999  # claims a source length the tokens don't match
    ok, reason = sequence_output.validate_tokens(corrupted)
    assert not ok and "does not match source length" in reason


def test_structure_frames_nan_coordinate_corruption():
    good = {
        "num_residues": 2,
        "source_residue_count": 2,
        "translations": [[0.0, 0.0, 0.0], [3.8, 0.0, 0.0]],
        "rotations": [[[1, 0, 0], [0, 1, 0], [0, 0, 1]]] * 2,
    }
    ok, _ = structure_output.validate_frames(good)
    assert ok

    corrupted = dict(good)
    corrupted["translations"] = [[0.0, 0.0, 0.0], [float("nan"), 0.0, 0.0]]
    ok, reason = structure_output.validate_frames(corrupted)
    assert not ok and "NaN/Inf" in reason


def test_structure_frames_residue_count_mismatch_corruption():
    bad = {
        "num_residues": 1,
        "source_residue_count": 3,  # 2 residues silently dropped
        "translations": [[0.0, 0.0, 0.0]],
        "rotations": [[[1, 0, 0], [0, 1, 0], [0, 0, 1]]],
    }
    ok, reason = structure_output.validate_frames(bad)
    assert not ok and "residue count mismatch" in reason


def test_structure_frames_implausible_bond_length_corruption():
    bad = {
        "num_residues": 2,
        "source_residue_count": 2,
        "translations": [[0.0, 0.0, 0.0], [50.0, 0.0, 0.0]],  # 50 Å apart — not a real peptide bond
        "rotations": [[[1, 0, 0], [0, 1, 0], [0, 0, 1]]] * 2,
    }
    ok, reason = structure_output.validate_frames(bad)
    assert not ok and "implausible CA-CA distance" in reason


def test_image_tensor_all_saturated_corruption():
    saturated = {"shape": [1, 2, 2], "data": [[[1.0, 1.0], [1.0, 1.0]]]}
    ok, reason = image_output.validate_image_tensor(saturated)
    assert not ok and "all-saturated" in reason


def test_image_tensor_all_zero_corruption():
    zeroed = {"shape": [1, 2, 2], "data": [[[0.0, 0.0], [0.0, 0.0]]]}
    ok, reason = image_output.validate_image_tensor(zeroed)
    assert not ok and "all-zero" in reason


def test_image_tensor_shape_mismatch_corruption():
    truncated = {"shape": [1, 4, 4], "data": [[[0.5, 0.5], [0.5, 0.5]]]}  # says 4x4, only has 2x2
    ok, reason = image_output.validate_image_tensor(truncated)
    assert not ok and "corrupted/truncated" in reason


def test_tabular_tokens_good_and_column_count_mismatch_corruption():
    from engine.pipelines.tabular_pipeline.canonical import TabularRecord
    from engine.pipelines.tabular_pipeline.featurize import to_tokens

    record = TabularRecord(name="row_1", columns=["a", "b"], values=["1", "2"])
    tokens = to_tokens(record)
    ok, _ = tabular_output.validate_tokens(tokens)
    assert ok

    corrupted = dict(tokens)
    corrupted["num_columns"] = 999  # claims a column count the tokens don't match
    ok, reason = tabular_output.validate_tokens(corrupted)
    assert not ok and "does not match column count" in reason


def test_tabular_tokens_out_of_vocab_corruption():
    from engine.pipelines.tabular_pipeline.featurize import VOCAB_SIZE

    bad = {"token_ids": [0, VOCAB_SIZE + 100], "attention_mask": [1, 1], "num_columns": 0}
    ok, reason = tabular_output.validate_tokens(bad)
    assert not ok and "out of vocab range" in reason
