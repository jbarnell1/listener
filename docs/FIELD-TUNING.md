# Week-One Field Tuning Playbook

Goal: when the hardware lands, get from "garbled noise" to "trustworthy capture" fast, and
de-risk the two unknowns that can only be tuned against **real audio** — speaker-ID accuracy
(#7) and Whisper hallucinations (#8). Everything below is adjustable live from the dashboard
(Settings → Tuning), so this is a tight capture → review → tweak loop, no code/restarts.

## 0. Before you wear it (day 0)
- [ ] **Enroll your own voice cleanly.** Record ~30–60s of just you, quiet room, normal
      distance. Self-ID is the anchor for task **ownership** ("did *I* say it?"), so make it
      solid. Confirm you show as **"me"** on the People page.
- [ ] **Turn the Google shutoff valve OFF** (Settings → Google → Pause). Week one is for
      *reviewing locally*; don't let calibration noise hit your real calendar. Flip it on
      once you trust the output.
- [ ] **Raise the triage threshold** to ~0.85 (Settings → Tuning → Auto-add confidence).
      Start high-precision/low-recall: better to send borderline items to the Review queue
      than to flood. Lower it later as you gain trust.
- [ ] Accept the **naming policy** (so you can label voices) and read it.

## 1. Capture a controlled test set (day 1–2)
Record these deliberately so you can compare knobs against known ground truth. Note roughly
what was said so you can grade the transcript.
- [ ] **Quiet 1:1** — you + one other person, normal room. (Baseline.)
- [ ] **Self only / push-to-talk** — speak a few tasks directly ("remind me to…"). This is
      your most reliable path; confirm it lands every time.
- [ ] **Noisy room** — TV/music/background chatter. (Stresses #8 hallucinations + #7 ID.)
- [ ] **Far-field** — someone across the room. (Stresses diarization + ID.)
- [ ] **Group** — 3+ people, some overlap. (Worst case for attribution.)

## 2. Grade it on the dashboard, per scenario
For each capture, open the transcript + Tasks/Review and check:
- **Hallucinations (#8):** phantom lines on silence/noise ("Thank you", "you")? Are they
  getting dropped? (Worker log shows "kept N / M segments".)
- **Names spelled right:** does it write "Erin" not "Aragon"? (Name-biasing uses enrolled
  names — enroll the people you talk to.)
- **Speaker-ID (#7):** right person attributed? Or wrong-merged / split into many Unknowns?
- **Ownership:** are *your* tasks owned by "me"? Others' tasks attributed correctly?
- **Junk rate:** how many extracted items are noise vs real? (Drives the triage threshold.)

## 3. The tuning knobs (Settings → Tuning) and when to turn them
| Symptom | Knob | Direction |
|---|---|---|
| Phantom transcripts on silence/noise (#8) | **Drop-silence aggressiveness** | lower (e.g. 0.6 → 0.4) |
| Real quiet speech getting dropped | **Drop-silence aggressiveness** / **Min transcription confidence** | raise back up |
| Same person split into many Unknowns (#7) | **Voice-match strictness** | lower (e.g. 0.40 → 0.32) |
| Different people merged into one (#7) | **Voice-match strictness** | raise (e.g. 0.40 → 0.50) |
| Calendar/Tasks flooded with junk | **Auto-add confidence** | raise |
| Real tasks stuck in Review | **Auto-add confidence** | lower |
| Near-duplicate tasks piling up | **Duplicate sensitivity** | lower |

Change **one knob at a time**, re-capture the same scenario, compare. Keep a note of what
you set and why (the Tuning card shows "changed" vs default).

## 4. Graduation criteria (when to trust it)
- [ ] Push-to-talk / "remind me" captures land **100%** of the time.
- [ ] Quiet 1:1 transcripts are accurate; your tasks are owned by "me".
- [ ] Hallucination lines on silence are gone.
- [ ] You recognize the people you talk to most (named once, recognized after).
- [ ] Junk rate in auto-added items is low enough to trust → **turn the Google valve ON**
      and **lower the triage threshold** toward the default.

## 5. Known limits to expect (don't be discouraged)
- A small body-worn mic is **wearer-voice-dominant**; far-field others may be rough. Lean on
  push-to-talk for anything important — it sidesteps every ambient-extraction unknown.
- Group/overlap diarization is the weakest link; expect to correct attributions early. Each
  correction improves the voiceprint.
- Profiles compound slowly and are non-destructive — a bad early read won't permanently
  corrupt them, and you can edit/rebuild any profile.
