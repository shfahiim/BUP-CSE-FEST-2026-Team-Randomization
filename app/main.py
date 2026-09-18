from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings
from app.errors import GridWiseError
from app.interpreter import GeminiInterpreter
from app.schemas import OptimizeRequest, OptimizeResponse
from app.service import OptimizationService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def create_app(service: OptimizationService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        interpreter: GeminiInterpreter | None = None
        if service is None:
            settings = Settings.from_env()
            interpreter = GeminiInterpreter(settings)
            application.state.optimization_service = OptimizationService(interpreter, settings)
        else:
            application.state.optimization_service = service
        yield
        if interpreter is not None:
            await interpreter.close()

    application = FastAPI(
        title="GridWise Energy Optimizer",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "location": list(error.get("loc", ())),
                "message": error.get("msg", "Invalid request"),
                "type": error.get("type", "validation_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=400, content={"error": "invalid_request", "details": errors})

    @application.exception_handler(GridWiseError)
    async def controlled_error_handler(_: Request, exc: GridWiseError) -> JSONResponse:
        logger.error("controlled internal failure type=%s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "Unable to produce a verified schedule."},
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        # Do not leak raw stack traces or request/provider data into production logs.
        logger.error("unexpected internal failure type=%s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "Unable to produce a verified schedule."},
        )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/optimize-energy", response_model=OptimizeResponse)
    async def optimize_energy(request: OptimizeRequest, raw_request: Request) -> OptimizeResponse:
        active_service: OptimizationService = raw_request.app.state.optimization_service
        return await active_service.optimize(request)

    return application


app = create_app()
