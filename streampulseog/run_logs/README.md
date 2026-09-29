# run_logs — runtime / terminal logs (streampulseOG)

Every command he runs is wrapped in `run.sh`, so the command and its full output are saved here.

```
! bash ~/job/projects/streampulseOG/run_logs/run.sh <short_name> "<command run from the repo root>"
```

Saves:
- `<timestamp>_<name>.log` : command + full output + exit code + seconds
- `INDEX.txt` : one line per run (read this first)
- `LATEST.txt` : short capped copy of the most recent run (fast to read)

Rules: one command at a time; heavy commands are run by him, not by Claude; Claude reads `INDEX.txt` and the newest log.
