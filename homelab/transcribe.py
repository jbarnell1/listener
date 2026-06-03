#!/usr/bin/env python3
"""H1 — transcribe a WAV with faster-whisper on the GPU.

Usage:
    python transcribe.py [audio.wav] [model_size]

Defaults: samples/jfk.wav, model "small.en".
Proves the WSL2 + CUDA + faster-whisper path works end-to-end.
"""
import ctypes
import glob
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
    audio = sys.argv[1] if len(sys.argv) > 1 else "samples/jfk.wav"
    model_size = sys.argv[2] if len(sys.argv) > 2 else "small.en"

    # device="cuda" + float16 → run on the 4070S at ~half the VRAM of float32,
    # with no speech-accuracy loss that matters.
    print(f"Loading '{model_size}' on CUDA (float16)...", flush=True)
    t0 = time.time()
    model = WhisperModel(model_size, device="cuda", compute_type="float16")
    print(f"  loaded in {time.time() - t0:.1f}s")

    print(f"Transcribing: {audio}", flush=True)
    t1 = time.time()
    # transcribe() returns a lazy generator of segments + an info object;
    # consuming the generator is what actually does the work.
    segments, info = model.transcribe(audio, beam_size=5)
    for seg in segments:
        print(f"  [{seg.start:6.2f} -> {seg.end:6.2f}]  {seg.text.strip()}")
    elapsed = time.time() - t1

    rtf = elapsed / info.duration if info.duration else float("nan")
    print()
    print(f"language  : {info.language} (p={info.language_probability:.2f})")
    print(f"audio     : {info.duration:.1f}s")
    print(f"transcribe: {elapsed:.2f}s   RTF={rtf:.3f}   ({1 / rtf:.0f}x real-time)")


if __name__ == "__main__":
    main()
