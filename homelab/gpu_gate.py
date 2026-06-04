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

# Utilization is the real "is a game rendering" signal. Free-VRAM is only an
# OOM guard: this box's baseline (Windows desktop + a resident Ollama model the
# pipeline itself uses ≈ 5–6 GB) already leaves <6 GB free with NO game running,
# so a high VRAM floor would defer forever. Keep the floor low; lean on util.
FREE_MIN_MIB = int(os.environ.get("LISTENER_GPU_FREE_MIN_MIB", "3072"))   # ~3 GB OOM guard
UTIL_MAX_PCT = int(os.environ.get("LISTENER_GPU_UTIL_MAX", "40"))         # game = sustained high util
SAMPLES = 3
SAMPLE_GAP = 1.5

# Windows nvidia-smi.exe is on the WSL-appended PATH (System32). Fall back to the
# common install path if PATH lookup fails.
_SMI = "nvidia-smi.exe"
_SMI_FALLBACK = "/mnt/c/Windows/System32/nvidia-smi.exe"


def _sample():
    """One (free_MiB, util_pct) reading from the host GPU. Raises on failure."""
    for exe in (_SMI, _SMI_FALLBACK):
        try:
            out = subprocess.run(
                [exe, "--query-gpu=memory.free,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            continue
        if out.returncode == 0 and out.stdout.strip():
            free, util = out.stdout.strip().splitlines()[0].split(",")
            return int(free.strip()), int(util.strip())
    raise RuntimeError("nvidia-smi.exe not available")


def status():
    """Return (clear: bool, detail: str). Fail-open (clear) if the GPU can't be
    read, so a broken gate never permanently stalls the pipeline — but log it."""
    try:
        frees, utils = [], []
        for i in range(SAMPLES):
            f, u = _sample()
            frees.append(f); utils.append(u)
            if i < SAMPLES - 1:
                time.sleep(SAMPLE_GAP)
    except Exception as e:  # noqa: BLE001
        return True, f"gate unavailable ({e}); proceeding"
    free, util = min(frees), max(utils)          # worst-case across the window
    clear = free >= FREE_MIN_MIB and util <= UTIL_MAX_PCT
    detail = (f"free={free}MiB util={util}% "
              f"(need ≥{FREE_MIN_MIB}MiB & ≤{UTIL_MAX_PCT}%)")
    return clear, detail


def peek():
    """Single fast sample for UI display (no averaging). (clear, detail)."""
    try:
        free, util = _sample()
    except Exception as e:  # noqa: BLE001
        return True, f"gate unavailable ({e})"
    clear = free >= FREE_MIN_MIB and util <= UTIL_MAX_PCT
    return clear, f"free={free}MiB · util={util}%"


if __name__ == "__main__":
    ok, why = status()
    print(("CLEAR" if ok else "BUSY") + " — " + why)
