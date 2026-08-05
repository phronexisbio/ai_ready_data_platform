"""Ray remote tasks for the heaviest transform steps — BUILD_PLAN.md §10
Phase 8: image featurization and MSA generation (still a stub per Phase 3 —
this phase moves *where* the stub's slot executes, not what it computes).

GPU-readiness without a GPU cluster to test against: each task declares its
GPU requirement via `RAY_TASK_NUM_GPUS` (an env var read at import time, not
hardcoded), defaulting to 0 for this CPU-only RayCluster
(infra/k8s-manifests/raycluster.yaml). Moving to a real GPU cluster means
setting that env var to the per-task GPU share and adding
`nvidia.com/gpu` to the worker group's K8s resources — never touching this
file. Ray genuinely enforces the request either way: with num_gpus=1 and zero
GPU workers, a submitted task queues forever instead of running, which is
exactly the behavior that makes the "GPU-ready" claim real rather than
cosmetic (see engine/steps/ray_batch_transform.py's verification path).
"""

import os
import socket

import ray

from engine.pipelines import image_pipeline
from engine.pipelines.sequence_pipeline import featurize as sequence_featurize
from engine.pipelines.sequence_pipeline.canonical import SequenceRecord

NUM_GPUS_PER_TASK = float(os.environ.get("RAY_TASK_NUM_GPUS", "0"))


def _worker_id() -> str:
    """Which Ray worker actually ran this task — used to prove batch work is
    genuinely spread across the cluster, not just executed serially."""
    return f"{socket.gethostname()}/pid={os.getpid()}"


@ray.remote(num_gpus=NUM_GPUS_PER_TASK)
def featurize_image_remote(content: bytes, filename: str, representations: list[str] | None = None) -> dict:
    results = image_pipeline.run(content, filename, representations)
    return {"worker": _worker_id(), "results": results}


@ray.remote(num_gpus=NUM_GPUS_PER_TASK)
def generate_msa_remote(name: str, sequence: str, alphabet: str) -> dict:
    record = SequenceRecord(name=name, sequence=sequence, alphabet=alphabet)
    msa = sequence_featurize.to_msa(record)  # stub — see module docstring
    return {"worker": _worker_id(), "msa": msa}
