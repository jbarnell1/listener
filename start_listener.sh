#!/bin/bash
# Launch Listener mounted under /listener (path-based PWA hosting; Tailscale strips the
# prefix so uvicorn --root-path tells the app its external mount point for URL generation).
cd /mnt/c/Listener/homelab
exec /home/jbarnell/listener-web/bin/uvicorn app:app --host 0.0.0.0 --port 8000 --root-path /listener
