"""
HTML page routes served via Jinja2 templates.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import run_service

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="/app/frontend/templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    run = await run_service.get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return templates.TemplateResponse(
        "run_detail.html", {"request": request, "run": run}
    )
