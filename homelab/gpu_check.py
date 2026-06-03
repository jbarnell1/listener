#!/usr/bin/env python3
"""Environment sanity check — torch+CUDA, numpy, pyannote, faster-whisper."""


def line(name, val):
    print(f"{name:<16} {val}")


def main() -> None:
    import numpy
    line("numpy", numpy.__version__)

    import torch
    line("torch", torch.__version__)
    line("torch.cuda", f"{torch.cuda.is_available()} - "
         f"{torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no GPU'}")

    try:
        import pyannote.audio
        line("pyannote.audio", pyannote.audio.__version__)
    except Exception as e:  # noqa: BLE001
        line("pyannote.audio", f"ERROR: {e}")

    try:
        import faster_whisper
        line("faster_whisper", getattr(faster_whisper, "__version__", "(installed)"))
    except Exception as e:  # noqa: BLE001
        line("faster_whisper", f"ERROR: {e}")


if __name__ == "__main__":
    main()
