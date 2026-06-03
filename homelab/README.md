# Listener Homelab Pipeline

Python services on **WSL2 (Ubuntu) + RTX 4070 Super**. **Fully local** for privacy
(ADR-016). Architecture + milestones: [`../docs/homelab/PIPELINE.md`](../docs/homelab/PIPELINE.md).

## One-time environment setup
Everything runs in WSL2. We use **`uv`** for packaging and keep the venv in WSL's
*native* filesystem (not `/mnt/c` — big CUDA wheels are slow/finicky on the Windows drive).

```bash
# 1. install uv (once)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. create the venv (native FS)
uv venv ~/listener-venv --python 3.10

# 3. install deps
cd /mnt/c/Listener/homelab
uv pip install --python ~/listener-venv/bin/python -r requirements.txt
```

> GPU note: cuBLAS/cuDNN come from the `nvidia-*` pip wheels — **no system CUDA
> toolkit needed**. The scripts `ctypes`-preload them, so you never set
> `LD_LIBRARY_PATH`. Requires the NVIDIA CUDA-on-WSL driver (check: `nvidia-smi`
> works inside WSL).

## H1 — transcription ✅
```bash
# fetch the sample once (gitignored):
wget -O samples/jfk.wav https://github.com/ggerganov/whisper.cpp/raw/master/samples/jfk.wav

cd /mnt/c/Listener/homelab
~/listener-venv/bin/python transcribe.py samples/jfk.wav small.en
```
Expected: the JFK quote, **RTF ≈ 0.05 (~20× real-time)** on GPU. Swap `small.en`
→ `large-v3` for production accuracy (one-word change).

## Milestone status (PIPELINE.md H1–H6)
- [x] **H1** — WSL2 + CUDA + faster-whisper transcribes a WAV
- [ ] H2 — FastAPI `/ingest` (verify HMAC, store, ACK)
- [ ] H3 — transcriber worker → SQLite
- [ ] H3.5 — pyannote diarization (needs HF token — Q-S7)
- [ ] H3.6 — ECAPA embeddings + speaker ID/cluster
- [ ] H4 — speaker-aware LLM intent split (Ollama; model from 12GB shortlist)
- [ ] H5 — APScheduler timed email + daily 6 AM summary
- [ ] H6 — PWA dashboard (label speakers, review profiles)
