"""sequence_pipeline — format adapters -> canonical SequenceRecord -> requested
representations. BUILD_PLAN.md §4a/§4b, Phase 3.
"""

from pathlib import Path

from engine.pipelines.sequence_pipeline import featurize
from engine.pipelines.sequence_pipeline.adapters import fasta, genbank, uniprot_json, uniprot_xml

PIPELINE_NAME = "sequence_pipeline"
PIPELINE_VERSION = "0.1.0"

DEFAULT_REPRESENTATIONS = ["tokens"]

_ADAPTERS_BY_SUFFIX = {
    ".fasta": fasta,
    ".fa": fasta,
    ".fna": fasta,
    ".xml": uniprot_xml,
    ".json": uniprot_json,
    ".gb": genbank,
    ".gbk": genbank,
    ".genbank": genbank,
}

# short job-parameter key -> full representation_type name. Known without
# calling the (potentially expensive) featurizer — needed to build a cache
# key (BUILD_PLAN §10 Phase 5) before deciding whether to run it at all.
REPRESENTATION_TYPES = {
    "tokens": "sequence_tokens",
}

FEATURIZERS = {
    "tokens": featurize.to_tokens,
}


def adapter_for(filename: str):
    suffix = Path(filename).suffix.lower()
    adapter = _ADAPTERS_BY_SUFFIX.get(suffix)
    if adapter is None:
        raise ValueError(f"no sequence_pipeline adapter for extension '{suffix}'")
    return adapter


def canonical_form_of(record) -> str:
    """A sequence's residue string — also the basis of its content hash."""
    return record.sequence


def run(content: bytes, filename: str, representations: list[str] | None = None) -> list[dict]:
    """Parse `content` and return one result dict per (record, representation).

    Each result: {record_name, canonical_form, representation_type, tensor}.
    Always computes — does not consult the content-addressed cache; that's
    engine/steps/transform.py's job, since it's the one with catalog access.
    """
    representations = representations or DEFAULT_REPRESENTATIONS
    adapter = adapter_for(filename)
    records = adapter.parse(content)

    results = []
    for record in records:
        for rep in representations:
            featurizer = FEATURIZERS.get(rep)
            if featurizer is None:
                raise ValueError(f"unknown representation '{rep}' for sequence_pipeline")
            tensor = featurizer(record)
            results.append(
                {
                    "record_name": record.name,
                    "canonical_form": canonical_form_of(record),
                    "representation_type": tensor["representation_type"],
                    "tensor": tensor,
                }
            )
    return results
