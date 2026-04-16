"""FastAPI application assembly for the backend host."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..modules.agent_session.api import router as agent_session_router
from ..modules.agent_session.service import warm_up_agent
from ..modules.files.api import router as files_router
from ..modules.ontology.api import router as ontology_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up infrastructure used by the application."""

    del app
    warm_up_agent()
    yield


def create_app() -> FastAPI:
    """Create the FastAPI application."""

    app = FastAPI(title="Ontology Studio", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(ontology_router)
    app.include_router(files_router)
    app.include_router(agent_session_router)
    return app


app = create_app()
