"""Unit tests for engine/pipelines/tabular_pipeline — BUILD_PLAN.md §11."""

from engine.pipelines import tabular_pipeline as tp

CSV = b"compound_id,assay,value\nCID001,IC50,12.5\nCID002,IC50,7.1\n"
TSV = b"compound_id\tassay\tvalue\nCID001\tIC50\t12.5\n"


def test_csv_adapter_one_record_per_row():
    records = tp.adapter_for("x.csv").parse(CSV)
    assert [r.name for r in records] == ["row_1", "row_2"]
    assert records[0].columns == ["compound_id", "assay", "value"]
    assert records[0].values == ["CID001", "IC50", "12.5"]
    assert records[1].values == ["CID002", "IC50", "7.1"]


def test_tsv_adapter():
    records = tp.adapter_for("x.tsv").parse(TSV)
    assert records[0].values == ["CID001", "IC50", "12.5"]


def test_canonical_form_is_deterministic():
    records = tp.adapter_for("x.csv").parse(CSV)
    assert tp.canonical_form_of(records[0]) == "compound_id=CID001\tassay=IC50\tvalue=12.5"


def test_default_representation_is_tokens():
    results = tp.run(CSV, "x.csv")
    assert {r["representation_type"] for r in results} == {"tabular_tokens"}
    assert len(results) == 2  # one per data row


def test_same_cell_hashes_to_same_token_id():
    records = tp.adapter_for("x.csv").parse(CSV)
    tokens_a = tp.FEATURIZERS["tokens"](records[0])
    tokens_b = tp.FEATURIZERS["tokens"](records[0])
    assert tokens_a["token_ids"] == tokens_b["token_ids"]


def test_token_count_matches_column_count_plus_bos_eos():
    records = tp.adapter_for("x.csv").parse(CSV)
    tokens = tp.FEATURIZERS["tokens"](records[0])
    assert tokens["num_columns"] == 3
    assert sum(tokens["attention_mask"]) == 3 + 2  # BOS + 3 cells + EOS
