"""Unit tests for engine/pipelines/sequence_pipeline — BUILD_PLAN.md §11 Phase 11."""

from engine.pipelines import sequence_pipeline as sp
from engine.pipelines.sequence_pipeline.canonical import guess_alphabet

FASTA = b">test_seq\nMVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSHGSAQVKGHGKKVADALTNA\n"


def test_guess_alphabet_protein_vs_nucleotide():
    assert guess_alphabet("MVLSPADK") == "protein"
    assert guess_alphabet("ACGTACGT") == "nucleotide"


def test_fasta_adapter_parses_multi_record():
    records = sp.adapter_for("x.fasta").parse(b">a\nACDE\n>b\nFGHI\n")
    assert [r.name for r in records] == ["a", "b"]
    assert records[0].sequence == "ACDE"


def test_uniprot_json_adapter():
    import json

    payload = json.dumps({"primaryAccession": "P12345", "sequence": {"value": "mvlsp"}}).encode()
    records = sp.adapter_for("x.json").parse(payload)
    assert records[0].name == "P12345"
    assert records[0].sequence == "MVLSP"  # uppercased


def test_uniprot_xml_adapter():
    xml = b"""<?xml version="1.0"?>
    <uniprot xmlns="http://uniprot.org/uniprot">
      <entry>
        <accession>P69905</accession>
        <sequence>MVLSPADK</sequence>
      </entry>
    </uniprot>"""
    records = sp.adapter_for("x.xml").parse(xml)
    assert records[0].name == "P69905"
    assert records[0].sequence == "MVLSPADK"


def test_default_representation_is_tokens():
    results = sp.run(FASTA, "x.fasta")
    assert {r["representation_type"] for r in results} == {"sequence_tokens"}


def test_token_featurization_length_matches_source_plus_bos_eos():
    results = sp.run(FASTA, "x.fasta", representations=["tokens"])
    tokens = results[0]["tensor"]
    record_len = len(results[0]["canonical_form"])
    assert tokens["source_length"] == record_len
    unpadded = sum(tokens["attention_mask"])
    assert unpadded == record_len + 2  # BOS + EOS


def test_msa_is_still_a_stub():
    """MSA generation is explicitly stubbed per BUILD_PLAN §10 Phase 3 — this
    test exists so a real implementation lands intentionally, not by accident
    leaving the stub silently in place."""
    from engine.pipelines.sequence_pipeline.canonical import SequenceRecord
    from engine.pipelines.sequence_pipeline.featurize import to_msa

    record = SequenceRecord(name="x", sequence="ACDE", alphabet="protein")
    assert to_msa(record) is None
