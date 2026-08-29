from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mabel.mcp.http import mcp_asgi_app
from mabel.shops.http import router as shops_router
from mabel.voice.webhook import router as voice_router

APP_TITLE = "Mabel"
LOCAL_OFFICE = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def create_app() -> FastAPI:
    app = FastAPI(title=APP_TITLE, docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(LOCAL_OFFICE),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(voice_router)
    app.include_router(shops_router)
    app.mount("/mcp", mcp_asgi_app())

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"ok": "mabel"}

    return app


app = create_app()
