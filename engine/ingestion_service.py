"""NATS -> Argo bridge — the "Ingestion Service" stage in BUILD_PLAN.md's
architecture diagram, between the event bus and the metadata catalog /
validation engine.

Pulls "file landed" events off the PLATFORM_EVENTS stream and submits one
Argo Workflow (the ingest-validate WorkflowTemplate) per file — this is the
piece that turns a connector's event into ingest+validate work, without the
connector ever calling the pipeline directly.

Drains whatever is currently pending and exits. That's deliberately not a
long-running Deployment yet: there's no job contention or scheduling need to
justify one until Phase 7/9, and a durable JetStream consumer means nothing
is lost by running this as a periodic batch instead of an always-on service —
each run picks up exactly where the last one left off.
"""

import asyncio
import json
import os
import subprocess

import nats
from nats.errors import TimeoutError as NatsTimeoutError

NATS_URL = os.environ.get("NATS_URL", "nats://nats.data-platform.svc.cluster.local:4222")
STREAM = "PLATFORM_EVENTS"
SUBJECT_FILTER = "platform.catalog.file.>"
DURABLE_NAME = "ingestion-service"
ARGO_NAMESPACE = os.environ.get("ARGO_NAMESPACE", "data-platform")


def _submit_workflow(event: dict) -> str:
    workflow = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {"generateName": "ingest-validate-", "namespace": ARGO_NAMESPACE},
        "spec": {
            "workflowTemplateRef": {"name": "ingest-validate"},
            "arguments": {
                "parameters": [
                    {"name": "file-id", "value": event["file_id"]},
                    {"name": "source", "value": event["source"]},
                    {"name": "dataset-id", "value": event["dataset_id"]},
                    {"name": "dataset-version", "value": str(event["dataset_version"])},
                    {"name": "modality", "value": event["modality"]},
                    {"name": "location", "value": event["location"]},
                ]
            },
        },
    }
    result = subprocess.run(
        ["kubectl", "create", "-f", "-"],
        input=json.dumps(workflow),
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl create failed for file_id={event['file_id']}: {result.stderr.strip()}")
    return result.stdout.strip()


async def drain() -> int:
    nc = await nats.connect(NATS_URL)
    processed = 0
    try:
        js = nc.jetstream()
        sub = await js.pull_subscribe(SUBJECT_FILTER, durable=DURABLE_NAME, stream=STREAM)
        while True:
            try:
                msgs = await sub.fetch(10, timeout=2)
            except NatsTimeoutError:
                break
            if not msgs:
                break
            for msg in msgs:
                event = json.loads(msg.data)
                print(f"file_id={event['file_id']} source={event['source']} location={event['location']}")
                print(f"  -> {_submit_workflow(event)}")
                await msg.ack()
                processed += 1
    finally:
        await nc.close()
    return processed


def main():
    processed = asyncio.run(drain())
    print(f"submitted {processed} workflow(s)")


if __name__ == "__main__":
    main()
