"""Two-tier model routing — cheap default, escalate on repair.

Per `final_1.md` §9.1 (P1-15 / two-tier routing). Cheap stages (intent /
linker / interpreter) always use the default model; drafter and critic
escalate to the stronger model when ``repair_count > 0``.
"""

from __future__ import annotations

from typing import Literal

Stage = Literal["intent", "linker", "drafter", "critic", "interpreter"]


class ModelRouter:
    """Picks the model for a (stage, repair_count) pair.

    Stages that don't benefit from a stronger model (intent, linker,
    interpreter) always use ``DEFAULT``. Drafter + critic escalate to
    ``ESCALATED`` after the first failed repair attempt.
    """

    def __init__(
        self,
        default_model: str = "gpt-4o-mini-2024-07-18",
        escalated_model: str = "gpt-4o-2024-08-06",
    ) -> None:
        self.default_model = default_model
        self.escalated_model = escalated_model

    def pick(self, stage: Stage, repair_count: int) -> str:
        if stage in {"intent", "linker", "interpreter"}:
            return self.default_model
        if repair_count > 0:
            return self.escalated_model
        return self.default_model
