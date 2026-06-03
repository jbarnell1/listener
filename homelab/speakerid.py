#!/usr/bin/env python3
"""H3.6 — ECAPA speaker embeddings + a tiny JSON speaker library.

Used by enroll.py (add a named voiceprint) and identify.py (match diarized
speakers against the library). Runs in the diarization venv (~/listener-diar).
"""
import json
import os

import numpy as np
import torch
import torchaudio
from speechbrain.inference.speaker import EncoderClassifier

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "speakers.json")
MATCH_THRESHOLD = 0.40  # cosine to call it the same person (Q-S6, tunable)

_MODEL = None


def ecapa() -> EncoderClassifier:
    global _MODEL
    if _MODEL is None:
        _MODEL = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=os.path.join(HERE, "models/ecapa"),
            run_opts={"device": "cuda:0"},
        )
    return _MODEL


def _load_mono16k(path: str):
    sig, sr = torchaudio.load(path)
    if sig.shape[0] > 1:
        sig = sig.mean(dim=0, keepdim=True)            # → mono
    if sr != 16000:
        sig = torchaudio.functional.resample(sig, sr, 16000)
        sr = 16000
    return sig, sr


def embed_file(path: str) -> np.ndarray:
    sig, _ = _load_mono16k(path)
    return ecapa().encode_batch(sig).squeeze().detach().cpu().numpy()


def embed_segments(path: str, turns):
    """turns = list of (start, end) seconds for ONE speaker → 192-d embedding."""
    sig, sr = _load_mono16k(path)
    chunks = [sig[:, int(s * sr):int(e * sr)] for (s, e) in turns if e > s]
    chunks = [c for c in chunks if c.shape[1] > 0]
    if not chunks:
        return None
    return ecapa().encode_batch(torch.cat(chunks, dim=1)).squeeze().detach().cpu().numpy()


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class SpeakerDB:
    """JSON-backed name → centroid embedding store."""

    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.people = {}
        if os.path.exists(path):
            with open(path) as f:
                self.people = {k: np.array(v) for k, v in json.load(f).items()}

    def save(self) -> None:
        with open(self.path, "w") as f:
            json.dump({k: v.tolist() for k, v in self.people.items()}, f)

    def enroll(self, name: str, emb: np.ndarray) -> None:
        # running-mean centroid update as more samples of a person arrive
        self.people[name] = (self.people[name] + emb) / 2.0 if name in self.people else emb
        self.save()

    def identify(self, emb: np.ndarray, threshold: float = MATCH_THRESHOLD):
        best, score = None, -1.0
        for name, ref in self.people.items():
            c = cosine(emb, ref)
            if c > score:
                best, score = name, c
        return (best, score) if score >= threshold else (None, score)
