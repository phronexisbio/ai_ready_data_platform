"""UniProt XML -> canonical SequenceRecord."""

import xml.etree.ElementTree as ET

from engine.pipelines.sequence_pipeline.canonical import SequenceRecord

_NS = {"up": "http://uniprot.org/uniprot"}


def _find_with_fallback(entry: ET.Element, tag: str) -> ET.Element | None:
    """`entry.find("up:x", NS) or entry.find("x")` looks right but is wrong:
    ElementTree's Element.__bool__ is `len(elem) > 0` (child count), not
    "was found" — a leaf element like <accession>P12345</accession> has no
    children, so it's falsy even on a successful find, and `or` silently
    falls through to the wrong branch. Explicit `is None` checks avoid that.
    """
    found = entry.find(f"up:{tag}", _NS)
    if found is None:
        found = entry.find(tag)
    return found


def parse(content: bytes) -> list[SequenceRecord]:
    root = ET.fromstring(content)
    entries = root.findall("up:entry", _NS) or root.findall("entry")  # a list — findall's emptiness check is fine
    records = []
    for entry in entries:
        accession_el = _find_with_fallback(entry, "accession")
        accession = accession_el.text if accession_el is not None else "unknown"
        seq_el = _find_with_fallback(entry, "sequence")
        sequence = (seq_el.text or "").replace("\n", "").strip().upper() if seq_el is not None else ""
        records.append(SequenceRecord(name=accession, sequence=sequence, alphabet="protein"))
    return records
