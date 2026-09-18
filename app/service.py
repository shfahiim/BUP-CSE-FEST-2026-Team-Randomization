from __future__ import annotations

import logging
from typing import Protocol

from app.config import Settings
from app.errors import InterpretationError, OptimizationInfeasible, ReplayError
from app.guardrails import all_no_ops, as_no_op
from app.optimizer import (
    largest_feasible_note_indices,
    minimal_infeasible_note_indices,
    solve,
)
from app.replay import replay_response
from app.schemas import DirectiveInterpretation, OptimizeRequest, OptimizeResponse

logger = logging.getLogger(__name__)


class Interpreter(Protocol):
    async def interpret(
        self,
        request: OptimizeRequest,
        *,
        feedback: str | None = None,
        bypass_cache: bool = False,
    ) -> list[DirectiveInterpretation]: ...


class OptimizationService:
    def __init__(self, interpreter: Interpreter, settings: Settings) -> None:
        self.interpreter = interpreter
        self.settings = settings

    async def optimize(self, request: OptimizeRequest) -> OptimizeResponse:
        degraded = False
        try:
            directives = await self.interpreter.interpret(request)
        except InterpretationError:
            degraded = True
            directives = all_no_ops(
                request,
                "Interpretation service was unavailable; the base schedule was used.",
            )

        try:
            result = solve(request, directives, self.settings.output_decimal_places)
        except OptimizationInfeasible:
            degraded = True
            directives, result = await self._recover_infeasible(request, directives)

        response = self._response(request, directives, result)
        violations = replay_response(request, directives, response)
        if violations:
            # One safe recovery attempt: discard semantic constraints, but never relax physics.
            if any(item.directive_type != "no_op" for item in directives):
                degraded = True
                directives = all_no_ops(
                    request,
                    "The interpreted constraints could not be verified; the base schedule was used.",
                )
                result = solve(request, directives, self.settings.output_decimal_places)
                response = self._response(request, directives, result)
                violations = replay_response(request, directives, response)
            if violations:
                raise ReplayError("serialized plan failed independent replay")

        if degraded:
            logger.warning("degraded optimization response scenario_id=%s", request.scenario_id)
        return response

    async def _recover_infeasible(self, request: OptimizeRequest, original: list[DirectiveInterpretation]):
        suspect_indices = minimal_infeasible_note_indices(request, original)
        feedback = (
            "The directives for note indices "
            f"{suspect_indices} are individually or jointly infeasible under the supplied battery and "
            "energy limits. Re-evaluate their type, hours, and numeric values while preserving notes "
            "outside this set."
        )
        candidate = original
        try:
            candidate = await self.interpreter.interpret(
                request,
                feedback=feedback,
                bypass_cache=True,
            )
            try:
                return candidate, solve(
                    request,
                    candidate,
                    self.settings.output_decimal_places,
                )
            except OptimizationInfeasible:
                pass
        except InterpretationError:
            pass

        kept = set(largest_feasible_note_indices(request, candidate))
        rewritten: list[DirectiveInterpretation] = []
        for directive in candidate:
            if directive.directive_type == "no_op" or directive.note_index in kept:
                rewritten.append(directive)
            else:
                rewritten.append(
                    as_no_op(
                        directive.note_index,
                        "The interpreted constraint was infeasible and was omitted after model retry.",
                    )
                )
        return rewritten, solve(request, rewritten, self.settings.output_decimal_places)

    @staticmethod
    def _response(request: OptimizeRequest, directives, result) -> OptimizeResponse:
        active = [item.directive_type for item in directives if item.directive_type != "no_op"]
        if active:
            strategy = ", ".join(active)
            summary = (
                f"Applied {strategy}; used available solar and shifted battery energy to minimize "
                "grid cost while returning the battery to its initial energy."
            )
        else:
            summary = (
                "No applicable operational adjustment was used; available solar and battery energy "
                "were scheduled to minimize grid cost with end-of-day battery neutrality."
            )
        return OptimizeResponse(
            scenario_id=request.scenario_id,
            directive_interpretation=directives,
            hourly_plan=result.plan,
            total_grid_kwh=result.total_grid_kwh,
            total_cost_bdt=result.total_cost_bdt,
            peak_grid_kwh=result.peak_grid_kwh,
            plan_summary=summary,
        )
