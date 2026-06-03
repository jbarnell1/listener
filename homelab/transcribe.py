#!/usr/bin/env python3
"""H1 — transcribe a WAV with faster-whisper on the GPU.

Usage:
    python transcribe.py [audio.wav] [model_size]

Defaults: samples/jfk.wav, model "small.en".
Proves the WSL2 + CUDA + faster-whisper path works end-to-end.
"""
import ctypes
import glob
import json
import os
import sys
import time


def _preload_cuda_libs() -> None:
    """Preload the pip-installed cuBLAS/cuDNN .so files so CTranslate2 can find
    them without LD_LIBRARY_PATH set in the shell. We dlopen them RTLD_GLOBAL so
    their symbols are already resolved when ctranslate2 loads."""
    try:
        import nvidia.cublas.lib
        import nvidia.cudnn.lib
    except ImportError:
        return  # system CUDA available, or running on CPU
    for pkg in (nvidia.cublas.lib, nvidia.cudnn.lib):
        for libdir in list(pkg.__path__):  # namespace pkg → __file__ is None, use __path__
            for so in sorted(glob.glob(os.path.join(libdir, "*.so*"))):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


_preload_cuda_libs()
from faster_whisper import WhisperModel  # noqa: E402  (must follow preload)


def main() -> None:
    as_json = "--json" in sys.argv  # machine-readable mode (only JSON on stdout)
    args = [a for a in sys.argv[1:] if a != "--json"]
    audio = args[0] if len(args) > 0 else "samples/jfk.wav"
    model_size = args[1] if len(args) > 1 else "small.en"

    def log(msg):  # human prints go to stderr in json mode so stdout stays clean
        print(msg, file=sys.stderr if as_json else sys.stdout, flush=True)

    # device="cuda" + float16 → run on the 4070S at ~half the VRAM of float32.
    log(f"Loading '{model_size}' on CUDA (float16)...")
    t0 = time.time()
    model = WhisperModel(model_size, device="cuda", compute_type="float16")
    log(f"  loaded in {time.time() - t0:.1f}s")

    log(f"Transcribing: {audio}")
    t1 = time.time()
    segments, info = model.transcribe(audio, beam_size=5)
    rows = []
    for seg in segments:  # consuming the generator does the work
        rows.append({"start": round(seg.start, 2), "end": round(seg.end, 2),
                     "text": seg.text.strip()})
        if not as_json:
            print(f"  [{seg.start:6.2f} -> {seg.end:6.2f}]  {seg.text.strip()}")
    elapsed = time.time() - t1

    if as_json:
        print(json.dumps(rows))
        return
    rtf = elapsed / info.duration if info.duration else float("nan")
    print()
    print(f"language  : {info.language} (p={info.language_probability:.2f})")
    print(f"audio     : {info.duration:.1f}s")
    print(f"transcribe: {elapsed:.2f}s   RTF={rtf:.3f}   ({1 / rtf:.0f}x real-time)")


if __name__ == "__main__":
    main()
