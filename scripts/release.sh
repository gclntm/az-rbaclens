#!/bin/bash
# =============================================================================
# RELEASE SCRIPT — cut a new release (RBAC Lens)
# =============================================================================
# Called by `make release V=X.Y.Z` / `make release-dry V=X.Y.Z` (the Makefile
# recipe, docs/README-MAKE.md). Version: X.Y.Z, plain integers, no leading zeros
# (the patch is a running counter: 1.2.5 → 1.2.42).
#
# Work happens on `dev`; `main` only moves at a release, by fast-forward.
#
# Usage:
#   ./scripts/release.sh 1.2.5            # interactive, confirms first
#   ./scripts/release.sh 1.2.5 --yes      # non-interactive
#   ./scripts/release.sh 1.2.5 --dry-run  # show the planned changes, touch nothing
#
# Prerequisites:
#   - on the dev branch (--branch <name> otherwise), working tree clean
#   - docs/CHANGELOG.md has the release notes under an unversioned
#     `## YYYY-MM-DD` heading (the first one is the one released)
#   - the version isn't already in the changelog or a git tag
#
# What gets touched:
#   - docs/CHANGELOG.md        first '## YYYY-MM-DD' → '## [X.Y.Z] - YYYY-MM-DD'
#   - backend/app/version.py   __version__ = "X.Y.Z"   (reported by /api/health)
#   - backend/pyproject.toml   version = "X.Y.Z"
#   - frontend/package.json    "version": "X.Y.Z" (and twice in package-lock.json)
#
# What happens to git:
#   - one commit "release: X.Y.Z" on dev
#   - main fast-forwarded to dev, tag vX.Y.Z on it
#   - pushes dev, main and the tag in one atomic push when an `origin`
#     remote exists (the pre-push check of `make hooks` runs on it); without
#     one the release stays local and the push command is printed for later.
#     A refused push leaves the release local and says how to retry.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERSION=""
ASSUME_YES="false"
DRY_RUN="false"
SOURCE_BRANCH="dev"
RELEASE_BRANCH="main"

if [[ -t 1 ]]; then
    red()    { printf '\033[31m%s\033[0m' "$*"; }
    green()  { printf '\033[32m%s\033[0m' "$*"; }
    yellow() { printf '\033[33m%s\033[0m' "$*"; }
    cyan()   { printf '\033[36m%s\033[0m' "$*"; }
    bold()   { printf '\033[1m%s\033[0m' "$*"; }
else
    red() { printf '%s' "$*"; }; green() { printf '%s' "$*"; }; yellow() { printf '%s' "$*"; }
    cyan() { printf '%s' "$*"; }; bold() { printf '%s' "$*"; }
fi
die()  { echo "$(red "ERROR:") $*" >&2; exit 1; }
info() { echo "$(cyan "→") $*"; }
ok()   { echo "$(green "✓") $*"; }
warn() { echo "$(yellow "!") $*"; }

usage() { sed -n '2,/^# =====/p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage ;;
        --yes|-y) ASSUME_YES="true"; shift ;;
        --dry-run|-n) DRY_RUN="true"; shift ;;
        --branch) SOURCE_BRANCH="$2"; shift 2 ;;
        --release-branch) RELEASE_BRANCH="$2"; shift 2 ;;
        -*) die "Unknown flag: $1 (try --help)" ;;
        *)  [[ -z "$VERSION" ]] || die "Unexpected positional: $1"; VERSION="$1"; shift ;;
    esac
done

