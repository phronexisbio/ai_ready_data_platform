"""NATS JetStream publishing shared by every connector.

Connectors never call the processing pipeline directly — they land a file,
register it in the catalog, and publish one event here. That's what gives
retry, buffering, and audit for free (BUILD_PLAN.md §3), and what lets a
future connector (e.g. a LIMS connector) be added without touching anything
downstream.

Publishing onto the `platform.>` subject space requires the PLATFORM_EVENTS
stream to already exist (see infra/setup-nats-streams.sh) — JetStream refuses
publishes on a subject no stream is configured to capture.
"""

import asyncio
import json
import os

import nats

NATS_URL = os.environ.get("NATS_URL", "nats://nats.data-platform.svc.cluster.local:4222")
FILE_LANDED_SUBJECT_PREFIX = "platform.catalog.file"


async def _publish(subject: str, payload: dict) -> None:
    nc = await nats.connect(NATS_URL)
    try:
        js = nc.jetstream()
        await js.publish(subject, json.dumps(payload).encode("utf-8"))
    finally:
        await nc.close()


def publish_file_landed(
    source: str,
    file_id: str,
    dataset_id: str,
    dataset_version: int,
    location: str,
    modality: str,
) -> str:
    """Emit one event per newly landed file. Returns the subject used."""
    subject = f"{FILE_LANDED_SUBJECT_PREFIX}.{source}"
    payload = {
        "file_id": file_id,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "location": location,
        "modality": modality,
        "source": source,
    }
    asyncio.run(_publish(subject, payload))
    return subject
