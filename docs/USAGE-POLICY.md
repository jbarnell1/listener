# Listener — Usage & Consent Policy

**Status:** V1 personal-use policy. Plain-language; not legal advice. See ADR-039 for the
decision behind this posture, and the security tickets (#4 per-device keys, #5 encryption
at rest) for the matching technical work.

## What this device does
Listener is an all-day audio wearable that captures speech and turns it into your tasks,
reminders, and notes, processed **locally** on your own homelab. It can group voices it
hears into anonymous clusters and — only when **you** choose to — attach a name to a
cluster so it can attribute things to the right person.

## The consent model (the important part)
1. **Anonymous voiceprints are not identities.** Until you name a voice, it is an
   unlabeled cluster (`Unknown_N`) with no link to any real person — anonymous data, not
   personally-identifying information.

2. **Identifying a person is your action, and your responsibility.** When you move an
   `Unknown_N` to a named person (on the *Unknowns* or *People* page, or via the
   assistant), you are asserting that **you have that person's permission** to associate
   their voice and what they said with their identity. The software cannot verify this —
   the obligation and any liability for that permission rest with **you, the user**, not
   with the software or its authors.

3. **Recording laws are your responsibility.** Audio-recording and wiretapping laws vary
   by jurisdiction; some require the consent of **everyone** in a conversation. You are
   responsible for using the device lawfully where you are. When in doubt, disclose that
   you are recording, and don't record where you shouldn't.

4. **Opt-out is built in.** Any person can be set to **"don't profile"** and any person —
   and all their data (voiceprint, profile, tasks, and their lines in transcripts) — can
   be **deleted** at any time from the People page.

## How your data is handled
- **Local-first.** Audio, transcripts, and voiceprints live on your homelab; the dashboard
  is reachable only over your private tailnet. Only the signed `/ingest` and `/telemetry`
  device endpoints are publicly exposed.
- **What leaves the box:** items you let it sync go to **your** Google Calendar/Tasks; a
  nightly brief goes to **your** email. You can pause all Google sync at any time
  (Settings → shutoff valve). *(Known gap: at-rest encryption + keeping third-party-derived
  facts off Google are tracked in issues #5 and #6.)*
- **Retention:** captured audio auto-deletes after a configurable window (default 30 days);
  transcripts and profiles are kept until you delete them.

## Your acknowledgement
Before you can name (identify) any voice, the app asks you to confirm:

> *"By naming a person I confirm I have their permission to associate their voice with
> their identity, and I am responsible for using this device lawfully where I am."*

This is recorded once. You can revisit this policy any time from Settings.
