---
name: review-pr
description: Use when the user asks to review a GitHub pull request locally without posting comments
---

# Review PR Locally

Review a GitHub pull request locally using all PR review agents in parallel. Never post comments to GitHub.

## Arguments

- `$1` — PR URL (e.g. `https://github.com/org/repo/pull/123`)

## Steps

1. **Unset GH_TOKEN** — the default token is incorrect and will cause `gh` commands to fail:
   ```bash
   unset GH_TOKEN
   ```

2. **Fetch PR metadata and save the canonical diff to a file.** Always capture `baseRefName` and `headRefName` — stacked PRs (e.g. `branch_2 → branch_1`) have a non-`main` base, and downstream agents must know this. If either command fails, troubleshoot and fix the issue (re-authenticate, check network) — do not ask the user for the diff:
   ```bash
   PR_URL="<PR_URL>"
   PR_NUMBER=$(echo "$PR_URL" | sed -E 's|.*pull/([0-9]+).*|\1|')
   META_FILE="/tmp/pr-${PR_NUMBER}-meta.json"
   DIFF_FILE="/tmp/pr-${PR_NUMBER}.diff"

   gh pr view "$PR_URL" \
     --json title,body,baseRefName,headRefName,additions,deletions,files,commits \
     > "$META_FILE"
   gh pr diff "$PR_URL" > "$DIFF_FILE"
   ```

3. **Validate that the diff matches the PR's metadata.** This catches base-branch misidentification — for a stacked PR `branch_2 → branch_1`, a wrong base produces a diff spanning `branch_2 → main` instead, which would silently corrupt the entire review. Fail loud on mismatch:
   ```bash
   BASE=$(jq -r '.baseRefName' "$META_FILE")
   HEAD=$(jq -r '.headRefName' "$META_FILE")
   EXPECTED_ADD=$(jq -r '.additions' "$META_FILE")
   EXPECTED_DEL=$(jq -r '.deletions' "$META_FILE")
   ACTUAL_ADD=$(grep -E '^\+' "$DIFF_FILE" | grep -cvE '^\+\+\+ ')
   ACTUAL_DEL=$(grep -E '^-' "$DIFF_FILE" | grep -cvE '^--- ')

   if [ "$ACTUAL_ADD" -ne "$EXPECTED_ADD" ] || [ "$ACTUAL_DEL" -ne "$EXPECTED_DEL" ]; then
       echo "ERROR: diff line counts (+$ACTUAL_ADD -$ACTUAL_DEL) do not match PR metadata (+$EXPECTED_ADD -$EXPECTED_DEL)."
       echo "Likely cause: wrong base branch was resolved — common with stacked PRs."
       echo "PR is $HEAD -> $BASE. Stop, verify on GitHub, and re-fetch with the correct URL."
       exit 1
   fi
   echo "Verified: $HEAD -> $BASE (+$EXPECTED_ADD -$EXPECTED_DEL)"
   ```
   If validation fails, STOP. Do not proceed with the review; report the mismatch to the user.

4. **Launch all PR review agents in parallel** using the Agent tool. **Every agent prompt MUST include the base ref, head ref, and the absolute diff file path**, so that no sub-agent re-derives the diff with the wrong base (e.g. `git diff main...HEAD`, which is wrong for stacked PRs). Launch all of these in a single message:

   - `pr-review-toolkit:pr-test-analyzer` — test coverage quality
   - `pr-review-toolkit:silent-failure-hunter` — silent failures and error handling
   - `pr-review-toolkit:code-simplifier` — code clarity and maintainability
   - `pr-review-toolkit:code-reviewer` — style, quality, and project guidelines
   - `pr-review-toolkit:comment-analyzer` — comment accuracy and maintainability
   - `pr-review-toolkit:type-design-analyzer` — type design, encapsulation, and invariants
   - `general-purpose` (with the Senior Code Reviewer prompt below) — high-level review against plan and coding standards
   - `general-purpose` (with the Ponytail Reviewer prompt below) — over-engineering: what to delete, stdlib/native replacements, speculative abstractions

   **Required context to embed in every agent prompt** (use literal path strings, not shell variables — sub-agents do not inherit the parent shell):
   - PR base branch: `<baseRefName>` — do NOT assume `main`; stacked PRs use other bases.
   - PR head branch: `<headRefName>`.
   - Canonical diff path: `/tmp/pr-<N>.diff` — do not re-derive the diff.
   - PR metadata JSON: `/tmp/pr-<N>-meta.json`.
   - If an agent needs to compare files in context, it must use `git diff <baseRefName>...<headRefName>`, never `git diff main...HEAD`.

   For the `general-purpose` reviewer, use this prompt (adapted from `superpowers:requesting-code-review`):

   ```
   You are a Senior Code Reviewer with expertise in software architecture,
   design patterns, and best practices. Your job is to review completed work
   against its plan or requirements and identify issues before they cascade.

   ## What Was Implemented
   {PR title + body summary}

   ## Requirements / Plan
   {PR description / linked plan, or "see PR body" if none}

   ## Branches
   Base: {baseRefName}
   Head: {headRefName}
   Note: for stacked PRs the base is NOT `main`. Do not assume `main` anywhere
   in your analysis.

   ## Diff to Review
   Canonical PR diff: {DIFF_FILE_PATH}
   Files changed: {list with additions/deletions}
   Do NOT re-derive the diff via `git diff main...HEAD` — that yields the wrong
   result for stacked PRs. If you need to inspect surrounding code, check out
   the head ref and reason against the base ref above.

   ## What to Check
   - Plan alignment: does the implementation match the stated PR objectives? Are deviations justified?
   - Code quality: separation of concerns, error handling, type safety, DRY without premature abstraction, edge cases
   - Architecture: sound design, security, integrates cleanly
   - Testing: tests verify real behaviour, edge cases, integration where it matters
   - Production readiness: migration strategy, backward compatibility, no obvious bugs
   - Project conventions from any CLAUDE.md files in scope

   ## Output
   ### Strengths
   [Specific, accurate praise]

   ### Issues
   #### Critical (Must Fix) — bugs, security, data loss, broken functionality
   #### Important (Should Fix) — architecture, missing features, error handling, test gaps
   #### Minor (Nice to Have) — style, optimisation, doc polish

   For each issue: file:line, what's wrong, why it matters, how to fix.

   ### Assessment
   **Ready to merge?** Yes | No | With fixes
   **Reasoning:** 1-2 sentences

   Read-only review. Do NOT modify code or post to GitHub.
   ```

   For the Ponytail Reviewer, use this prompt (invoke the `ponytail:ponytail-review` skill for its full ruleset, then apply it to the canonical diff):

   ```
   Invoke the ponytail:ponytail-review skill and follow it exactly. Review ONLY
   the canonical PR diff at {DIFF_FILE_PATH} for over-engineering — what to
   delete, hand-rolled stdlib, needless dependencies, speculative abstractions,
   dead flexibility.

   Base: {baseRefName}  Head: {headRefName}
   Do NOT re-derive the diff via `git diff main...HEAD` (wrong for stacked PRs).

   Output one line per finding in the skill's format, ending with
   `net: -<N> lines possible.` or `Lean already. Ship.`

   Read-only. Do NOT modify code or post to GitHub.
   ```

5. **Collect results** from all agents and present a unified review summary to the user, organised by category. Flag any issues that multiple agents independently identified.

## Important

- Do NOT post comments, approve, or request changes on the PR
- Do NOT push any code
- This is a local, read-only review
