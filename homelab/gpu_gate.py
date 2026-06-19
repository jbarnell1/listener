#!/usr/bin/env python3
"""GPU-aware processing gate (ADR-015).

The homelab GPU is shared with gaming. Before each heavy job the worker asks this
gate whether the coast is clear. We read the **Windows** `nvidia-smi.exe` over WSL
interop — the Linux WSL `nvidia-smi` can't reliably see host graphics apps.

Defer if free VRAM < FREE_MIN_MIB **or** GPU utilization > UTIL_MAX_PCT, averaged
over a few quick samples (hysteresis against momentary spikes). CPU work (ingest,
scheduler, email) never calls this — only the transcribe/diarize/LLM pipeline does.
"""
import os
import subprocess
import time

import db

# Utilization is the real "is a game rendering" signal. Free-VRAM is only an
# OOM guard: this box's baseline (Windows desktop + a resident Ollama model the
# pipeline itself uses ≈ 5–6 GB) already leaves <6 GB free with NO game running,
# so a high VRAM floor would defer forever. Keep the floor low; lean on util.
FREE_MIN_MIB = int(os.environ.get("LISTENER_GPU_FREE_MIN_MIB", "3072"))   # ~3 GB OOM guard
UTIL_MAX_PCT = int(os.environ.get("LISTENER_GPU_UTIL_MAX", "40"))         # game = sustained high util
SAMPLES = 3
SAMPLE_GAP = 1.5


def _limits():
    """Live (free_min_mib, util_max_pct) — dashboard override wins, else env/const."""
    try:
        c = db.connect()
        return (db.cfg(c, "gpu_free_min_mib", FREE_MIN_MIB),
                db.cfg(c, "gpu_util_max", UTIL_MAX_PCT))
    except Exception:  # noqa: BLE001 — never let a config read break the gate
        return FREE_MIN_MIB, UTIL_MAX_PCT

# Prefer the WSL2-native nvidia-smi: it works WITHOUT Windows interop (which can be off,
# giving "Exec format error" on the .exe) and reads the physical GPU's real utilization +
# free VRAM — so it still sees a game driving the card. Fall back to the Windows interop
# .exe where that's available instead.
_SMI_CANDIDATES = [
    "/usr/lib/wsl/lib/nvidia-smi",          # WSL2 native (no interop needed)
    "nvidia-smi",                           # whatever's on PATH
    "nvidia-smi.exe",                       # Windows via interop, if enabled
    "/mnt/c/Windows/System32/nvidia-smi.exe",
]


def _sample():
    """One (free_MiB, util_pct) reading from the GPU. Raises on failure."""
    for exe in _SMI_CANDIDATES:
        try:
            out = subprocess.run(
                [exe, "--query-gpu=memory.free,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=15)
        except OSError:                     # missing / not executable (interop off) -> next
            continue
        if out.returncode == 0 and out.stdout.strip():
            free, util = out.stdout.strip().splitlines()[0].split(",")
            return int(free.strip()), int(util.strip())
    raise RuntimeError("nvidia-smi not available")


def status(assume_loaded=False):
    """Return (clear: bool, detail: str). Fail-open (clear) if the GPU can't be
    read, so a broken gate never permanently stalls the pipeline — but log it.

    `assume_loaded=True` means our pipeline models are ALREADY resident in VRAM, so the
    free-VRAM floor is meaningless (our own models consume it) and would deadlock the gate
    (ADR-050). In that case we gate ONLY on utilization — the real "is a game rendering"
    signal — and skip the OOM floor (we're not allocating more)."""
    try:
        frees, utils = [], []
        for i in range(SAMPLES):
            f, u = _sample()
            frees.append(f); utils.append(u)
            if i < SAMPLES - 1:
                time.sleep(SAMPLE_GAP)
    except Exception as e:  # noqa: BLE001
        return True, f"gate unavailable ({e}); proceeding"
    free_min, util_max = _limits()
    free, util = min(frees), max(utils)          # worst-case across the window
    if assume_loaded:
        clear = util <= util_max                 # models warm: free-VRAM is our own; ignore it
        return clear, f"util={util}% (need ≤{util_max}%; warm — VRAM floor skipped)"
    clear = free >= free_min and util <= util_max
    detail = (f"free={free}MiB util={util}% "
              f"(need ≥{free_min}MiB & ≤{util_max}%)")
    return clear, detail


def peek():
    """Single fast sample for UI display (no averaging). (clear, detail)."""
    try:
        free, util = _sample()
    except Exception as e:  # noqa: BLE001
        return True, f"gate unavailable ({e})"
    free_min, util_max = _limits()
    clear = free >= free_min and util <= util_max
    return clear, f"free={free}MiB · util={util}%"


if __name__ == "__main__":
    ok, why = status()
    print(("CLEAR" if ok else "BUSY") + " — " + why)
