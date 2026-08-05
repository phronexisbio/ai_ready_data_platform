"""Connector framework — BUILD_PLAN.md §3.

Every data source (public database, cloud storage, or manual upload)
implements the same discover() / fetch() / validate() interface. `run()` then
does the same land -> register -> emit sequence for every connector: a
connector's only job is getting bytes into the `landing/` zone, registering
them in the catalog, and publishing one event per file — never calling the
processing pipeline directly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from connectors import events
from connectors.catalog_client import CatalogClient
from connectors.storage import checksum, land


@dataclass
class DiscoveredItem:
    """One thing a connector found at the source, before fetching its bytes."""

    name: str
    uri: str
    modality: str
    metadata: dict = field(default_factory=dict)


@dataclass
class FetchedItem:
    """A discovered item after its bytes have been pulled."""

    name: str
    content: bytes
    modality: str
    metadata: dict = field(default_factory=dict)


@dataclass
class LandedFile:
    file_id: str
    dataset_id: str
    dataset_version: int
    location: str
    modality: str
    event_subject: str


class Connector(ABC):
    """Base class every source connector implements the same way."""

    source: str  # short slug, e.g. "uniprot", "local" — used in landing paths and event subjects

    def __init__(self, catalog: CatalogClient | None = None):
        self.catalog = catalog or CatalogClient()

    @abstractmethod
    def discover(self) -> list[DiscoveredItem]:
        """Find what's available/new at the source."""

    @abstractmethod
    def fetch(self, item: DiscoveredItem) -> FetchedItem:
        """Pull one discovered item's bytes."""

    def validate(self, item: FetchedItem) -> bool:
        """Cheap sanity check before landing. Non-empty by default — override
        for source-specific checks (well-formed FASTA, etc). This is the
        input-validation half of BUILD_PLAN §6, not the output-tensor half."""
        return bool(item.content)

    def run(self, dataset_id: str, owner: str) -> list[LandedFile]:
        """discover -> fetch -> validate -> land -> register dataset+files -> emit events."""
        discovered = self.discover()
        fetched = [self.fetch(item) for item in discovered]
        valid = [item for item in fetched if self.validate(item)]

        manifest = {"files": [item.name for item in valid]}
        dataset = self.catalog.create_dataset(
            dataset_id=dataset_id,
            owner=owner,
            source=self.source,
            manifest=manifest,
        )
        dataset_version = dataset["dataset_version"]

        landed: list[LandedFile] = []
        for item in valid:
            key = f"{self.source}/{dataset_id}/{item.name}"
            location = land(key, item.content)
            content_hash = checksum(item.content)

            file_record = self.catalog.create_file(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                source=self.source,
                checksum=content_hash,
                modality=item.modality,
                location=location,
            )

            subject = events.publish_file_landed(
                source=self.source,
                file_id=file_record["file_id"],
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                location=location,
                modality=item.modality,
            )

            landed.append(
                LandedFile(
                    file_id=file_record["file_id"],
                    dataset_id=dataset_id,
                    dataset_version=dataset_version,
                    location=location,
                    modality=item.modality,
                    event_subject=subject,
                )
            )

        return landed
