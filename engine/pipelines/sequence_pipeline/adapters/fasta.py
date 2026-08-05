"""FASTA input -> canonical SequenceRecord(s)."""

from engine.pipelines.sequence_pipeline.canonical import SequenceRecord, guess_alphabet


def parse(content: bytes) -> list[SequenceRecord]:
    text = content.decode("utf-8")
    records = []
    for raw_record in text.split(">")[1:]:
        lines = raw_record.splitlines()
        header, seq_lines = lines[0], lines[1:]
        sequence = "".join(seq_lines).strip().upper()
        records.append(SequenceRecord(name=header.strip(), sequence=sequence, alphabet=guess_alphabet(sequence)))
    return records
