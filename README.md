# claude-skills

Personal [Claude Code](https://claude.com/claude-code) skills, packaged as a plugin.

## Install

```
/plugin marketplace add carebare47/claude-skills
/plugin install carebare47@carebare47
```

Update with `/plugin marketplace update carebare47`.

## Skills

### review-pr

```
/review-pr https://github.com/<owner>/<repo>/pull/<number>
```

Reviews a GitHub pull request locally with several review agents in parallel. It never posts to GitHub.

- Fetches the PR diff once and checks its line counts against the PR metadata. Every agent reviews that one diff, so stacked PRs are reviewed against their real base, not `main`.
- Leaves Unity editor-serialized files (`.prefab`, `.unity`, `.asset`, `.meta` and similar) out of the review diff. Add more with `--exclude GLOB`, or keep them with `--keep-editor-serialized`.
- Runs `gh` with `GH_TOKEN` unset, so `gh` uses its stored login from `gh auth login`.
- Checks whether earlier review comments were dealt with, including threads marked resolved whose code never changed.
- Checks each serious finding against the code before reporting it.

Requires `gh`, `git`, Python 3.8+, and these plugins from the official marketplace: `pr-review-toolkit`, `superpowers` and `ponytail@ponytail`.

### resume-crashed-sessions

```
/resume-crashed-sessions
```

After Claude Code crashes or is killed, lists the most recent sessions across all projects, newest first. Each one shows its working directory, a `claude --resume` command and the last thing you said. Sessions started within a few minutes of each other are the ones that were open at crash time.
