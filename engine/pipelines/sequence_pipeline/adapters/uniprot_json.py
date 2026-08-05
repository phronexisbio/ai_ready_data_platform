"""UniProt REST JSON entry (as returned by rest.uniprot.org/uniprotkb/*.json)
-> canonical SequenceRecord."""

import json

from engine.pipelines.sequence_pipeline.canonical import SequenceRecord


def parse(content: bytes) -> list[SequenceRecord]:
    data = json.loads(content.decode("utf-8"))
    entries = data.get("results", [data]) if isinstance(data, dict) else data
    records = []
    for entry in entries:
        accession = entry.get("primaryAccession", "unknown")
        sequence = entry["sequence"]["value"].upper()
        records.append(SequenceRecord(name=accession, sequence=sequence, alphabet="protein"))
    return records
