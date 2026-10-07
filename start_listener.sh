#!/bin/bash
# Boot entry point (C:\pwa-autostart\start-pwas.ps1). Delegates to homelab/listener.sh, the one
# launch path (ADR-056): it loads ~/.listener.env (ingest secret, Gmail), clears strays, starts
# uvicorn under --root-path /listener and waits until it answers.
exec bash /mnt/c/Listener/homelab/listener.sh up
