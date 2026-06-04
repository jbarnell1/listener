#!/usr/bin/env python3
"""WhisperX transcribe + forced alignment → word-level timestamps.

Runs in ~/listener-wx (torch cu128 + ctranslate2, harmonized CUDA 12). The
wav2vec2 forced alignment gives tight per-word boundaries, so words can be
attributed to speakers individually (fixes segment-straddle mis-tagging).

Usage:
    python wx_align.py [audio] [model] [--json]
"""
import json
import sys

import whisperx

DEVICE = "cuda"


def main():
    as_json = "--json" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--json"]
    audio_path = args[0] if args else "samples/two.wav"
    model_name = args[1] if len(args) > 1 else "small.en"

    def log(m):
        print(m, file=sys.stderr if as_json else sys.stdout, flush=True)

    log(f"loading whisperx {model_name} ...")
    model = whisperx.load_model(model_name, DEVICE, compute_type="float16")
    audio = whisperx.load_audio(audio_path)
    log("transcribing ...")
    result = model.transcribe(audio, batch_size=16)
    lang = result.get("language", "en")
    log(f"forced alignment (lang={lang}) ...")
    amodel, meta = whisperx.load_align_model(language_code=lang, device=DEVICE)
    aligned = whisperx.align(result["segments"], amodel, meta, audio, DEVICE,
                             return_char_alignments=False)

    words = []
    for seg in aligned["segments"]:
        for w in seg.get("words", []):
            if w.get("start") is not None and w.get("end") is not None:
                words.append({"word": w["word"], "start": round(w["start"], 3),
                              "end": round(w["end"], 3)})

    if as_json:
        print(json.dumps(words))
    else:
        for w in words:
            print(f"  [{w['start']:6.2f}-{w['end']:6.2f}] {w['word']}")
        print(f"\n{len(words)} words aligned")


if __name__ == "__main__":
    main()
