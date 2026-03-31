from __future__ import annotations

import base64
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import Settings
from cli_runner import (
    list_models,
    list_upscaler_ckpts,
    parse_models_table,
    resolve_cli_executable,
    run_generate_stream,
    temp_output_path,
)

__version__ = "0.1.0"

settings = Settings()
app = FastAPI(title="Draw Things CLI Bridge", version=__version__)
_started_at = time.time()


class GenerateBody(BaseModel):
    model: str = Field(..., description="Model .ckpt id, e.g. z_image_turbo_1.0_q8p.ckpt")
    prompt: str
    negative_prompt: str | None = None
    width: int | None = Field(None, ge=64, description="Multiple of 64")
    height: int | None = Field(None, ge=64, description="Multiple of 64")
    steps: int | None = None
    cfg: float | None = None
    seed: int | None = None
    """LoRA / advanced: partial JSGenerationConfiguration JSON merged by the CLI."""
    config_json: dict[str, Any] | None = None


@app.get("/health")
async def health():
    return {"ok": True, "cli": settings.cli_bin}


@app.get("/status")
async def status():
    """Fut-e a bridge + alap meta (CLI elérhetőség, uptime)."""
    cli_path = resolve_cli_executable(settings.cli_bin)
    cli_ok = shutil.which(cli_path) is not None or (
        os.path.isfile(cli_path) and os.access(cli_path, os.X_OK)
    )
    return {
        "ok": True,
        "running": True,
        "service": "drawthings_bridge",
        "version": __version__,
        "pid": os.getpid(),
        "uptime_seconds": round(time.time() - _started_at, 3),
        "listen": {"host": settings.host, "port": settings.port},
        "cli": {
            "bin": settings.cli_bin,
            "resolved_path": cli_path,
            "available": cli_ok,
        },
        "models_dir": settings.models_dir,
        "temp_dir": settings.temp_dir,
    }


@app.get("/models")
async def models(downloaded_only: bool = True):
    try:
        raw = await list_models(settings.cli_bin, settings.models_dir, downloaded_only)
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
    return {"models": parse_models_table(raw), "raw": raw}


@app.get("/upscalers")
async def upscalers(all_ckpt: bool = False):
    """
    Telepített .ckpt fájlok az upscalerhez (Models mappa): alapból csak valószínű upscaler nevek
    (esrgan, upscal, stb.). `?all_ckpt=1` = minden checkpoint a mappa gyökerében — kézi választás.
    """
    path, items, flt = list_upscaler_ckpts(settings.models_dir, all_ckpt=all_ckpt)
    return {
        "models_dir": path,
        "filter": flt,
        "all_ckpt": bool(all_ckpt),
        "count": len(items),
        "upscalers": items,
        "hint": (
            "Állítsd be a Pipe **UPSCALER_CKPT** mezőt egy `id` / `file` értékre. "
            "Üres lista: nincs ilyen nevű .ckpt, vagy más a Models útvonal (bridge **models_dir** / DRAWTHINGS_MODELS_DIR)."
        ),
    }


@app.post("/generate")
async def generate(body: GenerateBody):
    out = temp_output_path(settings.temp_dir)
    cfg_str = json.dumps(body.config_json) if body.config_json else None
    last_progress = None
    try:
        async for item in run_generate_stream(
            cli_bin=settings.cli_bin,
            models_dir=settings.models_dir,
            model=body.model,
            prompt=body.prompt,
            output_path=out,
            negative_prompt=body.negative_prompt,
            width=body.width,
            height=body.height,
            steps=body.steps,
            cfg=body.cfg,
            seed=body.seed,
            config_json=cfg_str,
        ):
            if isinstance(item, int):
                break
            last_progress = item
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e

    data = Path(out).read_bytes()
    b64 = base64.standard_b64encode(data).decode("ascii")
    return {
        "output_path": out,
        "progress": {
            "current": last_progress.current if last_progress else None,
            "total": last_progress.total if last_progress else None,
            "percent": last_progress.percent if last_progress else None,
        },
        "image_base64": b64,
    }


@app.post("/generate/stream")
async def generate_stream(body: GenerateBody):
    """Server-Sent Events: `event: progress` lines, then `event: done` with base64 image."""

    async def gen():
        # Azonnali bájt — curl -N így rögtön mutat valamit; proxyk is flush-elnek.
        yield ": stream\n\n"
        out = temp_output_path(settings.temp_dir)
        cfg_str = json.dumps(body.config_json) if body.config_json else None
        try:
            async for item in run_generate_stream(
                cli_bin=settings.cli_bin,
                models_dir=settings.models_dir,
                model=body.model,
                prompt=body.prompt,
                output_path=out,
                negative_prompt=body.negative_prompt,
                width=body.width,
                height=body.height,
                steps=body.steps,
                cfg=body.cfg,
                seed=body.seed,
                config_json=cfg_str,
            ):
                if isinstance(item, int):
                    data = Path(out).read_bytes()
                    b64 = base64.standard_b64encode(data).decode("ascii")
                    done = json.dumps({"image_base64": b64, "output_path": out})
                    yield f"event: done\ndata: {done}\n\n"
                    return
                payload = json.dumps(
                    {
                        "current": item.current,
                        "total": item.total,
                        "percent": item.percent,
                        "line": item.line[:240],
                    }
                )
                yield f"event: progress\ndata: {payload}\n\n"
        except RuntimeError as e:
            payload = json.dumps({"error": str(e)})
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
