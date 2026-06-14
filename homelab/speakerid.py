#!/usr/bin/env python3
"""H3.6 — ECAPA speaker embeddings + a SQLite-backed speaker library.

Used by enroll.py (add a named voiceprint) and identify.py (match diarized
speakers, persistently clustering unknowns). Runs in the diarization venv.
Embeddings are stored as float32 BLOBs in the `embeddings` table (see db.py).
"""
import os

import numpy as np
import torch
import torchaudio
from speechbrain.inference.speaker import EncoderClassifier

import db

HERE = os.path.dirname(os.path.abspath(__file__))
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
        sig = sig.mean(dim=0, keepdim=True)
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


def _to_blob(emb) -> bytes:
    return np.asarray(emb, dtype=np.float32).tobytes()


def _from_blob(blob) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


class SpeakerDB:
    """SQLite-backed name → centroid-embedding store (replaces the old JSON file)."""

    def __init__(self, path: str = db.DB_PATH):
        self.conn = db.init_db(path)

    @staticmethod
    def label(sid: int, name) -> str:
        return name if name else f"Unknown_{sid}"

    def _centroids(self):
        rows = self.conn.execute(
            "SELECT s.id, s.name, e.id AS eid, e.vec, e.n_samples "
            "FROM speakers s JOIN embeddings e "
            "ON e.speaker_id = s.id AND e.is_centroid = 1"
        ).fetchall()
        return rows

    def _upsert_centroid(self, speaker_id: int, emb: np.ndarray, source: str) -> None:
        cur = self.conn.cursor()
        row = cur.execute(
            "SELECT id, vec, n_samples FROM embeddings "
            "WHERE speaker_id=? AND is_centroid=1", (speaker_id,)).fetchone()
        if row:  # running-mean update of the centroid
            n = row["n_samples"]
            new = (_from_blob(row["vec"]) * n + emb) / (n + 1)
            cur.execute("UPDATE embeddings SET vec=?, n_samples=? WHERE id=?",
                        (_to_blob(new), n + 1, row["id"]))
        else:
            cur.execute(
                "INSERT INTO embeddings(speaker_id, vec, dim, is_centroid, n_samples, source)"
                " VALUES (?,?,?,1,1,?)", (speaker_id, _to_blob(emb), int(len(emb)), source))

    def enroll(self, name: str, emb: np.ndarray) -> int:
        cur = self.conn.cursor()
        row = cur.execute("SELECT id FROM speakers WHERE name=?", (name,)).fetchone()
        if row:
            sid = row["id"]
        else:
            cur.execute("INSERT INTO speakers(name, status) VALUES (?, 'enrolled')", (name,))
            sid = cur.lastrowid
        self._upsert_centroid(sid, emb, "enroll")
        cur.execute("UPDATE speakers SET status='enrolled', updated_at=datetime('now')"
                    " WHERE id=?", (sid,))
        self.conn.commit()
        return sid

    def identify(self, emb: np.ndarray, threshold: float = None,
                 create_unknown: bool = True):
        """Return (label, score, speaker_id). Persistently clusters unknowns: a
        non-matching voice becomes a new 'unknown' speaker, so the SAME voice in a
        later recording matches the SAME Unknown_N (label it later → recognized).
        `threshold` defaults to the dashboard-tunable voice-match setting (ADR-035)."""
        if threshold is None:
            threshold = db.cfg(self.conn, "voice_match_threshold", MATCH_THRESHOLD)
        # Wearer-first (ADR-041): the device owner's voice is the closest, strongest
        # signal on a body-worn mic and the most valuable to get right (task ownership).
        # Check it against its OWN, looser gate before the general N-way match, so the
        # wearer is reliably caught even when far-field diarization is shaky.
        srow = self.conn.execute(
            "SELECT s.id, s.name, e.vec FROM speakers s JOIN embeddings e "
            "ON e.speaker_id = s.id AND e.is_centroid = 1 WHERE s.is_self = 1 LIMIT 1").fetchone()
        if srow is not None:
            c_self = cosine(emb, _from_blob(srow["vec"]))
            if c_self >= db.cfg(self.conn, "wearer_match_threshold", 0.35):
                return self.label(srow["id"], srow["name"]), c_self, srow["id"]
        best = (None, None, -1.0)  # (sid, name, score)
        for r in self._centroids():
            c = cosine(emb, _from_blob(r["vec"]))
            if c > best[2]:
                best = (r["id"], r["name"], c)
        sid, name, score = best
        if sid is not None and score >= threshold:
            return self.label(sid, name), score, sid
        if create_unknown:
            cur = self.conn.cursor()
            cur.execute("INSERT INTO speakers(status) VALUES ('unknown')")
            new_sid = cur.lastrowid
            self._upsert_centroid(new_sid, emb, "auto")
            self.conn.commit()
            return self.label(new_sid, None), score, new_sid
        return None, score, None

    def rename(self, speaker_id: int, name: str) -> None:
        """Label an Unknown (the dashboard action) → becomes recognized everywhere."""
        self.conn.execute(
            "UPDATE speakers SET name=?, status='enrolled', updated_at=datetime('now')"
            " WHERE id=?", (name, speaker_id))
        self.conn.commit()

    def list_speakers(self):
        return self.conn.execute(
            "SELECT id, name, status FROM speakers ORDER BY id").fetchall()
