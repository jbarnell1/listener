# Security

Listener records audio of people. Treat a running instance as sensitive.

## How it is meant to be run

- The dashboard has **no login of its own**. Run it only on a private network (for example a
  Tailscale tailnet) or behind a reverse proxy that authenticates. Never expose it to the
  internet.
- Only two endpoints are designed to be reachable from outside: `POST /ingest` and
  `POST /telemetry`. Both require an HMAC-SHA256 signature over the timestamp and body, with a
  5 minute replay window. Use a per-device key (Settings > Devices) so a lost board can be
  revoked on its own.
- Set `LISTENER_INGEST_SECRET` to a long random value. The built-in default
  (`dev-secret-change-me`) is for local testing only.
- Secrets live in `~/.listener.env` (mode 600) and in `firmware/*/secrets.h`. Both are
  gitignored. Never commit them.

## Known gaps

- **No encryption at rest.** `listener.db` (transcripts and voiceprints), the audio chunks and
  the database backups are plain files. Use full-disk encryption on the server, and
  `rclone crypt` if you mirror backups offsite.
- The device stores its Wi-Fi passwords and device key in firmware flash without flash
  encryption. Someone with the board and a USB cable can read them. Revoke the device key if
  a board is lost.
- The firmware does not verify the server's TLS certificate yet.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository
(**Security > Report a vulnerability**), not a public issue. This is a one-person project run
in spare time; expect a reply within about a week.

If you find a secret in the git history, report it the same way.
