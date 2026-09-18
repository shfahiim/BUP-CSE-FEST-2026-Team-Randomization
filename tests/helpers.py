from __future__ import annotations

from app.errors import InterpretationError
from app.schemas import DirectiveInterpretation, OptimizeRequest


class MappingInterpreter:
    def __init__(self, mapping: dict[str, list[dict]]) -> None:
        self.mapping = mapping

    async def interpret(
        self,
        request: OptimizeRequest,
        *,
        feedback: str | None = None,
        bypass_cache: bool = False,
    ) -> list[DirectiveInterpretation]:
        if request.scenario_id not in self.mapping:
            raise InterpretationError("no fixture")
        return [DirectiveInterpretation.model_validate(item) for item in self.mapping[request.scenario_id]]


class FailingInterpreter:
    async def interpret(
        self,
        request: OptimizeRequest,
        *,
        feedback: str | None = None,
        bypass_cache: bool = False,
    ) -> list[DirectiveInterpretation]:
        raise InterpretationError("synthetic provider outage")
