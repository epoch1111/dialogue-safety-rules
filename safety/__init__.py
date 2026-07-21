"""Dialogue Agent v4.2.0 safety rule engine.

This package implements strict input validation + composite-index recall
+ deterministic rule evaluation. It is stdlib-only and avoids vector
databases, extra LLM calls, and complex knowledge graphs.

Public surface:

- :class:`DialogueSafetyEngine` (in ``safety.safety_engine``)
- :class:`AuditReport` and other dataclasses (in ``safety.models``)

Modules in this package:

- ``__init__.py``            public surface
- ``models.py``              dataclasses (DrugContext, RiskFlag,
                             SystemViolation, TextExtraction, AuditReport,
                             MissingContextField, RequiredContextReport,
                             InputValidationIssue, InputValidationResult,
                             StrictAuditInput, MedicationActionInput, ...)
- ``input_models.py``        strict v4.2.0 input dataclasses
- ``input_validator.py``     strict v4.2.0 InputValidator
- ``unit_converter.py``      dose-unit normalization
- ``required_context_checker.py``  per-audit required-context scan
- ``normalizer.py``          text + structured-field normalization
- ``keyword_matcher.py``     multi-trie keyword scanner (single instance)
- ``text_dose_parser.py``    finite regex-based dose extraction
- ``rule_repository.py``     load + validate + composite indexes
- ``candidate_selector.py``  per-channel union of candidate rules
- ``rule_evaluator.py``      deterministic per-type rule evaluation
- ``consistency_checker.py`` SYS001..SYS008
- ``semantic_retriever.py``  disabled stub (vector retriever interface)
- ``audit_logger.py``        writes logs/audit/<timestamp>.json
- ``safety_engine.py``       DialogueSafetyEngine.audit() entry point
"""

from safety.input_models import (
    CurrentMedicationInput,
    DialogueOutputInput,
    ExerciseAdviceInput,
    FoodAdviceInput,
    InputValidationIssue,
    InputValidationResult,
    MedicationActionInput,
    PatientStateInput,
    StrictAuditInput,
    SUPPORTED_SCHEMA_VERSIONS,
)
from safety.input_validator import InputValidator
from safety.models import (
    AuditReport,
    CareAction,
    DrugContext,
    ExerciseAdvice,
    FoodAdvice,
    MatchedEntities,
    MedicationAction,
    NormalizedDraft,
    RiskFlag,
    RuleViolation,
    SystemViolation,
    TextExtraction,
    TimingBreakdown,
)
from safety.required_context_checker import (
    MissingContextField,
    RequiredContextChecker,
    RequiredContextReport,
)
from safety.rule_repository import DrugAliasTable, Rule, RuleLoadError, RuleRepository
from safety.safety_engine import DialogueSafetyEngine
from safety.unit_converter import (
    ConvertedDose,
    UnitConversionError,
    UnsupportedUnitError,
    convert_mass_to_mg,
    daily_total_mg,
    is_finite_number,
    to_finite_float,
)

__all__ = [
    "AuditReport",
    "CareAction",
    "ConvertedDose",
    "CurrentMedicationInput",
    "DialogueOutputInput",
    "DialogueSafetyEngine",
    "DrugAliasTable",
    "DrugContext",
    "ExerciseAdvice",
    "ExerciseAdviceInput",
    "FoodAdvice",
    "FoodAdviceInput",
    "InputValidationIssue",
    "InputValidationResult",
    "InputValidator",
    "MatchedEntities",
    "MedicationAction",
    "MedicationActionInput",
    "MissingContextField",
    "PatientStateInput",
    "RequiredContextChecker",
    "RequiredContextReport",
    "RiskFlag",
    "Rule",
    "RuleLoadError",
    "RuleRepository",
    "RuleViolation",
    "StrictAuditInput",
    "SUPPORTED_SCHEMA_VERSIONS",
    "SystemViolation",
    "TextExtraction",
    "TimingBreakdown",
    "UnitConversionError",
    "UnsupportedUnitError",
    "convert_mass_to_mg",
    "daily_total_mg",
    "is_finite_number",
    "to_finite_float",
]