from __future__ import annotations

import asyncio
import logging
import os
import re as _re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_UPSCALER_FILENAME_HINT = _re.compile(
    r"(?i)(upscal|esrgan|realesr|realesrgan|swinir|latent|spatial_upscal|4x_|8x_|x4_|x8_|wan_.*spatial)",
)


def resolve_models_directory(models_dir: str | None) -> Path | None:
    """
    Draw Things modellek mappája: bridge beállítás, majd env, majd macOS alapértelmezés
    (draw-things-cli ugyanígy esik vissza).
    """
    if models_dir and str(models_dir).strip():
        p = Path(models_dir).expanduser()
        if p.is_dir():
            return p
    for key in ("DRAWTHINGS_MODELS_DIR", "DRAWTHINGS_BRIDGE_MODELS_DIR"):
        v = os.environ.get(key)
        if v:
            p = Path(v).expanduser()
            if p.is_dir():
                return p
    mac = Path.home() / "Library/Containers/com.liuliu.draw-things/Data/Documents/Models"
    if mac.is_dir():
        return mac
    return None


def list_upscaler_ckpts(
    models_dir: str | None,
    *,
    all_ckpt: bool = False,
) -> tuple[str | None, list[dict[str, str]], str]:
    """
    Vissza: (models_dir útvonal vagy None, sorok [{id,name,file}], filter leírás).

    - all_ckpt=False: csak valószínű upscaler .ckpt (fájlnév alapján).
    - all_ckpt=True: minden *.ckpt a mappa gyökerében (kézi választáshoz).
    """
    root = resolve_models_directory(models_dir)
    if root is None:
        return None, [], "no_models_directory"
    paths = sorted(root.glob("*.ckpt"))
    rows: list[dict[str, str]] = []
    if all_ckpt:
        for path in paths:
            fn = path.name
            rows.append({"id": fn, "name": fn, "file": fn})
        return str(root), rows, "all_ckpt"
    for path in paths:
        fn = path.name
        if _UPSCALER_FILENAME_HINT.search(fn):
            rows.append({"id": fn, "name": fn, "file": fn})
    return str(root), rows, "upscaler_hint"


