"""v4.2.0 candidate-rule selector.

Recall discipline
-----------------
Every candidate is recalled through a **named channel** that names the
index used (``drug_pair_index``, ``drug_field_index``, ``drug_food_index``,
``drug_exercise_index``, ``disease_food_index``, ``disease_exercise_index``,
``risk_compliance_index``, ``keyword_rule_index``, ``drug_only_rule_index``).
The legacy ``simple_index`` union is **not** used for production rules.

The selector also walks the patient_regimen → risk_flag channel so that
``response_compliance`` rules whose triggers reference a drug that is
present in the active regimen are still recalled even when no risk flag
is raised. This avoids losing them entirely.

Patient risk rules (patient_risk) are evaluated **separately** from the
candidate set; the engine consults them only via
``repository.patient_risk_rule_ids_for_fields``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Set

from safety.models import DrugContext, RiskFlag
from safety.rule_repository import RuleRepository


@dataclass
class RetrievalChannelEntry:
    channel: str
    key: List[str]
    rule_ids: List[str]


@dataclass
class SelectionResult:
    candidate_rule_ids: Set[str]
    channels: List[str]
    channel_trace: List[RetrievalChannelEntry] = field(default_factory=list)
    channel_sets: dict = field(default_factory=dict)


def _record(
    sets: dict,
    trace: List[RetrievalChannelEntry],
    channel: str,
    keys: List[str],
    rule_ids: Set[str],
) -> None:
    sets[channel] = sorted(rule_ids)
    if rule_ids:
        trace.append(RetrievalChannelEntry(
            channel=channel,
            key=list(keys),
            rule_ids=sorted(rule_ids),
        ))


def select_candidate_rule_ids(
    repository: RuleRepository,
    drug_ctx: DrugContext,
    risk_flags: List[RiskFlag],
    matched_keywords: Iterable[str],
    patient_fields: Iterable[str],
    text_dose_drugs: Iterable[str] = (),
    debug: bool = False,
) -> SelectionResult:
    """v4.2.0 candidate recall."""

    candidates: Set[str] = set()
    channels: List[str] = []
    sets: dict = {}
    trace: List[RetrievalChannelEntry] = []

    drugs = set(drug_ctx.resulting_drugs)
    risk_codes = {rf.code for rf in risk_flags}
    text_dose_set = {d for d in text_dose_drugs if d}

    dose_drugs = drugs | text_dose_set
    patient_field_list = list(patient_fields)

    # 1. drug_only — tightest channel. Only ``max_daily_dose`` qualifies
    # among production rules. drug_exercise/drug_drug/drug_food are
    # ruled OUT.
    drug_only = repository.drug_only_rule_ids(dose_drugs)
    if drug_only:
        _record(sets, trace, "drug_only_rule_index",
                [f"drugs={sorted(dose_drugs)}"],
                drug_only)
        channels.append("drug_only_rule_index")
        candidates |= drug_only

    # 2. drug_pair (DDI). Only between drugs in resulting_drugs.
    pairs = repository.drug_pair_rule_ids(dose_drugs)
    if pairs:
        _record(sets, trace, "drug_pair_index",
                [f"drugs={sorted(dose_drugs)}"], pairs)
        channels.append("drug_pair_index")
        candidates |= pairs

    # 3. drug + patient field composite (patient_state, max_daily_dose).
    field_names = patient_field_list + ["__dose__"]
    drug_fields = repository.drug_field_rule_ids(dose_drugs, field_names)
    if drug_fields:
        _record(sets, trace, "drug_field_index",
                [f"drugs={sorted(dose_drugs)}", f"fields={field_names}"],
                drug_fields)
        channels.append("drug_field_index")
        candidates |= drug_fields

    # 4. drug + food.
    drug_food = repository.drug_food_rule_ids(dose_drugs)
    if drug_food:
        _record(sets, trace, "drug_food_index", [f"drugs={sorted(dose_drugs)}"],
                drug_food)
        channels.append("drug_food_index")
        candidates |= drug_food

    # 5. drug + exercise.
    drug_ex = repository.drug_exercise_rule_ids(dose_drugs)
    if drug_ex:
        _record(sets, trace, "drug_exercise_index",
                [f"drugs={sorted(dose_drugs)}"], drug_ex)
        channels.append("drug_exercise_index")
        candidates |= drug_ex

    # 6. disease_food via risk_codes.
    disease_food = repository.disease_food_rule_ids(risk_codes)
    if disease_food:
        _record(sets, trace, "disease_food_index",
                [f"risk_codes={sorted(risk_codes)}"], disease_food)
        channels.append("disease_food_index")
        candidates |= disease_food

    # 7. disease_exercise via risk_codes.
    disease_ex = repository.disease_exercise_rule_ids(risk_codes)
    if disease_ex:
        _record(sets, trace, "disease_exercise_index",
                [f"risk_codes={sorted(risk_codes)}"], disease_ex)
        channels.append("disease_exercise_index")
        candidates |= disease_ex

    # 8. risk -> response_compliance.
    risk_comp = repository.risk_compliance_rule_ids(risk_codes)
    if risk_comp:
        _record(sets, trace, "risk_compliance_index",
                [f"risk_codes={sorted(risk_codes)}"], risk_comp)
        channels.append("risk_compliance_index")
        candidates |= risk_comp

    # 9. drug-keyword composite (food/exercise keyword table).
    matched_kw_list = list(matched_keywords)
    if matched_kw_list and dose_drugs:
        kw_composites: Set[str] = set()
        for d in dose_drugs:
            kw_composites |= repository.food_keyword_rule_ids(d, matched_kw_list)
        if kw_composites:
            _record(sets, trace, "keyword_rule_index",
                    [f"drugs={sorted(dose_drugs)}",
                     f"keywords={matched_kw_list}"],
                    kw_composites)
            channels.append("keyword_rule_index")
            candidates |= kw_composites

    # 10. patient_risk_field_index: separate from candidates; recorded
    # for the trace.
    pr_ids = repository.patient_risk_rule_ids_for_fields(patient_field_list)
    if pr_ids:
        _record(sets, trace, "patient_risk_field_index",
                [f"patient_fields={patient_field_list}"], pr_ids)
        channels.append("patient_risk_field_index")

    # 11. field-only.
    field_only = repository.field_rule_ids(patient_field_list)
    if field_only:
        _record(sets, trace, "field_only_rule_index",
                [f"patient_fields={patient_field_list}"], field_only)
        channels.append("field_only_rule_index")
        candidates |= field_only

    channel_trace = trace if debug else []
    return SelectionResult(
        candidate_rule_ids=candidates,
        channels=channels,
        channel_trace=channel_trace,
        channel_sets=sets if debug else {},
    )