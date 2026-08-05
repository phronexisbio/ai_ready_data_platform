"""Unit tests for engine/pipelines/molecule_pipeline — BUILD_PLAN.md §11 Phase 11.

No network, no cluster: RDKit is a local deterministic library, so these run
anywhere `pip install -r engine/requirements.txt` has been done.
"""

from rdkit import Chem

from engine.pipelines import molecule_pipeline as mp
from engine.pipelines.molecule_pipeline.canonical import MoleculeParseError

ASPIRIN_SMILES = "CC(=O)OC1=CC=CC=C1C(=O)O"


def test_smiles_and_sdf_produce_identical_canonical_form():
    """The Phase 3 done-when criterion, codified: SMILES and SDF of the same
    molecule must canonicalize to the same form, since both funnel through
    RDKit's canonicalization in canonical.py."""
    mol = Chem.MolFromSmiles(ASPIRIN_SMILES)
    sdf_block = Chem.MolToMolBlock(mol) + "$$$$\n"

    from_smiles = mp.run(ASPIRIN_SMILES.encode(), "aspirin.smi", representations=["graph"])
    from_sdf = mp.run(sdf_block.encode(), "aspirin.sdf", representations=["graph"])

    assert from_smiles[0]["canonical_form"] == from_sdf[0]["canonical_form"]
    assert from_smiles[0]["tensor"]["num_nodes"] == from_sdf[0]["tensor"]["num_nodes"]


def test_smiles_adapter_parses_name():
    records = mp.adapter_for("x.smi").parse(b"CCO ethanol\n")
    assert records[0].name == "ethanol"
    assert records[0].canonical_smiles == Chem.MolToSmiles(Chem.MolFromSmiles("CCO"))


def test_smiles_adapter_rejects_garbage():
    try:
        mp.adapter_for("x.smi").parse(b"not!!!a$$$smiles\n")
        assert False, "expected MoleculeParseError"
    except MoleculeParseError:
        pass


def test_inchi_adapter():
    mol = Chem.MolFromSmiles("CCO")
    inchi = Chem.MolToInchi(mol)
    records = mp.adapter_for("x.inchi").parse(inchi.encode() + b"\n")
    assert records[0].canonical_smiles == Chem.MolToSmiles(mol)


def test_default_representation_is_graph_only():
    results = mp.run(b"CCO ethanol\n", "x.smi")
    types = {r["representation_type"] for r in results}
    assert types == {"molecule_graph"}


def test_requesting_tokens_adds_that_representation():
    results = mp.run(b"CCO ethanol\n", "x.smi", representations=["graph", "tokens"])
    types = {r["representation_type"] for r in results}
    assert types == {"molecule_graph", "molecule_tokens"}


def test_graph_featurization_shape():
    mol = Chem.MolFromSmiles(ASPIRIN_SMILES)
    results = mp.run(ASPIRIN_SMILES.encode(), "x.smi", representations=["graph"])
    graph = results[0]["tensor"]
    assert graph["num_nodes"] == mol.GetNumAtoms()
    assert len(graph["node_features"]) == mol.GetNumAtoms()
    assert len(graph["edge_index"]) == 2 * mol.GetNumBonds()  # both directions


def test_token_featurization_has_bos_eos_and_correct_padding():
    results = mp.run(b"CCO ethanol\n", "x.smi", representations=["tokens"])
    tokens = results[0]["tensor"]
    assert tokens["token_ids"][0] == mp.featurize.BOS
    unpadded = sum(tokens["attention_mask"])
    assert tokens["token_ids"][unpadded - 1] == mp.featurize.EOS
    assert len(tokens["token_ids"]) == len(tokens["attention_mask"])