def resolve_cli_executable(cli_bin: str) -> str:
    """Interaktív shellben a PATH bővebb; LaunchAgent/uvicorn alatt előfordul, hogy csak így találjuk meg a CLI-t."""
    w = shutil.which(cli_bin)
    if w:
        return w
    for candidate in (
        "/opt/homebrew/bin/draw-things-cli",
        "/usr/local/bin/draw-things-cli",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return cli_bin


def _maybe_wrap_macos_script_for_pty(cmd: list[str]) -> list[str]:
    """
    Ha a kimenet nem TTY, sok CLI (Swift is) teljes pufferelést használ — így a pipe-on
    percekig nem jön bájt, az SSE üres. A macOS `script` pseudo-TTY-t ad, soronként ír.
    **Alapból nincs** `script` wrapper (gyorsabb, kevesebb overhead) — ugyanaz, mintha közvetlenül
    futtatnád a CLI-t. Pseudo-TTY + élő SSE progress: `DRAWTHINGS_BRIDGE_NO_SCRIPT=0`.
    """
    v = (os.environ.get("DRAWTHINGS_BRIDGE_NO_SCRIPT") or "1").strip().lower()
    if v in ("1", "true", "yes", ""):
        return cmd
    if sys.platform != "darwin":
        return cmd
    script = "/usr/bin/script"
    if not os.path.isfile(script) or not os.access(script, os.X_OK):
        return cmd
    return [script, "-q", "/dev/null"] + cmd


def env_for_subprocess() -> dict[str, str]:
    """PATH + HOME: a draw-things-cli és a modellek feloldásához."""
    env = dict(os.environ)
    env["TERM"] = "dumb"
    if not env.get("HOME"):
        env["HOME"] = os.path.expanduser("~")
    path = env.get("PATH", "")
    parts = [p for p in path.split(":") if p]
    for extra in reversed(
        ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")
    ):
        if extra not in parts:
            parts.insert(0, extra)
    env["PATH"] = ":".join(parts)
    return env

def _strip_ansi(s: str) -> str:
    """Eltávolítja a terminál szín / kurzor escape kódjait (script/TTY mód)."""
    return _re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", s)


@dataclass
class ProgressEvent:
    current: int | None = None
    total: int | None = None
    percent: float | None = None
    line: str = ""


def parse_models_table(stdout: str) -> list[dict]:
    """Parse `draw-things-cli models list` table output."""
    rows: list[dict] = []
    split_re = _re.compile(r" {2,}")
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("Models directory:"):
            continue
        if line.startswith("MODEL") or set(line) <= {"-", " "}:
            continue
        parts = [p.strip() for p in split_re.split(line) if p.strip()]
        if len(parts) < 2:
            continue
        file_id = parts[0]
        if not file_id.endswith(".ckpt"):
            continue
        name = parts[1]
        rows.append({"id": file_id, "name": name, "file": file_id})
    return rows


def _iter_progress_from_line(chunk: str) -> list[ProgressEvent]:
    """
    Egy chunkból legfeljebb egy ProgressEvent — a CLI több sorban frissít (ANSI),
    a régi regex a „3 %”-ot a Processing sorból is elcsípte → zajos, ellentmondó események.
    """
    clean = _strip_ansi(chunk)
    last_sm = None
    for m in _re.finditer(r"Sampling\.\.\.\s*(\d+)\s*/\s*(\d+)", clean):
        last_sm = m
    if last_sm is not None:
        c, t = int(last_sm.group(1)), int(last_sm.group(2))
        # Ne a teljes TTY-sor (több fázis / progress bar egy stringben) — csak egy rövid felirat az UI-nak.
        tail = clean[last_sm.end() : min(len(clean), last_sm.end() + 96)]
        pct_m = _re.search(r"(\d+)\s*%", tail)
        pct_ui = float(pct_m.group(1)) / 100.0 if pct_m else None
        pct = pct_ui if pct_ui is not None else (c / t if t else None)
        line_ui = f"Sampling... {c}/{t}"
        if pct_m:
            line_ui += f" ({pct_m.group(1)}%)"
        return [
            ProgressEvent(
                current=c,
                total=t,
                percent=pct,
                line=line_ui,
            )
        ]
    last_ph = None
    for m in _re.finditer(
        r"(Starting|Processing|Finishing)\.\.\.\s*[^\n]*?(\d+)\s*%",
        clean,
    ):
        last_ph = m
    if last_ph is not None:
        pct = float(last_ph.group(2)) / 100.0
        short = " ".join(clean[last_ph.start() : last_ph.end()].split())[:220]
        return [ProgressEvent(percent=pct, line=short)]
    return []


async def run_generate_stream(
    *,
    cli_bin: str,
    models_dir: str | None,
    model: str,
    prompt: str,
    output_path: str,
    negative_prompt: str | None = None,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    seed: int | None = None,
    config_json: str | None = None,
) -> AsyncIterator[ProgressEvent | int]:
    """
    Yields ProgressEvent updates, then the exit code (0 = success).
    """
    cmd = [cli_bin, "generate", "-m", model, "-p", prompt, "-o", output_path]
    if models_dir:
        cmd.extend(["--models-dir", models_dir])
    if negative_prompt:
        cmd.extend(["--negative-prompt", negative_prompt])
    if width is not None:
        cmd.extend(["--width", str(width)])
    if height is not None:
        cmd.extend(["--height", str(height)])
    if steps is not None:
        cmd.extend(["--steps", str(steps)])
    if cfg is not None:
        cmd.extend(["--cfg", str(cfg)])
    if seed is not None:
        cmd.extend(["--seed", str(seed)])
    if config_json:
        cmd.extend(["--config-json", config_json])

    exe = resolve_cli_executable(cli_bin)
    cmd[0] = exe
    cmd = _maybe_wrap_macos_script_for_pty(cmd)
    sub_env = env_for_subprocess()
    logger.info("draw-things-cli: %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=sub_env,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            f"draw-things-cli nem indítható (PATH?): {exe!r} — {e}"
        ) from e

    assert proc.stdout is not None
    out_chunks: list[bytes] = []
    last_prog_key: tuple[int | None, int | None, float | None] | None = None
    while True:
        block = await proc.stdout.read(4096)
        if not block:
            break
        out_chunks.append(block)
        text = block.decode("utf-8", errors="replace")
        for ev in _iter_progress_from_line(text):
            pr = round(ev.percent, 4) if ev.percent is not None else None
            key = (ev.current, ev.total, pr)
            if key == last_prog_key:
                continue
            last_prog_key = key
            yield ev

    code = await proc.wait()
    full_out = b"".join(out_chunks).decode("utf-8", errors="replace")
    if code != 0:
        tail = full_out[-4000:] if len(full_out) > 4000 else full_out
        logger.error("draw-things-cli exit %s: %s", code, tail)
        raise RuntimeError(
            f"draw-things-cli kilépett {code} kóddal. Utolsó kimenet:\n{tail}"
        )
    yield 0


async def list_models(cli_bin: str, models_dir: str | None, downloaded_only: bool) -> str:
    exe = resolve_cli_executable(cli_bin)
    cmd = [exe, "models", "list"]
    if models_dir:
        cmd.extend(["--models-dir", models_dir])
    if downloaded_only:
        cmd.append("--downloaded-only")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env_for_subprocess(),
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        msg = err.decode("utf-8", errors="replace") or out.decode("utf-8", errors="replace")
        raise RuntimeError(msg or f"models list failed with code {proc.returncode}")
    return out.decode("utf-8", errors="replace")


def temp_output_path(temp_dir: str, suffix: str = ".png") -> str:
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=suffix, dir=temp_dir)
    os.close(fd)
    return path
