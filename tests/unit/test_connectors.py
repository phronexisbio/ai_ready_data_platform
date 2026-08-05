"""Unit tests for connectors — BUILD_PLAN.md §3, Phase 11.

Tests discover()/fetch()/validate() in isolation (no MinIO/Postgres/NATS
needed — HTTP calls are mocked). The full land -> register -> emit flow via
Connector.run() needs the live cluster and is covered by
tests/integration/test_end_to_end.py instead, not duplicated here.
"""

from unittest.mock import MagicMock, patch

import pytest

from connectors.chembl_connector import ChEMBLConnector
from connectors.ftp_connector import FTPConnector
from connectors.geo_connector import GEOConnector
from connectors.local_connector import LocalConnector, guess_modality
from connectors.pubchem_connector import PubChemConnector
from connectors.s3_connector import S3Connector
from connectors.sra_connector import SRAConnector
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


GEO_ACC_CGI_SAMPLE = (
    b"^SERIES = GSE2553\n"
    b"!Series_title = NHGRI_Sarcoma_Baird\n"
    b"!Series_geo_accession = GSE2553\n"
    b"!Series_summary = first summary line\n"
    b"!Series_summary = second summary line\n"
)


def test_geo_connector_parses_acc_cgi_text_into_csv():
    import csv
    import io

    conn = GEOConnector(accessions=["GSE2553"])
    item = conn.discover()[0]
    assert item.uri == "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE2553&targ=self&form=text&view=brief"
    assert item.modality == "tabular"

    with patch("connectors.geo_connector.requests.get") as mock_get:
        mock_get.return_value = _mock_response(content=GEO_ACC_CGI_SAMPLE)
        fetched = conn.fetch(item)

    rows = list(csv.reader(io.StringIO(fetched.content.decode("utf-8"))))
    assert len(rows) == 2  # header + one data row, as engine/validators/input/tabular.py requires
    header, row = rows
    assert "Series_geo_accession" in header
    assert row[header.index("Series_geo_accession")] == "GSE2553"
    # repeated keys (Series_summary appears twice) are joined, not dropped
    assert "first summary line; second summary line" in row[header.index("Series_summary")]
    assert conn.validate(fetched)


def test_geo_connector_raises_on_unparseable_response():
    conn = GEOConnector(accessions=["GSE2553"])
    item = conn.discover()[0]
    with patch("connectors.geo_connector.requests.get") as mock_get:
        mock_get.return_value = _mock_response(content=b"no key-value pairs here")
        with pytest.raises(ValueError):
            conn.fetch(item)


def test_sra_connector_resolves_accession_then_fetches_summary():
    import csv
    import io

    conn = SRAConnector(accessions=["SRR000001"])
    item = conn.discover()[0]
    assert item.metadata["accession"] == "SRR000001"

    search_resp = _mock_response(json_data={"esearchresult": {"idlist": ["8"]}})
    summary_resp = _mock_response(
        json_data={"result": {"8": {"uid": "8", "createdate": "2008/04/04", "updatedate": "2015/04/07"}}}
    )
    with patch("connectors.sra_connector.requests.get", side_effect=[search_resp, summary_resp]) as mock_get:
        fetched = conn.fetch(item)

    assert mock_get.call_count == 2
    rows = list(csv.reader(io.StringIO(fetched.content.decode("utf-8"))))
    header, row = rows
    assert row[header.index("accession")] == "SRR000001"
    assert row[header.index("createdate")] == "2008/04/04"
    assert conn.validate(fetched)


def test_sra_connector_raises_on_empty_search_result():
    conn = SRAConnector(accessions=["SRR000001"])
    item = conn.discover()[0]
    search_resp = _mock_response(json_data={"esearchresult": {"idlist": []}})
    with patch("connectors.sra_connector.requests.get", return_value=search_resp):
        with pytest.raises(ValueError):
            conn.fetch(item)


def test_s3_connector_discover_builds_correct_uris_and_modality():
    with patch("connectors.s3_connector.boto3.client"):
        conn = S3Connector(bucket="1000genomes", keys=["README.ebi_aspera_info", "data/x.fasta"])
    items = conn.discover()
    assert items[0].name == "README.ebi_aspera_info"
    assert items[0].uri == "s3://1000genomes/README.ebi_aspera_info"
    assert items[0].modality == "unknown"  # no recognized extension
    assert items[1].modality == "sequence"  # .fasta


def test_s3_connector_accepts_explicit_modality_override_for_extensionless_keys():
    with patch("connectors.s3_connector.boto3.client"):
        conn = S3Connector(bucket="1000genomes", keys=[("README.ebi_aspera_info", "text")])
    item = conn.discover()[0]
    assert item.modality == "text"  # would otherwise guess "unknown" (no extension)


def test_s3_connector_fetches_object_bytes():
    import io

    mock_client = MagicMock()
    mock_client.get_object.return_value = {"Body": io.BytesIO(b"hello from s3")}
    with patch("connectors.s3_connector.boto3.client", return_value=mock_client):
        conn = S3Connector(bucket="1000genomes", keys=["README.ebi_aspera_info"])
    item = conn.discover()[0]
    fetched = conn.fetch(item)
    assert fetched.content == b"hello from s3"
    mock_client.get_object.assert_called_once_with(Bucket="1000genomes", Key="README.ebi_aspera_info")


def test_ftp_connector_discover_builds_correct_uris_and_modality():
    conn = FTPConnector(host="ftp.ncbi.nlm.nih.gov", paths=["/pub/taxonomy/taxdump_readme.txt"])
    items = conn.discover()
    assert items[0].name == "taxdump_readme.txt"
    assert items[0].uri == "ftp://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump_readme.txt"
    assert items[0].modality == "text"


def test_ftp_connector_fetches_file_bytes_and_uses_anonymous_login_by_default():
    mock_ftp_instance = MagicMock()

    def fake_retrbinary(cmd, write_callback):
        write_callback(b"hello from ftp")

    mock_ftp_instance.retrbinary.side_effect = fake_retrbinary

    with patch("connectors.ftp_connector.FTP", return_value=mock_ftp_instance) as mock_ftp_cls:
        conn = FTPConnector(host="ftp.ncbi.nlm.nih.gov", paths=["/pub/taxonomy/taxdump_readme.txt"])
        item = conn.discover()[0]
        fetched = conn.fetch(item)

    mock_ftp_cls.assert_called_once_with("ftp.ncbi.nlm.nih.gov", timeout=30)
    mock_ftp_instance.login.assert_called_once_with(user="anonymous", passwd="anonymous")
    mock_ftp_instance.quit.assert_called_once()
    assert fetched.content == b"hello from ftp"
