"""v4.2.0 unit conversion helpers.

All mass units are normalized to milligrams (mg). Unknown units are
returned as ``None`` so callers can detect them and force REVIEW. Volume
units are passed through unchanged because no rule currently compares
volumes — they raise ``UnitConversionError`` if any rule attempts to use
them.

The conversion table is intentionally small; we only support the units
the rule base actually uses. Adding a new unit requires:

1. adding it to ``_MASS_FACTORS`` below,
2. updating ``schemas/patient_state.schema.json`` and
   ``schemas/dialogue_output.schema.json``,
3. noting the change in ``CHANGELOG.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# Conversion factors TO milligrams.
_MASS_FACTORS = {
    "mcg": 0.001,
    "μg": 0.001,
    "ug": 0.001,
    "mg": 1.0,
    "毫克": 1.0,
    "g": 1000.0,
    "克": 1000.0,
}

_VOLUME_UNITS = {"ml", "mL", "升", "l", "L"}
_INTERNATIONAL_UNITS = {"IU", "iu"}


class UnitConversionError(ValueError):
    """Raised when a numeric value cannot be interpreted (NaN, infinity,
    unparsable string, etc.)."""


class UnsupportedUnitError(ValueError):
    """Raised when a unit string is not in the conversion table."""


@dataclass
class ConvertedDose:
    """Result of a unit conversion attempt.

    ``is_valid`` is False when the input could not be converted safely.
    ``reason`` is a stable code (e.g. ``NEGATIVE_DOSE``) so that the
    audit pipeline can emit a structured finding.
    """

    value_mg: Optional[float]
    original_value: object
    original_unit: str
    is_valid: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "value_mg": self.value_mg,
            "original_value": self.original_value,
            "original_unit": self.original_unit,
            "is_valid": self.is_valid,
            "reason": self.reason,
        }


def normalize_unit(unit: object) -> str:
    """Normalize a unit string to its lowercase canonical form."""
    if unit is None:
        return ""
    return str(unit).strip().lower()


def is_finite_number(value: object) -> bool:
    """Return True iff ``value`` is a finite real number.

    Rejects None, NaN, +/-Infinity, booleans, and anything that cannot be
    parsed via ``float``.
    """
    if isinstance(value, bool):
        return False
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False
        try:
            return math.isfinite(float(text))
        except (TypeError, ValueError):
            return False
    return False


def to_finite_float(value: object) -> Optional[float]:
    """Convert ``value`` to a finite float. Returns None if invalid."""
    if isinstance(value, bool):
        return None
    if value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            f = float(text)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None
    return None


def convert_mass_to_mg(value: object, unit: object) -> ConvertedDose:
    """Convert ``value`` from ``unit`` to milligrams.

    Returns a :class:`ConvertedDose` with ``is_valid=False`` when:

    - the value is negative or not a finite number,
    - the unit cannot be converted (e.g. ``mL`` with no concentration),
    - the value is an unsupported type (None, list, ...).
    """
    original_unit = "" if unit is None else str(unit).strip()
    normalized = normalize_unit(unit)

    if value is None or (isinstance(value, str) and not value.strip()):
        return ConvertedDose(None, value, original_unit, False, "EMPTY_VALUE")

    if isinstance(value, bool):
        return ConvertedDose(None, value, original_unit, False, "BOOLEAN_VALUE")

    try:
        f = float(value)
    except (TypeError, ValueError):
        return ConvertedDose(None, value, original_unit, False, "UNPARSED_VALUE")

    if not math.isfinite(f):
        return ConvertedDose(None, value, original_unit, False, "NON_FINITE_VALUE")

    if f < 0:
        return ConvertedDose(None, value, original_unit, False, "NEGATIVE_DOSE")

    if normalized in _INTERNATIONAL_UNITS:
        # Cannot convert IU to mg without drug-specific conversion data.
        return ConvertedDose(None, value, original_unit, False, "IU_NOT_CONVERTIBLE")

    if normalized in _VOLUME_UNITS:
        return ConvertedDose(None, value, original_unit, False, "VOLUME_UNIT_FOR_MASS")

    factor = _MASS_FACTORS.get(normalized)
    if factor is None:
        return ConvertedDose(None, value, original_unit, False, "UNKNOWN_UNIT")

    mg = f * factor
    if not math.isfinite(mg):
        return ConvertedDose(None, value, original_unit, False, "OVERFLOW")
    return ConvertedDose(mg, value, original_unit, True, "")


def frequency_per_day_to_daily_count(value: object) -> Optional[float]:
    """Coerce ``value`` to a positive finite count of doses per day."""
    f = to_finite_float(value)
    if f is None or f <= 0:
        return None
    return f


def daily_total_mg(
    dose_value: object,
    dose_unit: object,
    frequency_per_day: object,
) -> ConvertedDose:
    """Convert (dose, unit, frequency) into a total daily mg amount.

    Returns ``is_valid=False`` whenever any component cannot be safely
    coerced. Callers must NOT silently fall back to zero.
    """
    freq = frequency_per_day_to_daily_count(frequency_per_day)
    if freq is None:
        return ConvertedDose(
            None,
            dose_value,
            "" if dose_unit is None else str(dose_unit),
            False,
            "INVALID_FREQUENCY",
        )
    converted = convert_mass_to_mg(dose_value, dose_unit)
    if not converted.is_valid:
        return converted
    daily = converted.value_mg * freq
    if not math.isfinite(daily):
        return ConvertedDose(
            None,
            dose_value,
            converted.original_unit,
            False,
            "OVERFLOW",
        )
    return ConvertedDose(
        daily,
        dose_value,
        converted.original_unit,
        True,
        "",
    )


def supported_units() -> list:
    """Return the sorted list of supported unit tokens."""
    return sorted(set(_MASS_FACTORS) | _VOLUME_UNITS | _INTERNATIONAL_UNITS)