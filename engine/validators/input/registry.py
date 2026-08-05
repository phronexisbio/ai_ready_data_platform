"""Modality -> input validator dispatch — the input-validation half of the
modality router described in BUILD_PLAN.md §4/§6. A modality with no
registered validator here is unimplemented, not silently accepted.
"""

from engine.validators.input import image, molecule, sequence, structure, tabular, text

_VALIDATORS = {
    "sequence": sequence.validate,
    "molecule": molecule.validate,
    "tabular": tabular.validate,
    "text": text.validate,
    "structure": structure.validate,
    "image": image.validate,
}


def validate(modality: str, content: bytes) -> tuple[bool, str | None]:
    validator = _VALIDATORS.get(modality)
    if validator is None:
        return False, f"no input validator registered for modality '{modality}'"
    return validator(content)
