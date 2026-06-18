#!/usr/bin/env python3
"""WhisperX transcribe + forced alignment → word-level timestamps.

Runs in ~/listener-wx (torch cu128 + ctranslate2, harmonized CUDA 12). The
wav2vec2 forced alignment gives tight per-word boundaries, so words can be
attributed to speakers individually (fixes segment-straddle mis-tagging).

ADR-038 — de-noise the snapshot before it reaches the LLM:
  * name-biasing: seed the decoder with known speaker names so they're spelled right
  * gate hallucinations: drop segments with high no_speech_prob / low avg_logprob /
    known boilerplate, so silence + noise don't become phantom transcripts.

Usage:
    python wx_align.py AUDIO [MODEL] [--names "Jon,Sarah"] [--nospeech 0.6]
                       [--minlogprob -1.0] [--json]
"""
import json
import re
import sys

import whisperx

DEVICE = "cuda"

# Common Whisper hallucinations on silence/music/noise (lowercased, punctuation-stripped).
_HALLUCINATIONS = {
    "thank you", "thanks for watching", "thank you for watching", "please subscribe",
    "subscribe", "like and subscribe", "you", "bye", "bye bye", "okay", "ok",
    "thank you very much", "thanks", "music", "applause", "silence",
}


def _opt(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default


def _positionals(args):
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ("--names", "--nospeech", "--minlogprob"):
            skip = True
            continue
        out.append(a)
    return out


def _is_hallucination(text):
    t = re.sub(r"[^\w\s]", "", (text or "").strip().lower()).strip()
    if not t:
        return True
    if t in _HALLUCINATIONS:
        return True
    toks = t.split()
    # pathological repetition (e.g. "you you you you") or a 1-char fragment
    if len(toks) >= 4 and len(set(toks)) == 1:
        return True
    return False


# --- warm model cache (loaded once, reused across requests in --serve mode, ADR-049) ---
_MODEL = None
_MODEL_KEY = None
_ALIGN = {}
RESULT = "\x02"          # sentinel prefix marking the JSON result line on stdout


def _load_model(model_name, names):
    """Cache the transcribe model; reload only if the model OR the name-bias changes."""
    global _MODEL, _MODEL_KEY
    key = (model_name, names or "")
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL
    asr_options = {}
    namelist = [n.strip() for n in (names or "").split(",") if n.strip()]
    if namelist:
        asr_options["initial_prompt"] = "People in this conversation: " + ", ".join(namelist) + "."
    _MODEL = whisperx.load_model(model_name, DEVICE, compute_type="float16",
                                 asr_options=asr_options or None)
    _MODEL_KEY = key
    return _MODEL


def _align_model(lang):
    if lang not in _ALIGN:
        _ALIGN[lang] = whisperx.load_align_model(language_code=lang, device=DEVICE)
    return _ALIGN[lang]


def transcribe_words(audio_path, model_name="small.en", names="", nospeech_max=0.6,
                     min_logprob=-1.0, log=lambda m: None):
    model = _load_model(model_name, names)
    audio = whisperx.load_audio(audio_path)
    log("transcribing ...")
    result = model.transcribe(audio, batch_size=16)
    lang = result.get("language", "en")
    # Gate non-speech / low-confidence / boilerplate BEFORE alignment (ADR-038).
    kept, dropped = [], 0
    for s in result.get("segments", []):
        txt = (s.get("text") or "").strip()
        nsp = s.get("no_speech_prob")
        alp = s.get("avg_logprob")
        if (not txt or _is_hallucination(txt)
                or (nsp is not None and nsp > nospeech_max)
                or (alp is not None and alp < min_logprob)):
            dropped += 1
            continue
        kept.append(s)
    log(f"kept {len(kept)} / {len(result.get('segments', []))} segments ({dropped} dropped)")
    words = []
    if kept:
        log(f"forced alignment (lang={lang}) ...")
        amodel, meta = _align_model(lang)
        aligned = whisperx.align(kept, amodel, meta, audio, DEVICE, return_char_alignments=False)
        for seg in aligned["segments"]:
            for w in seg.get("words", []):
                if w.get("start") is not None and w.get("end") is not None:
                    words.append({"word": w["word"], "start": round(w["start"], 3),
                                  "end": round(w["end"], 3)})
    return words


def serve():
    """Warm server: load models once, then process one JSON request per stdin line.
    Request:  {"audio":..,"model":..,"names":..,"nospeech":..,"minlogprob":..}
    Response: a single stdout line  <RESULT><json words list>  (logs go to stderr)."""
    print("wx serve: ready", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            words = transcribe_words(req["audio"], req.get("model", "small.en"),
                                     req.get("names", ""), float(req.get("nospeech", 0.6)),
                                     float(req.get("minlogprob", -1.0)),
                                     log=lambda m: print(m, file=sys.stderr, flush=True))
            out = json.dumps(words)
        except Exception as e:  # noqa: BLE001 — report, keep serving
            out = json.dumps({"error": str(e)})
            print(f"wx serve error: {e}", file=sys.stderr, flush=True)
        sys.stdout.write(RESULT + out + "\n")
        sys.stdout.flush()


def main():
    as_json = "--json" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--json"]
    names = _opt(args, "--names", "") or ""
    nospeech_max = float(_opt(args, "--nospeech", "0.6"))
    min_logprob = float(_opt(args, "--minlogprob", "-1.0"))
    pos = _positionals(args)
    audio_path = pos[0] if pos else "samples/two.wav"
    model_name = pos[1] if len(pos) > 1 else "small.en"

    def log(m):
        print(m, file=sys.stderr if as_json else sys.stdout, flush=True)

    words = transcribe_words(audio_path, model_name, names, nospeech_max, min_logprob, log)
    if as_json:
        print(json.dumps(words))
    else:
        for w in words:
            print(f"  [{w['start']:6.2f}-{w['end']:6.2f}] {w['word']}")
        print(f"\n{len(words)} words aligned")


if __name__ == "__main__":
    serve() if "--serve" in sys.argv else main()
