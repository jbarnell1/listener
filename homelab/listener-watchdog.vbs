' Listener watchdog launcher — runs the health-gated restart hidden (no console flash).
' Invoked every few minutes by the "ListenerWatchdog" Windows Scheduled Task.
CreateObject("WScript.Shell").Run "wsl.exe -d Ubuntu -e bash -lc ""/mnt/c/Listener/homelab/listener.sh watchdog""", 0, False
