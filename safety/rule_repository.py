"""Rule repository for v4.2.0.

v4.2.0 changes
--------------
- **drug_only_rule_index** — a tight index of rules whose evaluation
  depends ONLY on the presence of a drug (no patient field, no
  combination, no risk flag). Almost no production rule type qualifies;
  the index is reserved for the synthetic stress tests.
- **DSL extensions** — parameters may now declare either:

    * a single ``field`` + ``operator`` + ``threshold`` (legacy), OR
    * a ``conditions`` block using ``all`` / ``any`` / ``not``
      predicates (v4.2.0), OR
    * a ``range`` block combining ``gte``/``lt`` predicates.

  When ``conditions`` is present the legacy fields are ignored.
- **all/any list semantics** — ``parameters.match_mode = "all"|"any"``
  is explicit whenever the parameter is a list.

The legacy API (``field_rule_ids(...)``, ``drug_pair_rule_ids(...)``,
etc.) is preserved for v4.1 callers. ``simple_drug_rule_ids`` continues
to exist but is no longer the default candidate recall path; the
candidate selector decides whether to consult it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


SUPPORTED_RULE_TYPES: Set[str] = {
    "max_daily_dose",
    "patient_state",
    "patient_condition",  # legacy alias kept for v3 files
    "patient_risk",
    "drug_drug",
    "drug_food",
    "drug_exercise",
    "disease_food",
    "disease_exercise",
    "response_compliance",
}

SUPPORTED_SEVERITIES: Set[str] = {"INFO", "WARN", "REVIEW", "BLOCK", "EMERGENCY"}

SUPPORTED_STATUSES: Set[str] = {"active", "inactive", "deprecated", "pending_medical_review"}

SUPPORTED_OPERATORS: Set[str] = {"lt", "lte", "gt", "gte", "eq", "contains", "in"}

VALID_RESPONSE_COMPLIANCE_KINDS: Set[str] = {
    "forbidden_medication_action",
    "forbidden_food_action",
    "forbidden_exercise_action",
    "required_care_action",
    "forbidden_drug_in_response",
    "required_stop_drug",
}

VALID_DSL_MATCH_MODES = {"all", "any"}


class RuleLoadError(ValueError):
    """Raised when a rule file is malformed. We fail loud on load."""


@dataclass
class Rule:
    id: str
    type: str
    severity: str
    triggers: Dict[str, List[str]]
    parameters: Dict[str, Any]
    message: str
    source: Dict[str, Any] = field(default_factory=dict)
    version: int = 1
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "severity": self.severity,
            "version": self.version,
            "status": self.status,
            "triggers": self.triggers,
            "parameters": self.parameters,
            "message": self.message,
            "source": self.source,
        }


@dataclass
class DrugAliasTable:
    canonical_to_aliases: Dict[str, Set[str]] = field(default_factory=dict)
    alias_to_canonical: Dict[str, str] = field(default_factory=dict)
    disease_to_codes: Dict[str, Set[str]] = field(default_factory=dict)
    disease_aliases_to_code: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "DrugAliasTable":
        table = cls()
        for canonical, aliases in payload.items():
            if canonical == "disease_aliases":
                for code, names in aliases.items():
                    normalized_code = _normalize_token(code)
                    name_set = {_normalize_token(name) for name in (names or []) + [code]}
                    name_set.discard("")
                    table.disease_to_codes[normalized_code] = name_set
                    for n in name_set:
                        table.disease_aliases_to_code[n] = normalized_code
                continue
            normalized_canonical = _normalize_token(canonical)
            alias_set = {_normalize_token(a) for a in (aliases or []) + [canonical]}
            alias_set.discard("")
            table.canonical_to_aliases[normalized_canonical] = alias_set
            for alias in alias_set:
                table.alias_to_canonical[alias] = normalized_canonical
        return table

    def canonical_drug(self, name: str) -> str:
        n = _normalize_token(name)
        return self.alias_to_canonical.get(n, n)

    def disease_code(self, raw: str) -> str:
        n = _normalize_token(raw)
        return self.disease_aliases_to_code.get(n, n)


class RuleRepository:
    def __init__(self, rules_dir: str | Path) -> None:
        self._dir = Path(rules_dir)
        if not self._dir.is_dir():
            raise RuleLoadError(f"Rules directory not found: {self._dir}")

        manifest = self._load_json(self._dir / "manifest.json")
        self.ruleset_version: str = manifest.get("ruleset_version", "unknown")
        self.decision_policy: Dict[str, str] = manifest.get("decision_policy", {})

        self._rules_by_id: Dict[str, Rule] = {}
        self._active_rules_by_id: Dict[str, Rule] = {}

        self.aliases = DrugAliasTable()
        self._diseases: Dict[str, Dict[str, Any]] = {}

        # Simple / compound indexes.
        self._simple_drug_index: Dict[str, Set[str]] = {}
        self._keyword_index: Dict[str, Set[str]] = {}
        self._field_only_rule_index: Dict[str, Set[str]] = {}
        # Legacy alias preserved.
        self._field_index: Dict[str, Set[str]] = self._field_only_rule_index
        self._risk_flag_index: Dict[str, Set[str]] = {}

        # Composite indexes.
        self._drug_pair_index: Dict[Tuple[str, str], Set[str]] = {}
        self._drug_field_index: Dict[Tuple[str, str], Set[str]] = {}
        self._drug_food_index: Dict[str, Set[str]] = {}
        self._drug_exercise_index: Dict[str, Set[str]] = {}
        self._disease_food_index: Dict[str, Set[str]] = {}
        self._disease_exercise_index: Dict[str, Set[str]] = {}
        self._risk_compliance_index: Dict[str, Set[str]] = {}
        self._keyword_rule_index: Dict[Tuple[str, str], Set[str]] = {}
        self._patient_risk_field_index: Dict[str, Set[str]] = {}

        # v4.2.0: tight drug-only index (no patient field, no pair,
        # no food/exercise, no disease, no risk). Almost no production
        # rule qualifies. Reserved for the synthetic stress tests.
        self._drug_only_rule_index: Dict[str, Set[str]] = {}

        self._pending_rule_ids: Set[str] = set()

        for relative_path in manifest.get("rule_files", []):
            self._load_file(relative_path)

    # --------------------------------------------------------------- loading

    def _load_file(self, relative_path: str) -> None:
        path = self._dir / relative_path
        if not path.exists():
            raise RuleLoadError(f"Declared rule file not found: {path}")

        payload = self._load_json(path)
        if not isinstance(payload, dict):
            raise RuleLoadError(f"{relative_path}: top-level JSON must be an object.")

        if "rules" in payload:
            for raw in payload["rules"]:
                self._register_rule(raw, source_file=relative_path)
            return

        incoming = DrugAliasTable.from_payload(payload)
        self._merge_aliases(incoming)

    def _merge_aliases(self, incoming: DrugAliasTable) -> None:
        for canonical, aliases in incoming.canonical_to_aliases.items():
            existing = self.aliases.canonical_to_aliases.get(canonical, set())
            existing |= aliases
            self.aliases.canonical_to_aliases[canonical] = existing
            for alias in aliases:
                self.aliases.alias_to_canonical[alias] = canonical
        for code, names in incoming.disease_to_codes.items():
            existing_names = self.aliases.disease_to_codes.get(code, set())
            existing_names |= names
            self.aliases.disease_to_codes[code] = existing_names
            for n in names:
                self.aliases.disease_aliases_to_code[n] = code

    def _register_rule(self, raw: Dict[str, Any], source_file: str) -> None:
        rule = self._validate_rule(raw, source_file)
        if rule.id in self._rules_by_id:
            raise RuleLoadError(
                f"Duplicate rule id {rule.id!r} in {source_file}"
            )

        self._rules_by_id[rule.id] = rule

        if rule.status == "pending_medical_review":
            self._pending_rule_ids.add(rule.id)
            return
        if rule.status in ("inactive", "deprecated"):
            return

        self._active_rules_by_id[rule.id] = rule
        self._index_rule(rule)

    def _validate_rule(self, raw: Dict[str, Any], source_file: str) -> Rule:
        if not isinstance(raw, dict):
            raise RuleLoadError(
                f"{source_file}: rule entry must be a JSON object"
            )
        rid = raw.get("id")
        if not isinstance(rid, str) or not rid:
            raise RuleLoadError(
                f"{source_file}: rule id must be a non-empty string"
            )

        version_raw = raw.get("version", 1)
        if not isinstance(version_raw, int) or version_raw < 1:
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} version must be a positive int"
            )

        missing = [
            k for k in ("type", "severity", "triggers", "parameters")
            if k not in raw
        ]
        if missing:
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} missing required fields {missing}"
            )

        rule_type = raw["type"]
        if rule_type not in SUPPORTED_RULE_TYPES:
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} unknown type {rule_type!r}"
            )

        severity = raw["severity"]
        if severity not in SUPPORTED_SEVERITIES:
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} bad severity {severity!r}"
            )

        status = str(raw.get("status", "active"))
        if status not in SUPPORTED_STATUSES:
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} bad status {status!r}"
            )

        triggers = raw["triggers"]
        if not isinstance(triggers, dict):
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} triggers must be dict"
            )
        for key in ("drugs_any", "keywords_any", "patient_fields_any", "risk_flags_any"):
            if key in triggers and not _is_list_of_str(triggers[key]):
                raise RuleLoadError(
                    f"{source_file}: rule {rid!r} triggers.{key} must be list[str]"
                )

        parameters = raw["parameters"]
        if not isinstance(parameters, dict):
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} parameters must be dict"
            )

        message = raw.get("message", "")
        if not isinstance(message, str) or not message:
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} must define non-empty message"
            )

        source = raw.get("source", {}) or {}
        if status != "pending_medical_review":
            if not isinstance(source, dict) or "document_title" not in source or "document_version" not in source:
                raise RuleLoadError(
                    f"{source_file}: rule {rid!r} source must include document_title and document_version"
                )

        if rule_type == "max_daily_dose":
            for key in ("drug", "max_daily_mg"):
                if key not in parameters:
                    raise RuleLoadError(
                        f"{source_file}: max_daily_dose rule {rid!r} needs {key!r}"
                    )
            if not _is_positive_number(parameters["max_daily_mg"]):
                raise RuleLoadError(
                    f"{source_file}: max_daily_dose rule {rid!r} max_daily_mg must be positive"
                )
            if "operator" in parameters and parameters["operator"] not in SUPPORTED_OPERATORS:
                raise RuleLoadError(
                    f"{source_file}: rule {rid!r} operator must be in {sorted(SUPPORTED_OPERATORS)}"
                )
        elif rule_type in ("patient_state", "patient_condition"):
            self._validate_patient_state_params(parameters, rid, source_file)
        elif rule_type == "patient_risk":
            for key in ("risk_code", "field", "operator", "threshold"):
                if key not in parameters:
                    raise RuleLoadError(
                        f"{source_file}: patient_risk rule {rid!r} needs {key!r}"
                    )
            if parameters["operator"] not in SUPPORTED_OPERATORS:
                raise RuleLoadError(
                    f"{source_file}: rule {rid!r} operator invalid"
                )
        elif rule_type == "drug_drug":
            for key in ("drug_a", "drug_b"):
                if key not in parameters:
                    raise RuleLoadError(
                        f"{source_file}: drug_drug rule {rid!r} needs {key!r}"
                    )
        elif rule_type in ("drug_food", "disease_food"):
            if "keywords" not in parameters:
                raise RuleLoadError(
                    f"{source_file}: {rule_type} rule {rid!r} needs 'keywords'"
                )
            if not isinstance(parameters["keywords"], list):
                raise RuleLoadError(
                    f"{source_file}: {rule_type} rule {rid!r} keywords must be list"
                )
            if "drug" not in parameters and "drugs" not in parameters \
                    and "disease_code" not in parameters:
                raise RuleLoadError(
                    f"{source_file}: {rule_type} rule {rid!r} needs drug, drugs, or disease_code"
                )
            if rule_type == "disease_food":
                if "disease_code" not in parameters:
                    raise RuleLoadError(
                        f"{source_file}: disease_food rule {rid!r} needs disease_code"
                    )
                trigger_risks = set(triggers.get("risk_flags_any", []) or [])
                trigger_fields = set(triggers.get("patient_fields_any", []) or [])
                if (
                    trigger_risks
                    and parameters["disease_code"] not in trigger_risks
                    and "disease_codes" not in trigger_fields
                ):
                    raise RuleLoadError(
                        f"{source_file}: disease_food rule {rid!r} disease_code "
                        f"must be in triggers.risk_flags_any or 'disease_codes' in patient_fields_any"
                    )
        elif rule_type in ("drug_exercise", "disease_exercise"):
            if "exercise_intensity" not in parameters:
                raise RuleLoadError(
                    f"{source_file}: {rule_type} rule {rid!r} needs exercise_intensity"
                )
            if rule_type == "drug_exercise":
                if "drug" not in parameters and "drugs" not in parameters:
                    raise RuleLoadError(
                        f"{source_file}: drug_exercise rule {rid!r} needs drug(s)"
                    )
            else:
                if "disease_code" not in parameters:
                    raise RuleLoadError(
                        f"{source_file}: disease_exercise rule {rid!r} needs disease_code"
                    )
                trigger_risks = set(triggers.get("risk_flags_any", []) or [])
                trigger_fields = set(triggers.get("patient_fields_any", []) or [])
                if (
                    trigger_risks
                    and parameters["disease_code"] not in trigger_risks
                    and "disease_codes" not in trigger_fields
                ):
                    raise RuleLoadError(
                        f"{source_file}: disease_exercise rule {rid!r} disease_code "
                        f"must be in triggers.risk_flags_any or 'disease_codes' in patient_fields_any"
                    )
        elif rule_type == "response_compliance":
            if "kind" not in parameters:
                raise RuleLoadError(
                    f"{source_file}: response_compliance rule {rid!r} needs 'kind'"
                )
            if parameters["kind"] not in VALID_RESPONSE_COMPLIANCE_KINDS:
                raise RuleLoadError(
                    f"{source_file}: response_compliance rule {rid!r} kind "
                    f"{parameters['kind']!r} invalid; valid: {sorted(VALID_RESPONSE_COMPLIANCE_KINDS)}"
                )
            for fa in parameters.get("forbidden_actions", []) or []:
                if not isinstance(fa, str) or not fa:
                    raise RuleLoadError(
                        f"{source_file}: response_compliance rule {rid!r} "
                        f"forbidden_actions contains non-string {fa!r}"
                    )
            for ct in parameters.get("required_care_types", []) or []:
                if not isinstance(ct, str) or not ct:
                    raise RuleLoadError(
                        f"{source_file}: response_compliance rule {rid!r} "
                        f"required_care_types contains non-string {ct!r}"
                    )

        return Rule(
            id=rid,
            type=rule_type,
            severity=severity,
            triggers=triggers,
            parameters=dict(parameters),
            message=message,
            source=source if isinstance(source, dict) else {},
            version=version_raw,
            status=status,
        )

    @staticmethod
    def _validate_patient_state_params(parameters, rid, source_file):
        if "conditions" not in parameters and "range" not in parameters:
            for key in ("field", "operator", "threshold"):
                if key not in parameters:
                    raise RuleLoadError(
                        f"{source_file}: patient_state rule {rid!r} needs {key!r}"
                    )
            if parameters["operator"] not in SUPPORTED_OPERATORS:
                raise RuleLoadError(
                    f"{source_file}: rule {rid!r} operator {parameters['operator']!r} invalid"
                )
        if "conditions" in parameters:
            _validate_conditions_block(parameters["conditions"], rid, source_file)
        if "range" in parameters:
            _validate_range_block(parameters["range"], rid, source_file)
        if "drug" not in parameters and "drugs" not in parameters:
            raise RuleLoadError(
                f"{source_file}: patient_state rule {rid!r} needs drug or drugs"
            )
        if "match_mode" in parameters and parameters["match_mode"] not in VALID_DSL_MATCH_MODES:
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} match_mode must be in {sorted(VALID_DSL_MATCH_MODES)}"
            )

    # -------------------------------------------------------------- indexing

    def _index_rule(self, rule: Rule) -> None:
        triggers = rule.triggers or {}

        for drug in triggers.get("drugs_any", []) or []:
            self._simple_drug_index.setdefault(drug, set()).add(rule.id)

        for kw in triggers.get("keywords_any", []) or []:
            self._keyword_index.setdefault(kw, set()).add(rule.id)

        if is_field_only_rule(rule):
            for field_name in triggers.get("patient_fields_any", []) or []:
                self._field_only_rule_index.setdefault(field_name, set()).add(rule.id)

        for risk_code in triggers.get("risk_flags_any", []) or []:
            self._risk_flag_index.setdefault(risk_code, set()).add(rule.id)

        if rule.type == "drug_drug":
            a = rule.parameters.get("drug_a")
            b = rule.parameters.get("drug_b")
            if a and b:
                key = tuple(sorted([a, b]))
                self._drug_pair_index.setdefault(key, set()).add(rule.id)

        elif rule.type == "max_daily_dose":
            drug = rule.parameters.get("drug") or (rule.parameters.get("drugs") or [None])[0]
            if drug:
                self._drug_field_index.setdefault((drug, "__dose__"), set()).add(rule.id)
                self._drug_only_rule_index.setdefault(drug, set()).add(rule.id)

        elif rule.type in ("patient_state", "patient_condition"):
            drugs = rule.parameters.get("drugs") or [rule.parameters.get("drug")]
            field_name = rule.parameters.get("field")
            # v4.2.0 DSL: a ``range`` block also declares a field. Index
            # the rule under that field so the candidate selector can
            # recall it.
            if field_name is None and isinstance(rule.parameters.get("range"), dict):
                field_name = rule.parameters["range"].get("field")
            for d in drugs or []:
                if d and field_name:
                    self._drug_field_index.setdefault((d, field_name), set()).add(rule.id)

        elif rule.type == "patient_risk":
            field_name = rule.parameters.get("field")
            if field_name:
                self._patient_risk_field_index.setdefault(field_name, set()).add(rule.id)

        elif rule.type == "drug_food":
            drugs = rule.parameters.get("drugs") or [rule.parameters.get("drug")]
            for d in drugs or []:
                if d:
                    self._drug_food_index.setdefault(d, set()).add(rule.id)
            for drug in drugs or []:
                for kw in rule.parameters.get("keywords", []) or []:
                    if drug:
                        self._keyword_rule_index.setdefault(
                            (drug, _normalize_token(kw)), set()
                        ).add(rule.id)

        elif rule.type == "disease_food":
            code = rule.parameters.get("disease_code")
            if code:
                self._disease_food_index.setdefault(code, set()).add(rule.id)

        elif rule.type == "drug_exercise":
            drugs = rule.parameters.get("drugs") or [rule.parameters.get("drug")]
            for d in drugs or []:
                if d:
                    self._drug_exercise_index.setdefault(d, set()).add(rule.id)

        elif rule.type == "disease_exercise":
            code = rule.parameters.get("disease_code")
            if code:
                self._disease_exercise_index.setdefault(code, set()).add(rule.id)

        elif rule.type == "response_compliance":
            for risk in triggers.get("risk_flags_any", []) or []:
                self._risk_compliance_index.setdefault(risk, set()).add(rule.id)
            for drug in triggers.get("drugs_any", []) or []:
                self._simple_drug_index.setdefault(drug, set()).add(rule.id)

    # --------------------------------------------------------------- access

    def canonical_drug(self, name: str) -> str:
        return self.aliases.canonical_drug(name)

    def disease_code(self, raw: str) -> str:
        return self.aliases.disease_code(raw)

    @property
    def rule_count(self) -> int:
        return len(self._rules_by_id)

    @property
    def active_rule_count(self) -> int:
        return len(self._active_rules_by_id)

    @property
    def pending_rule_ids(self) -> List[str]:
        return sorted(self._pending_rule_ids)

    def __len__(self) -> int:
        return len(self._rules_by_id)

    def get(self, rule_id: str) -> Rule:
        return self._rules_by_id[rule_id]

    def get_active(self, rule_id: str) -> Optional[Rule]:
        return self._active_rules_by_id.get(rule_id)

    def iter_active_rules(self) -> Iterable[Rule]:
        for r in self._active_rules_by_id.values():
            yield r

    def iter_all_rules(self) -> Iterable[Rule]:
        for r in self._rules_by_id.values():
            yield r

    def rule_ids(self) -> List[str]:
        return list(self._rules_by_id.keys())

    # --------------------------------------------------------- simple queries

    def simple_drug_rule_ids(self, drugs: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for d in drugs:
            out |= self._simple_drug_index.get(d, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def drug_only_rule_ids(self, drugs: Iterable[str]) -> Set[str]:
        """v4.2.0: tightest recall — only rules that bind ONLY a drug.

        Currently: ``max_daily_dose`` is the only production rule type
        that qualifies. Synthetic stress tests in tests/ use this index
        to prove that 1000 patient_state decoys sharing a drug do NOT
        leak into the candidate set when only a single field is present.
        """
        out: Set[str] = set()
        for d in drugs:
            out |= self._drug_only_rule_index.get(d, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def keyword_rule_ids(self, keywords: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for k in keywords:
            out |= self._keyword_index.get(k, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def field_rule_ids(self, fields: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for f in fields:
            out |= self._field_index.get(f, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def risk_flag_rule_ids(self, codes: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for c in codes:
            out |= self._risk_flag_index.get(c, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    # --------------------------------------------------------- composite queries

    def drug_pair_rule_ids(self, drugs: Set[str]) -> Set[str]:
        out: Set[str] = set()
        drugs_sorted = sorted(drugs)
        for i, a in enumerate(drugs_sorted):
            for b in drugs_sorted[i + 1:]:
                out |= self._drug_pair_index.get((a, b), set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def drug_field_rule_ids(self, drugs: Set[str], fields: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for d in drugs:
            for f in fields:
                out |= self._drug_field_index.get((d, f), set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def drug_food_rule_ids(self, drugs: Set[str]) -> Set[str]:
        out: Set[str] = set()
        for d in drugs:
            out |= self._drug_food_index.get(d, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def drug_exercise_rule_ids(self, drugs: Set[str]) -> Set[str]:
        out: Set[str] = set()
        for d in drugs:
            out |= self._drug_exercise_index.get(d, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def disease_food_rule_ids(self, codes: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for c in codes:
            out |= self._disease_food_index.get(c, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def disease_exercise_rule_ids(self, codes: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for c in codes:
            out |= self._disease_exercise_index.get(c, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def risk_compliance_rule_ids(self, codes: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for c in codes:
            out |= self._risk_compliance_index.get(c, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def food_keyword_rule_ids(self, drug: str, keywords: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for kw in keywords:
            out |= self._keyword_rule_index.get((drug, _normalize_token(kw)), set())
        return {rid for rid in out if self._is_evaluable(rid)}

    def patient_risk_rule_ids_for_fields(self, fields: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for f in fields:
            out |= self._patient_risk_field_index.get(f, set())
        return {rid for rid in out if self._is_evaluable(rid)}

    # ------------------------------------------------------------ diagnostics

    def describe_indexes(self) -> Dict[str, int]:
        return {
            "rules": len(self._rules_by_id),
            "active_rules": len(self._active_rules_by_id),
            "pending": len(self._pending_rule_ids),
            "drug_keys": len(self._simple_drug_index),
            "drug_only_keys": len(self._drug_only_rule_index),
            "keyword_keys": len(self._keyword_index),
            "field_keys": len(self._field_only_rule_index),
            "risk_keys": len(self._risk_flag_index),
            "drug_pair_keys": len(self._drug_pair_index),
            "drug_field_keys": len(self._drug_field_index),
            "drug_food_keys": len(self._drug_food_index),
            "drug_exercise_keys": len(self._drug_exercise_index),
            "disease_food_keys": len(self._disease_food_index),
            "disease_exercise_keys": len(self._disease_exercise_index),
            "risk_compliance_keys": len(self._risk_compliance_index),
            "patient_risk_field_keys": len(self._patient_risk_field_index),
            "drugs": len(self.aliases.canonical_to_aliases),
            "disease_codes": len(self.aliases.disease_to_codes),
        }

    # ------------------------------------------------------------ helpers

    def _is_evaluable(self, rule_id: str) -> bool:
        return rule_id in self._active_rules_by_id

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuleLoadError(f"Invalid JSON in {path}: {exc}") from exc


# ----------------------------------------------------------------- helpers


def is_field_only_rule(rule: Rule) -> bool:
    """Decide whether ``rule`` should be indexed under the
    ``_field_only_rule_index`` (no drug binding)."""

    if rule.type == "patient_risk":
        return False

    if rule.type in {
        "patient_state",
        "patient_condition",
        "max_daily_dose",
        "drug_drug",
        "drug_food",
        "drug_exercise",
    }:
        return False

    if rule.type in {"disease_food", "disease_exercise"}:
        return False

    if rule.type == "response_compliance":
        return False

    params = rule.parameters or {}
    has_drug = bool(
        params.get("drug") or params.get("drugs") or params.get("drug_a")
    )
    return not has_drug


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_list_of_str(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _is_positive_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return value > 0


def _validate_conditions_block(cond, rid, source_file):
    if not isinstance(cond, dict):
        raise RuleLoadError(
            f"{source_file}: rule {rid!r} conditions must be dict"
        )
    if "all" in cond:
        if not isinstance(cond["all"], list):
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} conditions.all must be list"
            )
        for c in cond["all"]:
            _validate_predicate(c, rid, source_file)
    if "any" in cond:
        if not isinstance(cond["any"], list):
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} conditions.any must be list"
            )
        for c in cond["any"]:
            _validate_predicate(c, rid, source_file)
    if "not" in cond:
        _validate_predicate(cond["not"], rid, source_file)
    if not any(k in cond for k in ("all", "any", "not")):
        raise RuleLoadError(
            f"{source_file}: rule {rid!r} conditions needs all/any/not"
        )


def _validate_range_block(rng, rid, source_file):
    if not isinstance(rng, dict):
        raise RuleLoadError(
            f"{source_file}: rule {rid!r} range must be dict"
        )
    for k in ("field",):
        if k not in rng:
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} range needs {k!r}"
            )
    for op in ("gte", "gt", "lte", "lt"):
        if op in rng and not _is_numeric(rng[op]):
            raise RuleLoadError(
                f"{source_file}: rule {rid!r} range.{op} must be numeric"
            )


def _validate_predicate(pred, rid, source_file):
    if not isinstance(pred, dict):
        raise RuleLoadError(
            f"{source_file}: rule {rid!r} predicate must be dict"
        )
    if "field" not in pred or "operator" not in pred or "value" not in pred:
        raise RuleLoadError(
            f"{source_file}: rule {rid!r} predicate needs field/operator/value"
        )
    if pred["operator"] not in SUPPORTED_OPERATORS:
        raise RuleLoadError(
            f"{source_file}: rule {rid!r} predicate operator invalid"
        )


def _is_numeric(v) -> bool:
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float))