[[ -n "$VERSION" ]] || die "Missing version. Usage: release.sh X.Y.Z [--yes] [--dry-run]"
[[ "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || \
    die "Version must be X.Y.Z with no leading zeros (e.g. 1.0.0 or 1.2.137), got: '$VERSION'"

cd "$REPO_ROOT"
CHANGELOG=docs/CHANGELOG.md
# Settings → Changelog fetches /CHANGELOG.md; the frontend image is built from
# frontend/ only, so the release refreshes this copy.
PUBLIC_CHANGELOG=frontend/public/CHANGELOG.md
FILES=("$CHANGELOG" "$PUBLIC_CHANGELOG" backend/app/version.py backend/pyproject.toml frontend/package.json
       frontend/package-lock.json)
HAS_REMOTE="false"
git remote get-url origin >/dev/null 2>&1 && HAS_REMOTE="true"

# ---- pre-flight ---------------------------------------------------------------
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$DRY_RUN" != "true" ]]; then
    [[ "$CURRENT_BRANCH" == "$SOURCE_BRANCH" ]] || \
        die "Expected to be on '$SOURCE_BRANCH', currently on '$CURRENT_BRANCH' (--branch <name> if your workflow differs)."
    git diff --quiet && git diff --cached --quiet || die "Working tree has uncommitted changes. Commit or stash first."
    git merge-base --is-ancestor "$RELEASE_BRANCH" "$SOURCE_BRANCH" || \
        die "'$RELEASE_BRANCH' has commits that '$SOURCE_BRANCH' doesn't — a fast-forward is impossible. Merge $RELEASE_BRANCH into $SOURCE_BRANCH first."
    # The pre-push check refuses other addresses; fail before committing, not after.
    if [[ "$HAS_REMOTE" == "true" ]]; then
        [[ "$(git config user.email)" == *@users.noreply.github.com ]] || \
            die "git user.email is not a GitHub no-reply address; the push would be refused (git config user.email <id>+<login>@users.noreply.github.com)."
    fi
fi
grep -q "^## \[$VERSION\]" "$CHANGELOG" && die "Version $VERSION already appears in $CHANGELOG — pick a higher one."
git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null && die "Git tag v$VERSION already exists."
if [[ "$HAS_REMOTE" == "true" ]] && git ls-remote --tags --exit-code origin "refs/tags/v$VERSION" >/dev/null 2>&1; then
    die "Git tag v$VERSION already exists on origin."
fi

DATE_LINE=$(grep -n "^## [0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}$" "$CHANGELOG" | head -1 || true)
[[ -n "$DATE_LINE" ]] || die "No unversioned '## YYYY-MM-DD' heading in $CHANGELOG. Write the release notes under one, then re-run."
DATE_LNO="${DATE_LINE%%:*}"
DATE_TEXT="${DATE_LINE#*:}"
RELEASE_DATE="${DATE_TEXT#\#\# }"

info "Releasing version $(bold "$VERSION") (changelog section dated $RELEASE_DATE)"
info "From branch:    $SOURCE_BRANCH → $RELEASE_BRANCH (fast-forward), tag v$VERSION"
info "Remote:         $([[ "$HAS_REMOTE" == "true" ]] && git remote get-url origin || echo 'none — local release, pushes skipped')"
info "Mode:           $([[ "$DRY_RUN" == "true" ]] && echo 'DRY-RUN (no git operations)' || echo 'live')"

if [[ "$ASSUME_YES" != "true" && "$DRY_RUN" != "true" ]]; then
    echo
    echo "$(yellow 'About to:')"
    echo "  1. Rewrite line $DATE_LNO of $CHANGELOG: '$DATE_TEXT' → '## [$VERSION] - $RELEASE_DATE'"
    echo "  2. Set the version to $VERSION in backend/app/version.py, backend/pyproject.toml, frontend/package.json,"
    echo "     frontend/package-lock.json"
    echo "     and copy $CHANGELOG to $PUBLIC_CHANGELOG (Settings → Changelog)"
    echo "  3. Commit 'release: $VERSION' on $SOURCE_BRANCH"
    echo "  4. Fast-forward $RELEASE_BRANCH, tag v$VERSION"
    [[ "$HAS_REMOTE" == "true" ]] && echo "  5. Push $SOURCE_BRANCH, $RELEASE_BRANCH and v$VERSION to origin"
    echo
    read -rp "$(bold 'Continue? [y/N] ') " ans
    [[ "$ans" =~ ^[Yy]$ ]] || die "Aborted by user."
fi

rewrite_files() {
    awk -v ver="$VERSION" -v date="$RELEASE_DATE" '
        !done && /^## [0-9]{4}-[0-9]{2}-[0-9]{2}$/ { print "## [" ver "] - " date; done = 1; next }
        { print }' "$CHANGELOG" > "$CHANGELOG.new" && mv "$CHANGELOG.new" "$CHANGELOG"
    sed -i.bak -E "s/^__version__ = \"[^\"]*\"/__version__ = \"$VERSION\"/" backend/app/version.py
    # First `version = "..."` only: the [project] one, not tool settings below it.
    awk -v ver="$VERSION" '!done && /^version = "/ { print "version = \"" ver "\""; done = 1; next } { print }' \
        backend/pyproject.toml > backend/pyproject.toml.new && mv backend/pyproject.toml.new backend/pyproject.toml
    awk -v ver="$VERSION" '
        !done && /^  "version":/ { sub(/"version": "[^"]+"/, "\"version\": \"" ver "\""); done = 1 }
        { print }' frontend/package.json > frontend/package.json.new && mv frontend/package.json.new frontend/package.json
    # The lockfile repeats the version twice: at the top, and in packages[""] (the first one indented 6).
    awk -v ver="$VERSION" '
        !top && /^  "version":/ { sub(/"version": "[^"]+"/, "\"version\": \"" ver "\""); top = 1 }
        !root && /^      "version":/ { sub(/"version": "[^"]+"/, "\"version\": \"" ver "\""); root = 1 }
        { print }' frontend/package-lock.json > frontend/package-lock.json.new \
        && mv frontend/package-lock.json.new frontend/package-lock.json
    rm -f backend/app/version.py.bak
    cp "$CHANGELOG" "$PUBLIC_CHANGELOG"
}

