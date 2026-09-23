#!/usr/bin/env python3
"""List recent Claude Code sessions across all project dirs, newest first.

After a crash you lose track of which directories had live sessions. This scans
~/.claude/projects for top-level session transcripts (not subagents), pulls each
one's working directory and last thing you said, and prints a resume command.

Usage: python3 scan.py [count]   # count defaults to 10
"""
import datetime
import glob
import json
import os
import sys

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
session_count = int(sys.argv[1]) if len(sys.argv) > 1 else 10


def last_user_text(parsed_line):
    message = parsed_line.get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in reversed(content):
            if isinstance(part, dict) and part.get("type") == "text":
                return part["text"]
    return None


rows = []
# One level deep only -> top-level sessions; subagents live in <id>/subagents/.
for transcript in glob.glob(os.path.join(PROJECTS_DIR, "*", "[0-9a-f]*-*.jsonl")):
    working_dir = None
    last_said = None
    with open(transcript) as handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if parsed.get("cwd"):
                working_dir = parsed["cwd"]
            if parsed.get("type") == "user":
                text = last_user_text(parsed)
                if text:
                    last_said = text
    modified = datetime.datetime.fromtimestamp(os.path.getmtime(transcript))
    session_id = os.path.basename(transcript).split("-")[0]
    rows.append((modified, working_dir or "?", session_id, (last_said or "").strip().replace("\n", " ")[:80]))

rows.sort(reverse=True)
for modified, working_dir, session_id, last_said in rows[:session_count]:
    print(f"{modified:%m-%d %H:%M}  {working_dir}")
    print(f"          resume: cd {working_dir} && claude --resume {session_id}")
    print(f"          last:   {last_said!r}\n")
