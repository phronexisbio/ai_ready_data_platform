"""molecule_pipeline — format adapters -> canonical MoleculeRecord -> requested
representations. BUILD_PLAN.md §4a/§4b, Phase 3.
"""

from pathlib import Path

from engine.pipelines.molecule_pipeline import featurize
from engine.pipelines.molecule_pipeline.adapters import inchi, mol2, sdf, smiles

PIPELINE_NAME = "molecule_pipeline"
PIPELINE_VERSION = "0.1.0"

DEFAULT_REPRESENTATIONS = ["graph"]

_ADAPTERS_BY_SUFFIX = {
    ".smi": smiles,
    ".txt": smiles,
    ".sdf": sdf,
    ".inchi": inchi,
    ".mol2": mol2,
}

# short job-parameter key -> full representation_type name. Known without
# calling the (potentially expensive) featurizer — needed to build a cache
# key (BUILD_PLAN §10 Phase 5) before deciding whether to run it at all.
REPRESENTATION_TYPES = {
    "graph": "molecule_graph",
    "tokens": "molecule_tokens",
}

FEATURIZERS = {
    "graph": featurize.to_graph,
    "tokens": featurize.to_tokens,
}


def adapter_for(filename: str):
    suffix = Path(filename).suffix.lower()
    adapter = _ADAPTERS_BY_SUFFIX.get(suffix)
    if adapter is None:
        raise ValueError(f"no molecule_pipeline adapter for extension '{suffix}'")
    return adapter


def canonical_form_of(record) -> str:
    """A molecule's canonical SMILES — also the basis of its content hash."""
    return record.canonical_smiles


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
                raise ValueError(f"unknown representation '{rep}' for molecule_pipeline")
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
