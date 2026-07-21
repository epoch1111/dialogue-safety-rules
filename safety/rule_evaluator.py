"""Rule evaluator for v4.2.0.

v4.2.0 changes
--------------
- Supports the new DSL: ``parameters.conditions`` (``all`` / ``any`` /
  ``not``) and ``parameters.range`` (``gte``/``lt``).
- Uses :mod:`safety.unit_converter` to compare doses regardless of the
  input unit (``g``, ``mg``, ``mcg``). Unknown or unparsable units
  cause ``max_daily_dose`` rules to fire (we cannot compare → block).
- For ``text_extractions``, doses are converted to mg before comparison.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from safety.models import (
    DrugContext,
    NormalizedDraft,
    RiskFlag,
    RuleConditionTrace,
    RuleEvaluationTrace,
    RuleViolation,
    TextExtraction,
)
from safety.normalizer import normalize
from safety.rule_repository import Rule
from safety.unit_converter import (
    convert_mass_to_mg,
    daily_total_mg,
    is_finite_number,
)


_FOOD_RECOMMEND_ACTIONS = {"recommend", "allow"}
_EXERCISE_RECOMMEND_ACTIONS = {"recommend", "allow"}
_EXERCISE_AVOID_ACTIONS = {"avoid", "stop", "limit"}


class RuleEvaluator:
    SUPPORTED_OPERATORS: Set[str] = {"lt", "lte", "gt", "gte", "eq"}

    # ----------------------------------------------------------------- public

    def evaluate(
        self,
        rule: Rule,
        patient_state: Dict[str, Any],
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        risk_flags: List[RiskFlag],
        text_extractions: List[TextExtraction],
        canonical_drug=None,
    ) -> Optional[RuleViolation]:
        result, _trace = self._evaluate_impl(
            rule, patient_state, draft, drug_ctx, risk_flags,
            text_extractions, canonical_drug, collect_trace=False,
        )
        return result

    def evaluate_with_trace(
        self,
        rule: Rule,
        patient_state: Dict[str, Any],
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        risk_flags: List[RiskFlag],
        text_extractions: List[TextExtraction],
        canonical_drug=None,
    ) -> Tuple[Optional[RuleViolation], List[RuleConditionTrace]]:
        return self._evaluate_impl(
            rule, patient_state, draft, drug_ctx, risk_flags,
            text_extractions, canonical_drug, collect_trace=True,
        )

    def _evaluate_impl(
        self,
        rule: Rule,
        patient_state: Dict[str, Any],
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        risk_flags: List[RiskFlag],
        text_extractions: List[TextExtraction],
        canonical_drug=None,
        collect_trace: bool = False,
    ) -> Tuple[Optional[RuleViolation], List[RuleConditionTrace]]:
        conditions: List[RuleConditionTrace] = []
        ctx = dict(collect_trace=collect_trace, conditions=conditions)

        if rule.status != "active":
            if collect_trace:
                conditions.append(RuleConditionTrace(
                    description=f"rule status is {rule.status!r}, not active",
                    actual=rule.status, operator="==", expected="active",
                    passed=False,
                ))
            return None, conditions

        if rule.type == "max_daily_dose":
            return self._eval_max_daily_dose(rule, draft, drug_ctx, text_extractions, canonical_drug, ctx), conditions
        if rule.type in ("patient_state", "patient_condition"):
            return self._eval_patient_state(rule, patient_state, drug_ctx, ctx), conditions
        if rule.type == "patient_risk":
            return self._eval_patient_risk(rule, patient_state, drug_ctx, ctx), conditions
        if rule.type == "drug_drug":
            return self._eval_drug_drug(rule, drug_ctx, ctx), conditions
        if rule.type == "drug_food":
            return self._eval_drug_food(rule, draft, drug_ctx, canonical_drug, ctx), conditions
        if rule.type == "drug_exercise":
            return self._eval_drug_exercise(rule, patient_state, draft, drug_ctx, canonical_drug, ctx), conditions
        if rule.type == "disease_food":
            return self._eval_disease_food(rule, draft, risk_flags, ctx), conditions
        if rule.type == "disease_exercise":
            return self._eval_disease_exercise(rule, draft, risk_flags, ctx), conditions
        if rule.type == "response_compliance":
            return self._eval_response_compliance(
                rule, draft, drug_ctx, risk_flags, canonical_drug, ctx
            ), conditions
        raise ValueError(f"Unsupported rule type: {rule.type}")

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _violation(rule: Rule, category: str, details: Dict[str, Any]) -> RuleViolation:
        return RuleViolation(
            rule_id=rule.id,
            severity=rule.severity,
            category=category,
            message=rule.message,
            details=details,
        )

    @staticmethod
    def _trace(ctx: dict, description: str, actual: Any = None,
               operator: str = "", expected: Any = None, passed: bool = False) -> None:
        if not ctx.get("collect_trace"):
            return
        conds: List[RuleConditionTrace] = ctx.setdefault("conditions", [])
        conds.append(RuleConditionTrace(
            description=description, actual=actual,
            operator=operator, expected=expected, passed=passed,
        ))

    @staticmethod
    def _compare(actual: Any, operator: str, threshold: Any) -> bool:
        if actual is None:
            return False
        try:
            left = float(actual)
            right = float(threshold)
        except (TypeError, ValueError):
            return False
        if operator == "lt":
            return left < right
        if operator == "lte":
            return left <= right
        if operator == "gt":
            return left > right
        if operator == "gte":
            return left >= right
        if operator == "eq":
            return left == right
        if operator == "contains":
            try:
                return str(threshold) in str(actual)
            except (TypeError, ValueError):
                return False
        if operator == "in":
            return str(actual) in str(threshold)
        raise ValueError(f"Unsupported operator: {operator}")

    @staticmethod
    def _targets(rule: Rule) -> List[str]:
        d = rule.parameters.get("drug")
        ds = rule.parameters.get("drugs") or []
        out: List[str] = []
        if d:
            out.append(d)
        if ds:
            out.extend(ds)
        return out

    @staticmethod
    def _canonicalize_drug(
        name: str,
        drug_ctx: DrugContext,
        resolver=None,
    ) -> str:
        if not name:
            return ""
        if callable(resolver):
            return resolver(name)
        n = name.strip().lower()
        known = set(drug_ctx.resulting_drugs) | set(drug_ctx.current_drugs) | set(drug_ctx.recommended_drugs)
        if n in known:
            return n
        for canonical in known:
            if canonical in n.lower() or n in canonical.lower():
                return canonical
        return n

    # -------------------------------------------------------- v4.2.0 DSL

    def _eval_predicates(
        self,
        conditions: Any,
        patient_state: Dict[str, Any],
        rule: Rule,
        ctx: dict,
    ) -> bool:
        """Evaluate an all/any/not block. Returns True iff all predicates pass."""
        if not isinstance(conditions, dict):
            return False
        if "all" in conditions:
            children = conditions["all"] or []
            for child in children:
                if not self._eval_single_predicate(child, patient_state, rule, ctx):
                    return False
            return True
        if "any" in conditions:
            children = conditions["any"] or []
            if not children:
                return False
            return any(
                self._eval_single_predicate(c, patient_state, rule, ctx)
                for c in children
            )
        if "not" in conditions:
            return not self._eval_single_predicate(conditions["not"], patient_state, rule, ctx)
        return False

    def _eval_single_predicate(
        self,
        pred: Any,
        patient_state: Dict[str, Any],
        rule: Rule,
        ctx: dict,
    ) -> bool:
        if not isinstance(pred, dict):
            return False
        field = pred.get("field")
        operator = pred.get("operator")
        value = pred.get("value")
        actual = patient_state.get(field) if field else None
        passed = self._compare(actual, operator, value)
        self._trace(ctx, f"{field} {operator} {value}", actual=actual,
                    operator=operator, expected=value, passed=passed)
        return passed

    def _eval_range_block(
        self,
        rng: Any,
        patient_state: Dict[str, Any],
        rule: Rule,
        ctx: dict,
    ) -> bool:
        if not isinstance(rng, dict):
            return False
        field = rng.get("field")
        actual = patient_state.get(field) if field else None
        if actual is None:
            self._trace(ctx, f"range on {field!r} but field missing",
                        actual=None, passed=False)
            return False
        for op, key in (("gte", "gte"), ("gt", "gt"),
                        ("lte", "lte"), ("lt", "lt")):
            if key in rng:
                passed = self._compare(actual, op, rng[key])
                self._trace(
                    ctx,
                    f"{field} {op} {rng[key]}",
                    actual=actual,
                    operator=op,
                    expected=rng[key],
                    passed=passed,
                )
                if not passed:
                    return False
        return True

    # ------------------------------------------------------------- per-type

    def _eval_max_daily_dose(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        text_extractions: List[TextExtraction],
        canonical_drug=None,
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        ctx = ctx or {}
        target = rule.parameters["drug"]
        max_daily_mg = float(rule.parameters["max_daily_mg"])
        self._trace(ctx, "max_daily_dose target drug", actual=target,
                    expected=target, passed=True)
        self._trace(ctx, "max_daily_mg threshold", actual=max_daily_mg,
                    passed=True)

        # (a) structured actions — convert via unit_converter.
        for action in draft.medication_actions:
            # Prefer the v4.2.0 canonical drug_id; fall back to the
            # legacy ``drug`` field so older inputs still fire.
            drug_label = action.drug_id or action.drug
            if not drug_label:
                continue
            if action.action == "stop":
                continue
            canonical = self._canonicalize_drug(drug_label, drug_ctx, resolver=canonical_drug)
            if canonical != target:
                continue

            # Skip evaluation when dose fields are missing entirely —
            # the validator and SYS002 already report this as REVIEW.
            if (
                action.dose_value is None
                or not action.dose_unit
                or action.frequency_per_day is None
            ):
                self._trace(
                    ctx,
                    f"action {action.drug!r} {action.action!r} missing dose "
                    f"fields; deferring to SYS002 / validator",
                    passed=False,
                )
                continue

            converted = daily_total_mg(
                action.dose_value, action.dose_unit, action.frequency_per_day,
            )
            if not converted.is_valid:
                # The unit is unparseable (NaN, mL, IU, ...) — per the
                # v4.2.0 spec "未知单位必须 REVIEW，不能跳过剂量规则"。
                # We surface a REVIEW-severity medical violation so the
                # audit aggregates REVIEW rather than BLOCK.
                self._trace(
                    ctx,
                    f"action {action.drug!r} {action.action!r} has unconvertible "
                    f"dose {action.dose_value} {action.dose_unit} ({converted.reason})",
                    actual=action.dose_value,
                    passed=False,
                )
                return RuleViolation(
                    rule_id=rule.id,
                    severity="REVIEW",
                    category="dose",
                    message=(
                        f"{action.drug} 的剂量 {action.dose_value}{action.dose_unit} "
                        f"无法换算为标准单位，请医生或药师复核。"
                    ),
                    details={
                        "drug": target,
                        "dose_mg": action.dose_value,
                        "frequency_per_day": action.frequency_per_day,
                        "dose_unit": action.dose_unit,
                        "source": "structured",
                        "reason": converted.reason,
                    },
                )

            daily = converted.value_mg
            self._trace(
                ctx,
                f"structured action {action.drug!r} {action.action!r}: "
                f"{daily} mg/day",
                actual=daily, operator="gt", expected=max_daily_mg,
                passed=daily <= max_daily_mg,
            )
            if daily > max_daily_mg:
                return self._violation(
                    rule,
                    "dose",
                    {
                        "drug": target,
                        "dose_mg": action.dose_value,
                        "frequency_per_day": action.frequency_per_day,
                        "dose_unit": action.dose_unit,
                        "calculated_daily_mg": daily,
                        "max_daily_mg": max_daily_mg,
                        "source": "structured",
                    },
                )

        # (b) text extractions.
        if target in drug_ctx.mentioned_drugs or self._drug_in_text_extractions(
            target, text_extractions, drug_ctx, canonical_drug
        ):
            for ext in text_extractions:
                canonical_ext = self._canonicalize_drug(
                    ext.drug or "", drug_ctx, resolver=canonical_drug
                )
                if not canonical_ext or canonical_ext != target:
                    continue
                if ext.confidence in ("low", "none"):
                    continue
                if ext.dose_value is None:
                    continue
                converted = convert_mass_to_mg(ext.dose_value, ext.dose_unit)
                if not converted.is_valid:
                    self._trace(
                        ctx,
                        f"text extraction {ext.raw_match!r} has unconvertible "
                        f"dose ({converted.reason})",
                        actual=ext.dose_value,
                        passed=False,
                    )
                    return RuleViolation(
                        rule_id=rule.id,
                        severity="REVIEW",
                        category="dose",
                        message=(
                            f"{target} 的剂量 {ext.dose_value}{ext.dose_unit} "
                            f"无法换算为标准单位，请医生或药师复核。"
                        ),
                        details={
                            "drug": target,
                            "dose_mg": ext.dose_value,
                            "frequency_per_day": ext.frequency_per_day,
                            "dose_unit": ext.dose_unit,
                            "source": "reply_text",
                            "confidence": ext.confidence,
                            "raw_match": ext.raw_match,
                            "reason": converted.reason,
                        },
                    )
                freq_value = ext.frequency_per_day or 1.0
                daily = converted.value_mg * float(freq_value)
                self._trace(
                    ctx,
                    f"text extraction {ext.raw_match!r} -> "
                    f"{daily} mg/day",
                    actual=daily, operator="gt", expected=max_daily_mg,
                    passed=daily <= max_daily_mg,
                )
                if daily > max_daily_mg and ext.confidence in ("high", "medium"):
                    return self._violation(
                        rule,
                        "dose",
                        {
                            "drug": target,
                            "dose_mg": ext.dose_value,
                            "frequency_per_day": freq_value,
                            "dose_unit": ext.dose_unit,
                            "calculated_daily_mg": daily,
                            "max_daily_mg": max_daily_mg,
                            "source": "reply_text",
                            "confidence": ext.confidence,
                            "raw_match": ext.raw_match,
                        },
                    )
        return None

    @staticmethod
    def _drug_in_text_extractions(
        drug: str,
        extras: List[TextExtraction],
        drug_ctx: DrugContext = None,
        resolver=None,
    ) -> bool:
        for e in extras:
            if not e.drug:
                continue
            if drug_ctx is not None:
                canonical = RuleEvaluator._canonicalize_drug(e.drug, drug_ctx, resolver=resolver)
                if canonical == drug:
                    return True
            elif e.drug.strip().lower() == drug:
                return True
        return False

    def _eval_patient_state(
        self,
        rule: Rule,
        patient_state: Dict[str, Any],
        drug_ctx: DrugContext,
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        ctx = ctx or {}
        targets = self._targets(rule)
        matched_drugs = sorted(t for t in targets if t in drug_ctx.resulting_drugs)
        self._trace(
            ctx,
            f"resulting_drugs contains one of {targets}",
            actual=drug_ctx.resulting_drugs, expected=targets,
            passed=bool(matched_drugs),
        )
        if targets and not matched_drugs:
            return None

        # v4.2.0 DSL: conditions block takes precedence.
        if "conditions" in rule.parameters:
            ok = self._eval_predicates(rule.parameters["conditions"], patient_state, rule, ctx)
            if not ok:
                return None
            return self._violation(
                rule, "patient_state",
                {
                    "drug": targets[0] if targets else "",
                    "matched_drugs": matched_drugs,
                    "conditions": "all/any/not",
                },
            )
        if "range" in rule.parameters:
            if not self._eval_range_block(rule.parameters["range"], patient_state, rule, ctx):
                return None
            return self._violation(
                rule, "patient_state",
                {
                    "drug": targets[0] if targets else "",
                    "matched_drugs": matched_drugs,
                    "range": rule.parameters["range"],
                },
            )

        field_name = rule.parameters["field"]
        operator = rule.parameters["operator"]
        threshold = rule.parameters["threshold"]
        actual = patient_state.get(field_name)
        passed = self._compare(actual, operator, threshold)
        self._trace(
            ctx,
            f"patient_state.{field_name} {operator} {threshold}",
            actual=actual, operator=operator, expected=threshold,
            passed=passed,
        )
        if not passed:
            return None
        return self._violation(
            rule,
            "patient_state",
            {
                "drug": targets[0] if targets else "",
                "matched_drugs": matched_drugs,
                "field": field_name,
                "actual_value": actual,
                "operator": operator,
                "threshold": threshold,
            },
        )

    def _eval_patient_risk(
        self,
        rule: Rule,
        patient_state: Dict[str, Any],
        drug_ctx: DrugContext,
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        return None

    # ---------------------------------------------------------- risk detection

    def detect_risk_flag(
        self,
        rule: Rule,
        patient_state: Dict[str, Any],
        drug_ctx: DrugContext,
    ) -> Optional[RiskFlag]:
        if rule.type != "patient_risk":
            return None
        field_name = rule.parameters["field"]
        operator = rule.parameters["operator"]
        threshold = rule.parameters["threshold"]
        actual = patient_state.get(field_name)

        if operator == "contains":
            if isinstance(actual, (list, tuple, set)):
                ok = str(threshold) in [str(x) for x in actual]
            else:
                ok = str(threshold) in str(actual or "")
        elif operator == "in":
            ok = str(actual) in str(threshold)
        else:
            ok = self._compare(actual, operator, threshold)
        if not ok:
            return None
        applies = rule.parameters.get("applies_drugs") or []
        related = sorted(t for t in applies if t in (set(drug_ctx.resulting_drugs) | set(drug_ctx.current_drugs)))
        return RiskFlag(
            code=rule.parameters["risk_code"],
            severity=rule.severity,
            source_rule_id=rule.id,
            patient_value=actual,
            threshold=threshold,
            operator=operator,
            related_drugs=related,
        )

    def _eval_drug_drug(
        self,
        rule: Rule,
        drug_ctx: DrugContext,
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        ctx = ctx or {}
        drug_a = rule.parameters["drug_a"]
        drug_b = rule.parameters["drug_b"]
        present = drug_a in drug_ctx.resulting_drugs and drug_b in drug_ctx.resulting_drugs
        self._trace(
            ctx,
            f"both {drug_a!r} and {drug_b!r} in resulting_drugs",
            actual=present,
            passed=present,
        )
        if present:
            return self._violation(
                rule,
                "drug_drug",
                {
                    "drug_a": drug_a,
                    "drug_b": drug_b,
                    "resulting_drugs": sorted(drug_ctx.resulting_drugs),
                },
            )
        return None

    def _eval_drug_food(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        canonical_drug=None,
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        ctx = ctx or {}
        targets = self._targets(rule)
        matched_drugs = sorted(t for t in targets if t in drug_ctx.resulting_drugs)
        self._trace(
            ctx,
            f"resulting_drugs contains one of {targets}",
            actual=drug_ctx.resulting_drugs,
            expected=targets,
            passed=bool(matched_drugs),
        )
        if targets and not matched_drugs:
            return None
        keywords: List[str] = rule.parameters.get("keywords", []) or []
        kw_norm = [normalize(k) for k in keywords]
        from safety.normalizer import INVALID_FOOD_ACTION
        for fa in draft.food_advice:
            if not fa.action or fa.action == INVALID_FOOD_ACTION:
                continue
            if fa.action not in _FOOD_RECOMMEND_ACTIONS:
                continue
            text = normalize(f"{fa.food} {fa.concept} {fa.instruction}")
            matched = [k for k in kw_norm if k and k in text]
            if matched:
                self._trace(
                    ctx,
                    f"food advice matches keyword set {matched}",
                    actual=matched, passed=True,
                )
                return self._violation(
                    rule,
                    "drug_food",
                    {
                        "drug": targets[0] if targets else "",
                        "matched_drugs": matched_drugs,
                        "matched_keywords": matched,
                        "food_advice": vars(fa),
                    },
                )
        return None

    def _eval_drug_exercise(
        self,
        rule: Rule,
        patient_state: Dict[str, Any],
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        canonical_drug=None,
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        ctx = ctx or {}
        targets = self._targets(rule)
        matched_drugs = sorted(t for t in targets if t in drug_ctx.resulting_drugs)
        if targets and not matched_drugs:
            return None
        field_name = rule.parameters.get("field", "")
        operator = rule.parameters["operator"]
        threshold = rule.parameters["threshold"]
        expected_intensity = rule.parameters["exercise_intensity"]

        if field_name:
            actual_value = patient_state.get(field_name)
            passed = self._compare(actual_value, operator, threshold)
            self._trace(
                ctx,
                f"patient_state.{field_name} {operator} {threshold}",
                actual=actual_value, operator=operator, expected=threshold,
                passed=passed,
            )
            if not passed:
                return None
        else:
            actual_value = None

        from safety.normalizer import INVALID_EXERCISE_ACTION
        for ea in draft.exercise_advice:
            if not ea.action or ea.action == INVALID_EXERCISE_ACTION:
                continue
            if ea.action not in _EXERCISE_RECOMMEND_ACTIONS:
                continue
            if not ea.intensity:
                continue
            if normalize(ea.intensity) != normalize(expected_intensity):
                continue
            return self._violation(
                rule,
                "drug_exercise",
                {
                    "drug": targets[0] if targets else "",
                    "matched_drugs": matched_drugs,
                    "field": field_name,
                    "actual_value": actual_value,
                    "exercise_advice": vars(ea),
                },
            )
        return None

    # --------------------------------------------------- disease-driven rules

    def _eval_disease_food(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        risk_flags: List[RiskFlag],
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        ctx = ctx or {}
        codes_required = self._disease_codes_for_rule(rule)
        if not any(rf.code in codes_required for rf in risk_flags):
            self._trace(
                ctx,
                f"no risk_flag in {codes_required} is currently raised",
                actual=[rf.code for rf in risk_flags], expected=codes_required,
                passed=False,
            )
            return None
        keywords: List[str] = rule.parameters.get("keywords", []) or []
        kw_norm = [normalize(k) for k in keywords]
        from safety.normalizer import INVALID_FOOD_ACTION
        for fa in draft.food_advice:
            if not fa.action or fa.action == INVALID_FOOD_ACTION:
                continue
            if fa.action not in _FOOD_RECOMMEND_ACTIONS:
                continue
            text = normalize(f"{fa.food} {fa.concept} {fa.instruction}")
            matched = [k for k in kw_norm if k and k in text]
            if matched:
                codes_hit = [rf.code for rf in risk_flags if rf.code in codes_required]
                self._trace(
                    ctx,
                    f"food advice matched keywords {matched}",
                    actual=matched, passed=True,
                )
                return self._violation(
                    rule,
                    "disease_food",
                    {
                        "matched_disease_codes": codes_hit,
                        "matched_keywords": matched,
                        "food_advice": vars(fa),
                    },
                )
        return None

    def _eval_disease_exercise(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        risk_flags: List[RiskFlag],
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        ctx = ctx or {}
        codes_required = self._disease_codes_for_rule(rule)
        if not any(rf.code in codes_required for rf in risk_flags):
            return None
        expected_intensity = rule.parameters["exercise_intensity"]
        from safety.normalizer import INVALID_EXERCISE_ACTION
        for ea in draft.exercise_advice:
            if not ea.action or ea.action == INVALID_EXERCISE_ACTION:
                continue
            if ea.action not in _EXERCISE_RECOMMEND_ACTIONS:
                continue
            if not ea.intensity:
                continue
            if normalize(ea.intensity) != normalize(expected_intensity):
                continue
            codes_hit = [rf.code for rf in risk_flags if rf.code in codes_required]
            self._trace(
                ctx,
                f"exercise advice matched intensity {ea.intensity!r}",
                actual=ea.intensity, passed=True,
            )
            return self._violation(
                rule,
                "disease_exercise",
                {
                    "matched_disease_codes": codes_hit,
                    "intensity": ea.intensity,
                    "exercise_advice": vars(ea),
                },
            )
        return None

    def _disease_codes_for_rule(self, rule: Rule) -> Set[str]:
        out: Set[str] = set()
        code = rule.parameters.get("disease_code")
        if code:
            out.add(code)
        for c in rule.parameters.get("disease_codes") or []:
            out.add(c)
        for c in rule.triggers.get("risk_flags_any", []) or []:
            out.add(c)
        return out

    # ------------------------------------------------------ response compliance

    def _eval_response_compliance(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        risk_flags: List[RiskFlag],
        canonical_drug=None,
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        ctx = ctx or {}
        kind = rule.parameters["kind"]
        if kind == "forbidden_medication_action":
            return self._check_forbidden_med_action(rule, draft, drug_ctx, risk_flags, canonical_drug, ctx)
        if kind == "forbidden_food_action":
            return self._check_forbidden_food_action(rule, draft, drug_ctx, risk_flags, ctx)
        if kind == "forbidden_exercise_action":
            return self._check_forbidden_exercise_action(rule, draft, drug_ctx, risk_flags, ctx)
        if kind == "required_care_action":
            return self._check_required_care_action(rule, draft, drug_ctx, risk_flags, ctx)
        if kind == "forbidden_drug_in_response":
            return self._check_forbidden_drug_in_response(rule, draft, drug_ctx, risk_flags, canonical_drug, ctx)
        if kind == "required_stop_drug":
            return self._check_required_stop_drug(rule, draft, drug_ctx, risk_flags, canonical_drug, ctx)
        return None

    # --- helpers for response_compliance variants ---

    @staticmethod
    def _rule_targets_for_drugs(rule: Rule, drug_ctx: DrugContext) -> List[str]:
        targets = rule.parameters.get("drugs") or []
        return [d for d in targets if d in drug_ctx.resulting_drugs]

    def _check_forbidden_med_action(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        risk_flags: List[RiskFlag],
        canonical_drug=None,
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        ctx = ctx or {}
        forbidden: Set[str] = set(rule.parameters.get("forbidden_actions", []) or [])
        required_risks = set(rule.triggers.get("risk_flags_any", []) or [])
        if required_risks:
            if not any(rf.code in required_risks for rf in risk_flags):
                self._trace(
                    ctx,
                    f"required_risks {required_risks} not raised",
                    actual=[rf.code for rf in risk_flags], expected=required_risks,
                    passed=False,
                )
                return None
        targets = self._rule_targets_for_drugs(rule, drug_ctx)
        if not targets:
            return None
        for action in draft.medication_actions:
            if action.action in forbidden:
                raw = action.drug or ""
                canonical = self._canonicalize_drug(raw, drug_ctx, resolver=canonical_drug)
                if not canonical or canonical in targets:
                    self._trace(
                        ctx,
                        f"action {action.action!r} on {canonical!r} is forbidden",
                        actual=action.action, expected=f"not in {sorted(forbidden)}",
                        passed=False,
                    )
                    return self._violation(
                        rule,
                        "response_compliance",
                        {
                            "kind": "forbidden_medication_action",
                            "drug": canonical or raw,
                            "action": action.action,
                            "matched_risk_codes": [
                                rf.code for rf in risk_flags
                                if rf.code in required_risks
                            ],
                            "matched_drugs": targets,
                        },
                    )
        return None

    def _check_forbidden_food_action(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        risk_flags: List[RiskFlag],
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        forbidden: Set[str] = set(rule.parameters.get("forbidden_actions", []) or [])
        for fa in draft.food_advice:
            if fa.action in forbidden:
                return self._violation(
                    rule,
                    "response_compliance",
                    {
                        "kind": "forbidden_food_action",
                        "food": fa.food,
                        "action": fa.action,
                        "matched_risk_codes": [
                            rf.code for rf in risk_flags
                            if rf.code in (rule.triggers.get("risk_flags_any") or [])
                        ],
                    },
                )
        return None

    def _check_forbidden_exercise_action(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        risk_flags: List[RiskFlag],
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        forbidden: Set[str] = set(rule.parameters.get("forbidden_actions", []) or [])
        for ea in draft.exercise_advice:
            if ea.action in forbidden:
                return self._violation(
                    rule,
                    "response_compliance",
                    {
                        "kind": "forbidden_exercise_action",
                        "activity": ea.activity,
                        "intensity": ea.intensity,
                        "action": ea.action,
                        "matched_risk_codes": [
                            rf.code for rf in risk_flags
                            if rf.code in (rule.triggers.get("risk_flags_any") or [])
                        ],
                    },
                )
        return None

    def _check_required_care_action(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        risk_flags: List[RiskFlag],
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        required: Set[str] = set(rule.parameters.get("required_care_types", []) or [])
        present = {ca.type for ca in draft.care_actions if ca.action in {"recommend", "perform"}}
        if required.issubset(present):
            return None
        missing = sorted(required - present)
        self._trace(
            ctx,
            f"required care types missing: {missing}",
            actual=sorted(present), expected=sorted(required), passed=False,
        )
        return self._violation(
            rule,
            "response_compliance",
            {
                "kind": "required_care_action",
                "missing_care_types": missing,
                "matched_risk_codes": [
                    rf.code for rf in risk_flags
                    if rf.code in (rule.triggers.get("risk_flags_any") or [])
                ],
            },
        )

    def _check_forbidden_drug_in_response(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        risk_flags: List[RiskFlag],
        canonical_drug=None,
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        forbidden: Set[str] = set(rule.parameters.get("drugs") or [])
        for action in draft.medication_actions:
            if action.action not in {"start", "increase", "continue", "replace"}:
                continue
            canonical = self._canonicalize_drug(action.drug or "", drug_ctx, resolver=canonical_drug)
            if canonical in forbidden:
                return self._violation(
                    rule,
                    "response_compliance",
                    {
                        "kind": "forbidden_drug_in_response",
                        "drug": canonical,
                        "action": action.action,
                        "matched_risk_codes": [
                            rf.code for rf in risk_flags
                            if rf.code in (rule.triggers.get("risk_flags_any") or [])
                        ],
                    },
                )
        return None

    def _check_required_stop_drug(
        self,
        rule: Rule,
        draft: NormalizedDraft,
        drug_ctx: DrugContext,
        risk_flags: List[RiskFlag],
        canonical_drug=None,
        ctx: Optional[dict] = None,
    ) -> Optional[RuleViolation]:
        required: Set[str] = set(rule.parameters.get("drugs") or [])
        stopped = set()
        for action in draft.medication_actions:
            if action.action in {"stop", "hold"}:
                stopped.add(self._canonicalize_drug(action.drug or "", drug_ctx, resolver=canonical_drug))
        if required.issubset(stopped):
            return None
        missing = sorted(required - stopped)
        return self._violation(
            rule,
            "response_compliance",
            {
                "kind": "required_stop_drug",
                "missing_stop_drugs": missing,
                "matched_risk_codes": [
                    rf.code for rf in risk_flags
                    if rf.code in (rule.triggers.get("risk_flags_any") or [])
                ],
            },
        )