# Scheduling & Timed Delivery

Answers "how does the 7 PM trash email actually fire?" Timeliness is a scheduling
problem, not a connectivity problem (see ADR-006).

## Engine: APScheduler with a SQLite job store
- `BackgroundScheduler` (or `AsyncIOScheduler` inside the FastAPI app) with
  `SQLAlchemyJobStore(url="sqlite:///listener.db")`.
- Jobs **persist across restarts** — a reboot at 5 PM still fires the 7 PM email.
- Supports both **one-off** (`date` trigger) and **recurring** (`cron` trigger).

## Two tiers
### Tier SOON — timed one-off jobs
When the intent splitter emits a `SOON` intent with `due_at`:
1. Insert/Update `intents` row → `status = scheduled`.
2. `scheduler.add_job(send_action_email, 'date', run_date=due_at, args=[intent_id],
   id=f"intent-{id}", replace_existing=True)`.
3. At fire time → render the tagged email, send, set `status = sent`.
- If `due_at` is already past or marked urgent → fire immediately.
- If `due_at` is null but tier is SOON → default heuristic (e.g. +2h, or a
  sensible time-of-day) — tune later.

### Tier LATER — daily rollup
- One recurring `cron` job at **06:00** builds the "Day Ahead" summary from
  `LATER` intents + `context` updates and emails it.

## Edits / cancellations
- The PWA / conversational editor can change `due_at` or dismiss an intent →
  `scheduler.reschedule_job` / `remove_job` keeps jobs in sync with the DB.

## Why not alternatives
- Raw `subprocess` + `sleep`: lost on reboot, no visibility, no reschedule.
- OS cron / Task Scheduler: clumsy for dynamic one-off jobs; less portable
  (also ties us to Q-S1 OS choice).
- Celery + beat: heavier infra than needed for a single-box homelab.

## Open
- Q-S1 (OS → how we daemonize: systemd unit vs Windows service / NSSM).
- Default time-of-day heuristics for SOON intents lacking an explicit time.
