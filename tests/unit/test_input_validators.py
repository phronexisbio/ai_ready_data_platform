"""Unit tests for engine/validators/input/* — BUILD_PLAN.md §6 input gate,
Phase 11. One good + one bad case per modality, plus the registry's
unregistered-modality behavior.
"""

from engine.validators.input.registry import validate

GOOD_PDB = b"""ATOM      1  N   ALA A   1      11.104   6.134  -6.504  1.00 20.00           N
ATOM      2  CA  ALA A   1      11.639   6.071  -5.147  1.00 20.00           C
END
"""

GOOD_TIFF = None  # built lazily below, needs tifffile


def _good_tiff_bytes() -> bytes:
    import io

    import numpy as np
    import tifffile

    buf = io.BytesIO()
    tifffile.imwrite(buf, np.zeros((2, 4, 4), dtype="uint8") + 100)
    return buf.getvalue()


def test_sequence_good_and_bad():
    ok, reason = validate("sequence", b">seq\nMVLSPADK\n")
    assert ok and reason is None

    ok, reason = validate("sequence", b">seq\nMVLSP123XYZ\n")
    assert not ok and "invalid residues" in reason


def test_molecule_good_and_bad():
    ok, _ = validate("molecule", b"CC(=O)OC1=CC=CC=C1C(=O)O aspirin\n")
    assert ok

    ok, reason = validate("molecule", b"CC!!!garbage\n")
    assert not ok and "not valid in SMILES" in reason


def test_tabular_good_and_bad():
    ok, _ = validate("tabular", b"a,b,c\n1,2,3\n4,5,6\n")
    assert ok

    ok, reason = validate("tabular", b"a,b,c\n1,2\n")
    assert not ok and "columns" in reason


def test_text_good_and_bad():
    ok, _ = validate("text", b'{"key": "value"}')
    assert ok

    ok, reason = validate("text", b"{not json")
    assert not ok and "invalid JSON" in reason


def test_structure_good_and_bad():
    ok, _ = validate("structure", GOOD_PDB)
    assert ok

    ok, reason = validate("structure", b"this is not a structure file")
    assert not ok and "no atom records" in reason


def test_image_good_and_bad():
    ok, _ = validate("image", _good_tiff_bytes())
    assert ok

    ok, reason = validate("image", b"not a tiff file")
    assert not ok and "could not parse" in reason


def test_unregistered_modality_is_rejected_not_silently_accepted():
    ok, reason = validate("some_future_modality", b"anything")
    assert not ok
    assert "no input validator registered" in reason
