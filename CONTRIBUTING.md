# Contributing

Listener is a one-person project built for one household. Issues, questions and pull requests
are welcome, with the understanding that replies can take a few days.

## Before you open a pull request

- For anything larger than a small fix, open an issue first so we can agree on the approach.
- Keep one change per pull request.
- Design decisions are recorded as ADRs at the top of [`docs/DECISIONS.md`](docs/DECISIONS.md).
  If your change settles a design question, add one (short, dated, what was chosen and what
  was rejected). Other docs link to the ADR instead of repeating the reasoning.

## Never commit private data

This project handles recordings of real people. Do not put any of these in a commit, an issue
or a pull request: audio, transcripts, voiceprints, `listener.db`, Wi-Fi names or passwords,
device keys, `~/.listener.env`, `secrets.h`, hostnames or tailnet names, real names of people
who did not agree to it. Use the invented demo data instead (see below). The `gitleaks`
workflow scans every push.

## Working on the server (dashboard and pipeline)

The dashboard runs in a light virtual environment with no machine learning packages:

```bash
cd homelab
python3 -m venv ~/listener-web
~/listener-web/bin/pip install -r requirements-web.txt -r requirements-dev.txt

~/listener-web/bin/python -m ruff check .     # lint
~/listener-web/bin/python -m pytest           # tests: no GPU, no network, own temp database
```

To look at the UI without a wearable or a GPU, seed a throwaway database with invented data:

```bash
export LISTENER_DB=/tmp/listener-demo.db LISTENER_DATA_DIR=/tmp/listener-demo
~/listener-web/bin/python demo_seed.py
~/listener-web/bin/uvicorn app:app --port 8000     # http://localhost:8000
```

The transcription, diarization and LLM stages need an NVIDIA GPU and are not covered by the
automated tests. If you change them, say in the pull request how you checked the change (for
example `python worker.py --once some.wav`).

House rules for the server code:

- SQL lives in `db.py`; routes and tools call its functions.
- Every in-app URL in a template is prefixed with `{{ base }}` so the app works under a path
  prefix.
- Nothing may send audio or transcripts to a hosted service. Anything that leaves the machine
  must be opt-in and listed under "What leaves your home" in the README.

## Working on the firmware

- Arduino IDE or `arduino-cli`, ESP32 Arduino core 3.x, board "ESP32S3 Dev Module", USB CDC On
  Boot enabled, Flash 16MB, PSRAM "OPI PSRAM".
- Copy `secrets.example.h` to `secrets.h` in the sketch folder. `secrets.h` is gitignored.
- The `Firmware` workflow compiles the main sketches on every firmware change. A compile check
  is not a hardware test: say in the pull request whether you ran it on a board.

## Writing style

Plain, specific language. No em dashes in docs, commit messages, pull requests or UI copy.

## License

By contributing you agree that your contribution is licensed under the
[GNU AGPL v3.0](LICENSE), the license of this project.
