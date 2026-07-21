from __future__ import annotations

from typing import Any, Dict

from dialogue_agent import PresetDialogueAgent
from safety import DialogueSafetyEngine


class DialogueOrchestrator:
    """
    Only demonstrates this single chain:

    Patient -> Dialogue Agent -> Safety Rule Engine -> Patient
    """

    def __init__(
        self,
        dialogue_agent: PresetDialogueAgent,
        safety_engine: DialogueSafetyEngine,
    ):
        self.dialogue_agent = dialogue_agent
        self.safety_engine = safety_engine

    def handle_message(
        self,
        user_message: str,
        patient_state: Dict[str, Any],
        preset_name: str,
    ) -> Dict[str, Any]:
        # 1. Dialogue Agent first generates the candidate reply.
        draft = self.dialogue_agent.generate(
            user_message=user_message,
            patient_state=patient_state,
            preset_name=preset_name,
        )

        # 2. The raw reply must never be returned directly to the patient -
        #    it must be audited first.
        audit_result = self.safety_engine.audit(
            patient_state=patient_state,
            dialogue_output=draft.to_dict(),
        )

        # 3. Only PASS returns the raw reply.
        return {
            "user_message": user_message,
            "llm_draft": draft.to_dict(),
            "audit": audit_result.to_dict(),
            "sent_to_patient": audit_result.patient_visible_response,
            "original_llm_reply_was_sent": audit_result.decision == "PASS",
        }