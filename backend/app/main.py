from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import auth_state, pages, results, runs, stream

app = FastAPI(
    title="UI Test Platform",
    description="Upload test cases, run Playwright tests, track results.",
    version="1.0.0",
)

# ── Static assets ─────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="/app/frontend/static"), name="static")

# ── API routers ───────────────────────────────────────────────────────────────
app.include_router(runs.router)
app.include_router(results.router)
app.include_router(stream.router)
app.include_router(auth_state.router)

# ── HTML page router (last — catch-all friendly) ──────────────────────────────
app.include_router(pages.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
