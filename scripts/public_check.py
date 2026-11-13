#!/usr/bin/env python3
"""Keep private data out of what this repository publishes.

Private names live outside the repository's files, in the git directory,
which git never commits or pushes:

  .git/info/denylist      names you add by hand (one per line: company,
                          hosts, people…), matched anywhere, even inside a
                          longer word; lines starting with # are notes
  .git/info/denylist-db   names and ids taken from a stack's database by
                          `make denylist <env>` (replaced each time),
                          matched as whole words
  .git/info/allowlist     names from those lists that may be published
                          because they are generic (mg-prod, Tenant Root
                          Group…)

Modes:
  public_check.py tree        check the tracked files as they are now
                              (`make public-check`)
  public_check.py push REMOTE pre-push hook (scripts/git-hooks/pre-push):
                              refuses the push when a commit to send
                                - descends from the history kept under
                                  the local `pre-public` tag,
                                - has an author or committer email that
                                  is not a GitHub no-reply address,
                                - adds a line or has a message holding a
                                  private name;
                              then runs gitleaks when it is installed.
  public_check.py denylist    read psql output (scripts/denylist.sql) on
                              stdin and write .git/info/denylist-db

Lockfiles (package-lock.json, *.lock) are skipped: they only name public
registries and packages, and gitleaks still scans them. A match prints
the file or commit and the matched name; nothing leaves this machine.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ZERO = "0" * 40
ARCHIVE_TAG = "pre-public"
NOREPLY = "@users.noreply.github.com"
LOCKFILE = re.compile(r"(^|/)(package-lock\.json|[^/]*\.lock)$")
HEX8 = re.compile(r"[0-9a-f]{8}")
GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
# Path segments of Azure ids that name nothing private.
GENERIC_SEGMENTS = {"subscriptions", "resourcegroups", "providers", "managementgroups"}


def git(*args: str, check: bool = True) -> str:
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=check)
    return out.stdout


def git_dir() -> Path:
    return Path(git("rev-parse", "--git-common-dir").strip()).resolve()


def read_list(name: str) -> set[str]:
    path = git_dir() / "info" / name
    if not path.exists():
        return set()
    lines = (ln.strip() for ln in path.read_text(encoding="utf-8").splitlines())
    return {ln.lower() for ln in lines if ln and not ln.startswith("#")}


class Patterns:
    """Hand-listed names (matched anywhere) and database names (whole words)."""

    def __init__(self) -> None:
        allow = read_list("allowlist")
        self.anywhere = sorted(read_list("denylist") - allow)
        self.words = sorted(read_list("denylist-db") - allow - set(self.anywhere))

    def __len__(self) -> int:
        return len(self.anywhere) + len(self.words)


def find(text: str, pats: Patterns) -> list[str]:
    low = text.lower()
    return [p for p in pats.anywhere if p in low] + find_words(low, pats.words)


def find_words(low: str, pats: list[str]) -> list[str]:
    """Patterns found in lower-cased ``low`` as whole words (not inside a longer word or name)."""
    hits = []
    for p in pats:
        # Word edges only matter where the pattern itself starts or ends with
        # a letter or digit ("/opt/x" matches inside "/srv/opt/x"). An id
        # prefix may be followed by the rest of the id ("abcd1234-…").
        check_before = p[0].isalnum()
        check_after = p[-1].isalnum()
        joined = "_" if HEX8.fullmatch(p) else "_-"
        i = low.find(p)
        while i != -1:
            before = low[i - 1] if i > 0 else " "
            after = low[i + len(p)] if i + len(p) < len(low) else " "
            if not (check_before and (before.isalnum() or before in "_-")) and not (
                check_after and (after.isalnum() or after in joined)
            ):
                hits.append(p)
                break
            i = low.find(p, i + 1)
    return hits


def check_tree(pats: Patterns) -> int:
    bad = 0
    for f in git("ls-files", "-z").split("\0"):
        if not f or LOCKFILE.search(f):
            continue
        for hit in find(f, pats):
            print(f"!! {f}: the file name holds '{hit}'")
            bad += 1
        if not Path(f).is_file():
            continue
        try:
            text = Path(f).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for hit in find(text, pats):
            print(f"!! {f}: {hit}")
            bad += 1
    return bad


def push_range(local: str, remote_sha: str, remote: str) -> str:
    """The commits a push sends, as git rev-list / git log arguments."""
    known = (
        remote_sha != ZERO
        and subprocess.run(
            ["git", "cat-file", "-e", f"{remote_sha}^{{commit}}"], capture_output=True
        ).returncode
        == 0
    )
    if known:
        return f"{remote_sha}..{local}"
    # A new branch, or a remote tip not fetched here: all the remote isn't known to have.
    return f"{local} --not --remotes={remote}"


def check_push(remote: str, pats: Patterns) -> int:
    bad = 0
    ranges: list[str] = []
    seen: set[str] = set()
    roots = []
    if git("rev-parse", "-q", "--verify", f"refs/tags/{ARCHIVE_TAG}", check=False).strip():
        roots = git("rev-list", "--max-parents=0", ARCHIVE_TAG).split()
    for line in sys.stdin:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if local_sha == ZERO:  # a deletion sends nothing
            continue
        if remote_ref == f"refs/tags/{ARCHIVE_TAG}":
            print(f"!! {remote_ref}: the archive tag never leaves this machine")
            bad += 1
            continue
        commit = git("rev-parse", f"{local_sha}^{{commit}}").strip()
        old = [
            r
            for r in roots
            if subprocess.run(["git", "merge-base", "--is-ancestor", r, commit]).returncode == 0
        ]
        if old:
            print(f"!! {local_ref}: holds the old history kept under '{ARCHIVE_TAG}'")
            bad += 1
            continue
        if git("cat-file", "-t", local_sha).strip() == "tag":
            tag = git("cat-file", "-p", local_sha)
            m = re.search(r"^tagger .*<([^>]*)>", tag, re.M)
            if m and not m.group(1).endswith(NOREPLY):
                print(f"!! {local_ref}: tagger email is not a GitHub no-reply address")
                bad += 1
            for hit in find(tag, pats):
                print(f"!! {local_ref}: tag message holds '{hit}'")
                bad += 1
        spec = push_range(commit, remote_sha, remote)
        if spec not in ranges:
            ranges.append(spec)
        for c in git("rev-list", *spec.split()).split():
            if c in seen:
                continue
            seen.add(c)
            short = c[:8]
            emails = git("show", "-s", "--format=%ae%n%ce", c).split()
            if any(not e.endswith(NOREPLY) for e in emails):
                print(f"!! {short}: author or committer email is not a GitHub no-reply address")
                bad += 1
            message = git("show", "-s", "--format=%B", c)
            for hit in find(message, pats):
                print(f"!! {short}: message holds '{hit}'")
                bad += 1
            # Added lines and new file names ("+++ b/<path>"); a merge against its first parent.
            patch = git(
                "show",
                "--format=",
                "-p",
                "--no-color",
                "--no-ext-diff",
                "--diff-merges=first-parent",
                c,
                "--",
                ".",
                ":!*package-lock.json",
                ":!*.lock",
            )
            added = "\n".join(ln[1:] for ln in patch.splitlines() if ln.startswith("+"))
            for hit in find(added, pats):
                print(f"!! {short}: adds '{hit}'")
                bad += 1
    if bad == 0:
        bad += run_gitleaks(ranges)
    return bad


def run_gitleaks(ranges: list[str]) -> int:
    """Secret scan of the commits being pushed only (not the local archive)."""
    exe = shutil.which("gitleaks")
    if not exe:
        print("== gitleaks not installed (brew install gitleaks): secret scan skipped")
        return 0
    for spec in ranges:
        rc = subprocess.run(
            [exe, "git", "--no-banner", "--redact", f"--log-opts={spec}", "."]
        ).returncode
        if rc != 0:
            print(f"!! gitleaks found something in {spec}: see above")
            return 1
    return 0


def write_denylist() -> int:
    """psql -At output on stdin → .git/info/denylist-db (names, ids, id prefixes)."""
    out: set[str] = set()
    for raw in sys.stdin:
        x = raw.strip()
        if len(x) < 5:
            continue
        for g in GUID.findall(x):
            out.update({g.lower(), g.lower()[:8]})
        if x.startswith("/"):
            out.update(
                s
                for s in x.split("/")
                if s.lower() not in GENERIC_SEGMENTS and not s.lower().startswith("microsoft.")
            )
        else:
            out.add(x)
            if "@" in x:  # the mail domain too
                out.add(x.rsplit("@", 1)[1])
    # A single plain word ("default", "Platform") would match everywhere.
    out = {o for o in out if len(o) >= 5 and not re.fullmatch(r"[A-Za-z]+", o)}
    path = git_dir() / "info" / "denylist-db"
    if not out:
        print(f"!! nothing read from the database (is the stack up?): {path} kept as it was")
        return 1
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(sorted(out)) + "\n", encoding="utf-8")
    print(f"== {len(out)} names and ids written to {path}")
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "tree"
    if mode == "denylist":
        return write_denylist()
    pats = Patterns()
    if not pats:
        print("!! no denylist: add names to .git/info/denylist or run `make denylist <env>`")
        return 1
    if mode == "tree":
        bad = check_tree(pats)
    elif mode == "push" and len(sys.argv) > 2:
        bad = check_push(sys.argv[2], pats)
    else:
        print(__doc__)
        return 2
    if bad:
        print(f"!! {bad} problem(s): nothing was published")
        return 1
    print(f"== clean ({len(pats)} private names checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
