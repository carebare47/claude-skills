---
name: resume-crashed-sessions
description: Use when Claude Code crashed or was killed and you need to find which directories had live sessions so you can resume them. Lists recent sessions across all projects with their cwd and a resume command.
---

# Resume crashed sessions

Run the scanner and show the user the table:

```bash
python3 ~/.claude/skills/resume-crashed-sessions/scan.py
```

It prints the most recent sessions across every project dir, newest first, each with
its working directory, a `claude --resume <id>` command, and the last thing the user said.

Notes:
- The very top row is usually the *current* session (the one running this skill) — drop it from what you present.
- Sessions clustered within a few minutes of each other are the ones that were open at crash time.
- Pass a number to widen the window: `scan.py 20`.
