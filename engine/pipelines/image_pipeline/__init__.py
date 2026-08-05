"""image_pipeline — format adapters -> canonical ImageRecord -> requested
representations. BUILD_PLAN.md §4a/§4b, Phase 4.
"""

from pathlib import Path

from engine.pipelines.image_pipeline import featurize
from engine.pipelines.image_pipeline.adapters import tiff

PIPELINE_NAME = "image_pipeline"
PIPELINE_VERSION = "0.1.0"

DEFAULT_REPRESENTATIONS = ["tensor"]

_ADAPTERS_BY_SUFFIX = {
    ".tif": tiff,
    ".tiff": tiff,
}

# short job-parameter key -> full representation_type name. Known without
# calling the (potentially expensive) featurizer — needed to build a cache
# key (BUILD_PLAN §10 Phase 5) before deciding whether to run it at all.
REPRESENTATION_TYPES = {
    "tensor": "image_tensor",
}

FEATURIZERS = {
    "tensor": featurize.to_image_tensor,
}


def adapter_for(filename: str):
    suffix = Path(filename).suffix.lower()
    adapter = _ADAPTERS_BY_SUFFIX.get(suffix)
    if adapter is None:
        raise ValueError(f"no image_pipeline adapter for extension '{suffix}'")
    return adapter


def canonical_form_of(record) -> str:
    """A hash of the raw pixel content — images have no natural canonical
    string the way a molecule has SMILES, but this still serves as the basis
    of a deterministic content hash (hashing it again in transform.py is
    harmless — the composition is still deterministic)."""
    return featurize.content_hash(record)


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
                raise ValueError(f"unknown representation '{rep}' for image_pipeline")
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