if [[ "$DRY_RUN" == "true" ]]; then
    snap=$(mktemp -d -t release-dryrun.XXXXXX)
    for f in "${FILES[@]}"; do mkdir -p "$snap/$(dirname "$f")"; cp "$f" "$snap/$f"; done
    # Restore on any exit — including Ctrl-C or a closed pipe (make release-dry | head).
    restore() { for f in "${FILES[@]}"; do cp "$snap/$f" "$f"; done; rm -rf "$snap"; }
    trap restore EXIT
    trap 'exit 130' INT TERM PIPE
    rewrite_files
    echo; echo "$(cyan '--- planned diff ---')"
    for f in "${FILES[@]}"; do
        [[ "$f" == "$PUBLIC_CHANGELOG" ]] && continue   # a copy of $CHANGELOG
        diff -u "$snap/$f" "$f" --label "a/$f" --label "b/$f" || true
    done
    echo; ok "Dry-run complete — files restored, no git operations."
    exit 0
fi

rewrite_files
ok "Files rewritten."
git add "${FILES[@]}"
git commit -q -m "release: $VERSION

See docs/CHANGELOG.md [$VERSION] for the notes."
ok "Committed on $SOURCE_BRANCH."
git checkout -q "$RELEASE_BRANCH"
git merge -q --ff-only "$SOURCE_BRANCH"
git tag -a "v$VERSION" -m "Release $VERSION — see docs/CHANGELOG.md"
ok "Fast-forwarded $RELEASE_BRANCH, tagged v$VERSION."
git checkout -q "$SOURCE_BRANCH"
PUSH="git push --atomic origin $SOURCE_BRANCH $RELEASE_BRANCH v$VERSION"
if [[ "$HAS_REMOTE" == "true" ]]; then
    if $PUSH; then
        ok "Pushed $SOURCE_BRANCH, $RELEASE_BRANCH and v$VERSION."
    else
        warn "Push refused (see above): nothing was sent, the release is local."
        echo "    Fix the commits it names (amend on $SOURCE_BRANCH, then move $RELEASE_BRANCH and v$VERSION), then: $PUSH"
    fi
else
    warn "No 'origin' remote: release is local. Once a remote exists:"
    echo "    $PUSH"
fi
ok "On $SOURCE_BRANCH. Release $VERSION done."
