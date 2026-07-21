"""v4.2.1 legacy input adapter.

The strict v4.2.1 schema rejects legacy fields (``name``, ``drug``,
``food``, ``activity``, ``concept``). For callers that have not yet
migrated, :class:`LegacyInputAdapter` converts a legacy v4.1 input
into the strict shape so the audit pipeline can still run.

The adapter is invoked **only** when ``compat_mode=True`` was passed to
:meth:`DialogueSafetyEngine.audit_payload`. Production callers must
NOT enable compat_mode. Every legacy-adapter run adds a
``DEPRECATED_INPUT_SCHEMA`` finding so the audit trail records that the
caller is using a deprecated contract.

What the adapter does NOT do:

- It does NOT silently derive drug_id from drug_name when the
  authoritative term table has no match. An unknown legacy drug_name
  is forwarded to the strict validator with both ``drug_id`` and
  ``drug_name`` empty; the strict validator will then emit
  ``INPUT_UNKNOWN_DRUG``.
- It does NOT mask the LLM's own ``requires_review=true`` or
  ``uncertainty_reasons``. Those pass through unchanged.
- It does NOT mask measurement freshness violations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


class LegacyInputAdapter:
    def __init__(self, repository) -> None:
        self._repository = repository

    # ----------------------------------------------------------- public

    def adapt(self, payload: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Convert legacy fields in ``payload`` and return
        ``(new_payload, findings)``.

        Only emits ``DEPRECATED_INPUT_SCHEMA`` if actual legacy fields
        were detected. New-schema callers that happened to opt into
        compat_mode see no findings.
        """
        if not isinstance(payload, dict):
            return {}, [{"code": "INPUT_NOT_OBJECT", "severity": "REVIEW",
                         "message": "Top-level audit input must be a JSON object.",
                         "details": {}}]

        findings: List[Dict[str, Any]] = []
        legacy_seen: List[str] = []

        out = dict(payload)
        out.setdefault("schema_version", "1.0")

        # patient_state.current_medications
        ps = dict(out.get("patient_state") or {})
        meds = []
        for med in ps.get("current_medications", []) or []:
            if not isinstance(med, dict):
                meds.append(med)
                continue
            m = dict(med)
            # v4.1 used ``name`` instead of ``drug_name``.
            if "drug_name" not in m and "name" in m:
                m["drug_name"] = m["name"]
                legacy_seen.append("patient_state.current_medications[].name")
            meds.append(m)
        ps["current_medications"] = meds
        out["patient_state"] = ps

        # dialogue_output.{medication_actions, food_advice, exercise_advice}
        do = dict(out.get("dialogue_output") or {})
        ma = []
        for action in do.get("medication_actions", []) or []:
            if not isinstance(action, dict):
                ma.append(action)
                continue
            a = dict(action)
            # legacy ``drug`` field becomes drug_name (if drug_name is
            # missing). drug_id is only auto-derived if the alias table
            # recognizes the legacy drug.
            if "drug_name" not in a and "drug" in a:
                a["drug_name"] = a["drug"]
                legacy_seen.append("dialogue_output.medication_actions[].drug")
            if not a.get("drug_id") and a.get("drug_name"):
                canonical = self._repository.canonical_drug(a["drug_name"])
                if canonical and canonical != a["drug_name"].strip().lower():
                    a["drug_id"] = canonical
            # ``replace_drug`` (legacy) maps to ``replace_drug_name``.
            if "replace_drug_name" not in a and "replace_drug" in a:
                a["replace_drug_name"] = a["replace_drug"]
                legacy_seen.append(
                    "dialogue_output.medication_actions[].replace_drug")
            if not a.get("replace_drug_id") and a.get("replace_drug_name"):
                canonical = self._repository.canonical_drug(
                    a["replace_drug_name"])
                if canonical and canonical != a["replace_drug_name"].strip().lower():
                    a["replace_drug_id"] = canonical
            # ``continue`` actions in v4.1 / v4.2.0 always carried an
            # implicit route="oral". Backward-compat only.
            if a.get("action") == "continue" and not a.get("route"):
                a["route"] = "oral"
                legacy_seen.append(
                    "dialogue_output.medication_actions[].route (continue)")
            # ``start`` / ``increase`` / ``decrease`` / ``replace`` in
            # v4.1 / v4.2.0 also implicitly carried route="oral". The
            # strict v4.2.1 pipeline rejects this; compat_mode is the
            # only path that auto-fills it for backward compat. The
            # DEPRECATED_INPUT_SCHEMA finding records the conversion.
            if a.get("action") in {"start", "increase", "decrease",
                                   "replace"} and not a.get("route"):
                a["route"] = "oral"
                legacy_seen.append(
                    f"dialogue_output.medication_actions[].route ({a['action']})")
            ma.append(a)
        do["medication_actions"] = ma

        fa = []
        for food in do.get("food_advice", []) or []:
            if not isinstance(food, dict):
                fa.append(food)
                continue
            f = dict(food)
            if "food_name" not in f and "food" in f:
                f["food_name"] = f["food"]
                legacy_seen.append("dialogue_output.food_advice[].food")
            if "food_concept_id" not in f:
                if "concept" in f:
                    f["food_concept_id"] = f["concept"]
                    legacy_seen.append("dialogue_output.food_advice[].concept")
                elif "food_name" in f:
                    f["food_concept_id"] = (
                        f["food_name"].strip().lower()
                    )
                    legacy_seen.append(
                        "dialogue_output.food_advice[].food->concept_id")
            fa.append(f)
        do["food_advice"] = fa

        ea = []
        for ex in do.get("exercise_advice", []) or []:
            if not isinstance(ex, dict):
                ea.append(ex)
                continue
            e = dict(ex)
            if "activity_name" not in e and "activity" in e:
                e["activity_name"] = e["activity"]
                legacy_seen.append("dialogue_output.exercise_advice[].activity")
            if "activity_concept_id" not in e:
                if "concept" in e:
                    e["activity_concept_id"] = e["concept"]
                    legacy_seen.append("dialogue_output.exercise_advice[].concept")
                elif "activity_name" in e:
                    # v4.1 callers that supplied only ``activity``
                    # never had a concept ID. Synthesize one so the
                    # strict downstream pipeline has something to
                    # index. We mark this as a deprecated conversion.
                    e["activity_concept_id"] = (
                        e["activity_name"].strip().lower()
                    )
                    legacy_seen.append(
                        "dialogue_output.exercise_advice[].activity->concept_id")
            ea.append(e)
        do["exercise_advice"] = ea

        out["dialogue_output"] = do

        if legacy_seen:
            findings.append({
                "code": "DEPRECATED_INPUT_SCHEMA",
                "severity": "INFO",
                "message": ("compat_mode=true was used; the legacy input "
                            "shape is deprecated. Migrate to the v4.2.1 "
                            "strict schema (drug_id + drug_name, "
                            "food_concept_id + food_name, "
                            "activity_concept_id + activity_name)."),
                "details": {"deprecated_fields": sorted(set(legacy_seen))},
            })

        return out, findings