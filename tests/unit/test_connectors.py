"""Unit tests for connectors — BUILD_PLAN.md §3, Phase 11.

Tests discover()/fetch()/validate() in isolation (no MinIO/Postgres/NATS
needed — HTTP calls are mocked). The full land -> register -> emit flow via
Connector.run() needs the live cluster and is covered by
tests/integration/test_end_to_end.py instead, not duplicated here.
"""

from unittest.mock import MagicMock, patch

import pytest

from connectors.chembl_connector import ChEMBLConnector
from connectors.local_connector import LocalConnector, guess_modality
from connectors.pubchem_connector import PubChemConnector
from connectors.uniprot_connector import UniProtConnector

SAMPLE_DATA_DIR = "tests/sample_data/local_batch"


def _mock_response(json_data=None, content=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    if json_data is not None:
        resp.json.return_value = json_data
    if content is not None:
        resp.content = content
    return resp


def test_guess_modality_by_extension():
    from pathlib import Path

    assert guess_modality(Path("x.fasta")) == "sequence"
    assert guess_modality(Path("x.pdb")) == "structure"
    assert guess_modality(Path("x.smi")) == "molecule"
    assert guess_modality(Path("x.tiff")) == "image"
    assert guess_modality(Path("x.csv")) == "tabular"
    assert guess_modality(Path("x.unknown_ext")) == "unknown"


def test_local_connector_discovers_and_fetches_real_sample_files():
    conn = LocalConnector(batch_dir=SAMPLE_DATA_DIR)
    items = conn.discover()
    names = {i.name for i in items}
    assert "sample_sequences.fasta" in names
    assert "sample_molecules.smi" in names

    fasta_item = next(i for i in items if i.name == "sample_sequences.fasta")
    fetched = conn.fetch(fasta_item)
    assert fetched.content.startswith(b">")


def test_local_connector_missing_dir_raises():
    conn = LocalConnector(batch_dir="tests/sample_data/does-not-exist")
    with pytest.raises(FileNotFoundError):
        conn.discover()


def test_uniprot_connector_discover_builds_correct_uris():
    conn = UniProtConnector(accessions=["P69905", "P68871"])
    items = conn.discover()
    assert [i.name for i in items] == ["P69905.fasta", "P68871.fasta"]
    assert items[0].uri == "https://rest.uniprot.org/uniprotkb/P69905.fasta"


def test_uniprot_connector_fetch_and_validate():
    conn = UniProtConnector(accessions=["P69905"])
    item = conn.discover()[0]
    with patch("connectors.uniprot_connector.requests.get") as mock_get:
        mock_get.return_value = _mock_response(content=b">sp|P69905|HBA_HUMAN\nMVLSPADK\n")
        fetched = conn.fetch(item)
    assert conn.validate(fetched)
    assert not conn.validate(type(fetched)(name="x", content=b"not fasta", modality="sequence"))


def test_chembl_connector_extracts_canonical_smiles():
    conn = ChEMBLConnector(chembl_ids=["CHEMBL25"])
    item = conn.discover()[0]
    assert item.uri == "https://www.ebi.ac.uk/chembl/api/data/molecule/CHEMBL25.json"
    with patch("connectors.chembl_connector.requests.get") as mock_get:
        mock_get.return_value = _mock_response(
            json_data={"molecule_structures": {"canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O"}}
        )
        fetched = conn.fetch(item)
    assert b"CC(=O)Oc1ccccc1C(=O)O" in fetched.content
    assert b"CHEMBL25" in fetched.content


def test_pubchem_connector_handles_canonicalsmiles_key():
    conn = PubChemConnector(cids=["2244"])
    item = conn.discover()[0]
    with patch("connectors.pubchem_connector.requests.get") as mock_get:
        mock_get.return_value = _mock_response(
            json_data={"PropertyTable": {"Properties": [{"CID": 2244, "CanonicalSMILES": "CC(=O)OC1=CC=CC=C1C(=O)O"}]}}
        )
        fetched = conn.fetch(item)
    assert b"CC(=O)OC1=CC=CC=C1C(=O)O" in fetched.content


def test_pubchem_connector_handles_connectivitysmiles_key():
    """A real API quirk discovered in Phase 7 — PubChem sometimes returns
    ConnectivitySMILES even when CanonicalSMILES was requested. The connector
    must not assume only one key name is possible."""
    conn = PubChemConnector(cids=["2244"])
    item = conn.discover()[0]
    with patch("connectors.pubchem_connector.requests.get") as mock_get:
        mock_get.return_value = _mock_response(
            json_data={"PropertyTable": {"Properties": [{"CID": 2244, "ConnectivitySMILES": "CC(=O)OC1=CC=CC=C1C(=O)O"}]}}
        )
        fetched = conn.fetch(item)
    assert b"CC(=O)OC1=CC=CC=C1C(=O)O" in fetched.content


def test_pubchem_connector_raises_on_missing_smiles_key():
    conn = PubChemConnector(cids=["2244"])
    item = conn.discover()[0]
    with patch("connectors.pubchem_connector.requests.get") as mock_get:
        mock_get.return_value = _mock_response(json_data={"PropertyTable": {"Properties": [{"CID": 2244}]}})
        with pytest.raises(ValueError):
            conn.fetch(item)
