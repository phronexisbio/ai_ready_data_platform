"""structure_pipeline — format adapters -> canonical StructureRecord ->
requested representations. BUILD_PLAN.md §4a/§4b, Phase 4.
"""

from pathlib import Path

from engine.pipelines.structure_pipeline import featurize
from engine.pipelines.structure_pipeline.adapters import mmcif, pdb

PIPELINE_NAME = "structure_pipeline"
PIPELINE_VERSION = "0.1.0"

DEFAULT_REPRESENTATIONS = ["frames"]

_ADAPTERS_BY_SUFFIX = {
    ".pdb": pdb,
    ".cif": mmcif,
    ".mmcif": mmcif,
    ".pdbx": mmcif,
}

# short job-parameter key -> full representation_type name. Known without
# calling the (potentially expensive) featurizer — needed to build a cache
# key (BUILD_PLAN §10 Phase 5) before deciding whether to run it at all.
REPRESENTATION_TYPES = {
    "frames": "structure_frames",
    "graph": "structure_graph",
}

FEATURIZERS = {
    "frames": featurize.to_se3_frames,
    "graph": featurize.to_graph,
}


def adapter_for(filename: str):
    suffix = Path(filename).suffix.lower()
    adapter = _ADAPTERS_BY_SUFFIX.get(suffix)
    if adapter is None:
        raise ValueError(f"no structure_pipeline adapter for extension '{suffix}'")
    return adapter


def canonical_form_of(record) -> str:
    """A structure's one-letter residue sequence — the closest analogue to a
    molecule's canonical SMILES, and the basis of its content hash."""
    return record.one_letter_sequence


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
                raise ValueError(f"unknown representation '{rep}' for structure_pipeline")
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
