"""GenBank input -> canonical SequenceRecord, via Biopython."""

import io

from Bio import SeqIO

from engine.pipelines.sequence_pipeline.canonical import SequenceRecord


def parse(content: bytes) -> list[SequenceRecord]:
    text = content.decode("utf-8")
    records = []
    for rec in SeqIO.parse(io.StringIO(text), "genbank"):
        sequence = str(rec.seq).upper()
        records.append(SequenceRecord(name=rec.id, sequence=sequence, alphabet="nucleotide"))
    return records
