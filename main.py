#!/usr/bin/env python3
from backend.app import app
from backend.config import settings


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=False,
    )
