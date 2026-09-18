class GridWiseError(Exception):
    """Base class for controlled application errors."""


class InterpretationError(GridWiseError):
    """No configured model produced a usable interpretation."""


class DirectiveValidationError(GridWiseError):
    """An interpretation violates deterministic guardrails."""


class OptimizationInfeasible(GridWiseError):
    """The compiled optimization model is infeasible."""


class OptimizationError(GridWiseError):
    """The solver failed for a reason other than ordinary infeasibility."""


class ReplayError(GridWiseError):
    """A generated response failed independent replay."""
