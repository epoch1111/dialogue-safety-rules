from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from models import DialogueDraft


class PresetDialogueAgent:
    """
    模拟 Dialogue Agent。

    它不调用真实大模型，而是从 llm_presets.json 返回预设的结构化输出。
    这样可以稳定测试规则引擎是否真的能拦截不安全回复。
    """

    def __init__(self, presets_path: str | Path):
        self.presets: Dict[str, dict] = json.loads(
            Path(presets_path).read_text(encoding="utf-8")
        )

    def generate(
        self,
        user_message: str,
        patient_state: dict,
        preset_name: str,
    ) -> DialogueDraft:
        if preset_name not in self.presets:
            raise KeyError(f"Unknown preset: {preset_name}")

        # 实际系统中，这里会把 user_message + patient_state 发给 LLM。
        # Demo 中固定返回预设输出。
        return DialogueDraft.from_dict(self.presets[preset_name])
