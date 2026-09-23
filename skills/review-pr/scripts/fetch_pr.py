#!/usr/bin/env python3
"""Fetch a GitHub pull request's metadata, diff and discussion, and write a review diff.

The review diff omits Unity editor-serialized files. Their YAML is rewritten wholesale by the
editor on save, so reviewing it produces findings about serialization noise rather than code.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

UNITY_EDITOR_SERIALIZED_GLOBS = (
    "*.anim",
    "*.asset",
    "*.brush",
    "*.controller",
    "*.cubemap",
    "*.flare",
    "*.fontsettings",
    "*.guiskin",
    "*.lighting",
    "*.mask",
    "*.mat",
    "*.meta",
    "*.mixer",
    "*.overrideController",
    "*.physicMaterial",
    "*.physicsMaterial2D",
    "*.playable",
    "*.prefab",
    "*.preset",
    "*.renderTexture",
    "*.shadervariants",
    "*.signal",
    "*.spriteatlas",
    "*.spriteatlasv2",
    "*.terrainlayer",
    "*.unity",
)

PULL_REQUEST_URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repository>[^/]+)/pull/(?P<number>\d+)(?:[/?#].*)?$"
)

PULL_REQUEST_METADATA_FIELDS = (
    "url,title,body,baseRefName,headRefName,additions,deletions,files,commits"
)


PULL_REQUEST_CONNECTION_QUERY_TEMPLATE = """
query($owner: String!, $repository: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repository) {
    pullRequest(number: $number) {
      CONNECTION_NAME(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { NODE_FIELDS }
      }
    }
  }
}
"""

AUTHOR_FIELDS = "author { login __typename }"

REVIEW_THREAD_FIELDS = f"""
isResolved
isOutdated
path
line
originalLine
diffSide
resolvedBy {{ login }}
comments(first: 100) {{
  pageInfo {{ hasNextPage }}
  nodes {{ {AUTHOR_FIELDS} body createdAt url diffHunk }}
}}
"""

REVIEW_FIELDS = f"{AUTHOR_FIELDS} state body submittedAt url"

CONVERSATION_COMMENT_FIELDS = f"{AUTHOR_FIELDS} body createdAt url"


class PullRequestFetchError(RuntimeError):
    pass


def run_gh_with_stored_login(gh_arguments: list[str]) -> str:
    environment_without_gh_token = {
        name: value for name, value in os.environ.items() if name != "GH_TOKEN"
    }
    completed_process = subprocess.run(
        ["gh", *gh_arguments],
        env=environment_without_gh_token,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed_process.returncode != 0:
        raise PullRequestFetchError(
            f"`gh {' '.join(gh_arguments)}` failed with exit code "
            f"{completed_process.returncode}:\n{completed_process.stderr.strip()}"
        )
    return completed_process.stdout


def fetch_all_connection_nodes(
    pull_request_location: dict[str, str], connection_name: str, node_fields: str
) -> list[dict]:
    query = PULL_REQUEST_CONNECTION_QUERY_TEMPLATE.replace(
        "CONNECTION_NAME", connection_name
    ).replace("NODE_FIELDS", node_fields)
    nodes: list[dict] = []
    page_cursor = None
    while True:
        gh_arguments = [
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={pull_request_location['owner']}",
            "-f",
            f"repository={pull_request_location['repository']}",
            "-F",
            f"number={pull_request_location['number']}",
        ]
        if page_cursor is not None:
            gh_arguments += ["-f", f"cursor={page_cursor}"]
        response = json.loads(run_gh_with_stored_login(gh_arguments))
        connection = response["data"]["repository"]["pullRequest"][connection_name]
        nodes.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            return nodes
        page_cursor = connection["pageInfo"]["endCursor"]


def describe_author(node: dict) -> dict:
    # GitHub returns a null author for deleted accounts and displays them as "ghost".
    if node["author"] is None:
        return {"author": "ghost", "author_is_bot": False}
    return {"author": node["author"]["login"], "author_is_bot": node["author"]["__typename"] == "Bot"}


def fetch_discussion(pull_request_location: dict[str, str]) -> dict:
    review_threads = []
    for thread in fetch_all_connection_nodes(
        pull_request_location, "reviewThreads", REVIEW_THREAD_FIELDS
    ):
        if thread["comments"]["pageInfo"]["hasNextPage"]:
            raise PullRequestFetchError(
                f"A review thread on {thread['path']} has more than 100 comments; "
                "fetching only some of them would misreport whether it was resolved."
            )
        thread_comments = thread["comments"]["nodes"]
        review_threads.append(
            {
                "path": thread["path"],
                "line": thread["line"],
                "original_line": thread["originalLine"],
                "diff_side": thread["diffSide"],
                "is_resolved": thread["isResolved"],
                "resolved_by": thread["resolvedBy"]["login"] if thread["resolvedBy"] else None,
                "is_outdated": thread["isOutdated"],
                "original_diff_hunk": thread_comments[0]["diffHunk"],
                "comments": [
                    {
                        **describe_author(comment),
                        "body": comment["body"],
                        "created_at": comment["createdAt"],
                        "url": comment["url"],
                    }
                    for comment in thread_comments
                ],
            }
        )
    review_summaries = [
        {
            **describe_author(review),
            "state": review["state"],
            "body": review["body"],
            "submitted_at": review["submittedAt"],
            "url": review["url"],
        }
        for review in fetch_all_connection_nodes(pull_request_location, "reviews", REVIEW_FIELDS)
        if review["body"].strip()
    ]
    conversation_comments = [
        {
            **describe_author(comment),
            "body": comment["body"],
            "created_at": comment["createdAt"],
            "url": comment["url"],
        }
        for comment in fetch_all_connection_nodes(
            pull_request_location, "comments", CONVERSATION_COMMENT_FIELDS
        )
    ]
    return {
        "review_threads": review_threads,
        "review_summaries": review_summaries,
        "conversation_comments": conversation_comments,
    }


def split_diff_into_file_sections(diff_text: str) -> list[str]:
    return [section for section in re.split(r"(?m)^(?=diff --git )", diff_text) if section]


def git_numstat_per_file_section(diff_text: str) -> list[tuple[int, int, str]]:
    """Per-file (added, deleted, new path), in diff order, parsed by git itself.

    Format of `git apply --numstat -z`: https://git-scm.com/docs/git-apply#Documentation/git-apply.txt--z
    """
    completed_process = subprocess.run(
        ["git", "apply", "--numstat", "-z", "-"],
        input=diff_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed_process.returncode != 0:
        raise PullRequestFetchError(
            f"git could not parse the PR diff:\n{completed_process.stderr.strip()}"
        )
    numstat_tokens = completed_process.stdout.split("\0")
    per_file_stats = []
    token_index = 0
    while token_index < len(numstat_tokens) - 1:
        added_text, deleted_text, path = numstat_tokens[token_index].split("\t", 2)
        is_rename_or_copy = path == ""
        if is_rename_or_copy:
            path = numstat_tokens[token_index + 2]
            token_index += 3
        else:
            token_index += 1
        is_binary = added_text == "-"
        per_file_stats.append(
            (0 if is_binary else int(added_text), 0 if is_binary else int(deleted_text), path)
        )
    return per_file_stats


def is_excluded(path: str, excluded_globs: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, glob) for glob in excluded_globs)


def parse_arguments() -> argparse.Namespace:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("pull_request_url")
    argument_parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Extra path glob to leave out of the review diff. Repeatable.",
    )
    argument_parser.add_argument(
        "--keep-editor-serialized",
        action="store_true",
        help="Keep Unity editor-serialized files in the review diff.",
    )
    return argument_parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    url_match = PULL_REQUEST_URL_PATTERN.match(arguments.pull_request_url)
    if url_match is None:
        raise PullRequestFetchError(
            f"Not a GitHub pull request URL: {arguments.pull_request_url!r}. "
            "Expected https://github.com/<owner>/<repo>/pull/<number>."
        )
    canonical_url = "https://github.com/{owner}/{repository}/pull/{number}".format(
        **url_match.groupdict()
    )

    head_commit = run_gh_with_stored_login(
        [
            "api",
            "repos/{owner}/{repository}/pulls/{number}".format(**url_match.groupdict()),
            "--jq",
            ".head.sha",
        ]
    ).strip()
    metadata_json = run_gh_with_stored_login(
        ["pr", "view", canonical_url, "--json", PULL_REQUEST_METADATA_FIELDS]
    )
    metadata = json.loads(metadata_json)
    full_diff_text = run_gh_with_stored_login(["pr", "diff", canonical_url])

    file_sections = split_diff_into_file_sections(full_diff_text)
    per_file_stats = git_numstat_per_file_section(full_diff_text)
    if len(file_sections) != len(per_file_stats):
        raise PullRequestFetchError(
            f"Split the diff into {len(file_sections)} file sections but git parsed "
            f"{len(per_file_stats)} files."
        )

    total_added = sum(added for added, _, _ in per_file_stats)
    total_deleted = sum(deleted for _, deleted, _ in per_file_stats)
    if (total_added, total_deleted) != (metadata["additions"], metadata["deletions"]):
        raise PullRequestFetchError(
            f"Diff line counts (+{total_added} -{total_deleted}) do not match PR metadata "
            f"(+{metadata['additions']} -{metadata['deletions']}) for "
            f"{metadata['headRefName']} -> {metadata['baseRefName']}. The diff does not "
            "describe this PR, or the head moved between fetches. Do not review it."
        )

    excluded_globs = list(arguments.exclude)
    if not arguments.keep_editor_serialized:
        excluded_globs.extend(UNITY_EDITOR_SERIALIZED_GLOBS)
    review_sections = []
    excluded_paths = []
    for section, (_, _, path) in zip(file_sections, per_file_stats):
        if is_excluded(path, excluded_globs):
            excluded_paths.append(path)
        else:
            review_sections.append(section)

    output_directory = Path(
        tempfile.mkdtemp(prefix="review-pr-{owner}-{repository}-{number}-".format(
            **url_match.groupdict()
        ))
    )
    metadata_path = output_directory / "metadata.json"
    review_diff_path = output_directory / "review.diff"
    discussion_path = output_directory / "discussion.json"
    metadata_path.write_text(metadata_json)
    review_diff_path.write_text("".join(review_sections))
    discussion = fetch_discussion(url_match.groupdict())
    discussion_path.write_text(json.dumps(discussion, indent=2))

    json.dump(
        {
            "pull_request_url": canonical_url,
            "base_branch": metadata["baseRefName"],
            "head_branch": metadata["headRefName"],
            "head_commit": head_commit,
            "additions": metadata["additions"],
            "deletions": metadata["deletions"],
            "metadata_path": str(metadata_path),
            "review_diff_path": str(review_diff_path),
            "reviewed_file_count": len(review_sections),
            "excluded_files": excluded_paths,
            "discussion_path": str(discussion_path),
            "review_thread_count": len(discussion["review_threads"]),
            "unresolved_review_thread_count": sum(
                not thread["is_resolved"] for thread in discussion["review_threads"]
            ),
            "review_summary_count": len(discussion["review_summaries"]),
            "conversation_comment_count": len(discussion["conversation_comments"]),
        },
        sys.stdout,
        indent=2,
    )
    print()


if __name__ == "__main__":
    try:
        main()
    except PullRequestFetchError as fetch_error:
        sys.exit(f"ERROR: {fetch_error}")
