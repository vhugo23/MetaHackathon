"""FastAPI application entry point.

Day 2 scope only: a liveness endpoint. It does not query PostgreSQL —
database readiness is proven by the container startup sequence
(architecture.md Section 15.1, README.md "Health and Database
Readiness"), not by this route.
"""

from fastapi import FastAPI

app = FastAPI(title="Meta RNE Platform")


@app.get("/health")
def get_health() -> dict[str, str]:
    return {"status": "ok"}
