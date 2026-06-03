#!/usr/bin/env python3
"""H3.6 — ECAPA-TDNN speaker embeddings (192-d voiceprints).

Usage:
    python embed.py fileA [fileB]

Embeds fileA; if fileB is given, prints the cosine similarity between them.
High cosine (~0.5+) = same speaker; low (~<0.3) = different speakers.
"""
import sys

import numpy as np
import torch  # noqa: F401  (ensures CUDA libs load)
import torchaudio
from speechbrain.inference.speaker import EncoderClassifier

_MODEL = None


def model() -> EncoderClassifier:
    global _MODEL
    if _MODEL is None:
        _MODEL = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir="models/ecapa",
            run_opts={"device": "cuda"},
        )
    return _MODEL


def embed_file(path: str) -> np.ndarray:
    sig, sr = torchaudio.load(path)
    if sig.shape[0] > 1:
        sig = sig.mean(dim=0, keepdim=True)          # → mono
    if sr != 16000:
        sig = torchaudio.functional.resample(sig, sr, 16000)
    emb = model().encode_batch(sig).squeeze().detach().cpu().numpy()
    return emb


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main() -> None:
    a = sys.argv[1] if len(sys.argv) > 1 else "samples/jon.wav"
    ea = embed_file(a)
    print(f"{a}: dim={ea.shape[0]} norm={np.linalg.norm(ea):.2f}")
    if len(sys.argv) > 2:
        b = sys.argv[2]
        eb = embed_file(b)
        print(f"{b}: dim={eb.shape[0]} norm={np.linalg.norm(eb):.2f}")
        print(f"\ncosine({a}, {b}) = {cosine(ea, eb):.3f}")


if __name__ == "__main__":
    main()
