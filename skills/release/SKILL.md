---
name: release
description: Cut a release from conventional commits on GitHub or GitLab — work out the next SemVer from history, write the changelog and version files, open the release PR/MR, then tag and publish once merged. Use when shipping a version, bumping SemVer, writing release notes, creating a tag or GitHub/GitLab release, or when asked whether a change is a major/minor/patch.
allowed-tools:
  - Bash
  - Read
  - Write
license: MIT
permissions:
  - shell
  - env
  - file_write
  - network
---

# Release — the same flow for GitHub and GitLab

```
python3 "$SKILL_DIR/scripts/release.py" plan  [--bump auto|major|minor|patch]
python3 "$SKILL_DIR/scripts/release.py" apply [--yes]
python3 "$SKILL_DIR/scripts/release.py" tag   [--yes]
```

`plan` and every run without `--yes` are read-only. Nothing is pushed by accident.

**Not for:** deploying — this stops at tag plus release notes. A repo with no commit convention has no version to compute: see the last section for the fallback.

## Portability

Works in any agent that can run shell commands. `$SKILL_DIR` is this skill's folder:

```bash
SKILL_DIR=~/.claude/skills/release     # or ~/.codex/skills/…, ~/.pi/agent/skills/…
```

## Prerequisites

- `git`, plus `gh` (GitHub) or `glab` (GitLab), authenticated. The provider is detected from
  `origin`; `gitlab` anywhere in the URL means `glab`.
- Commits written as conventional commits (`feat:`, `fix:`, `feat!:`, `chore:`). Without them the
  version falls back to `patch` and every entry lands under *Other* — which is a signal to fix
  commit hygiene, not to hand-edit a changelog.

## The three steps

1. **`plan`** — reads the last tag, the commits since it, and decides the bump:
   `!` or `BREAKING CHANGE` → major, any `feat` → minor, otherwise patch. It lists the version
   files it found (`package.json`, `pyproject.toml`, `VERSION`, `__version__`) and prints the
   changelog it would write. Preflight problems (dirty tree, detached HEAD, behind remote) are
   listed here, so you find them before anything is written.
2. **`apply --yes`** — writes the version into every file it found, prepends `CHANGELOG.md`,
   branches `release/vX.Y.Z`, commits `chore(release): vX.Y.Z`, pushes, and opens the PR/MR with
   the changelog as its body.
3. **`tag --yes`** — after the merge: annotated tag, push, and the GitHub/GitLab release with the
   same notes.

The order matters: the tag goes on the merge commit, not on the branch, so the release and the
code it ships are the same commit.

## Choices worth making deliberately

- **`--bump`** overrides the decision when history is ambiguous (a refactor that is technically
  breaking, a `feat` that is really a fix).
- **Monorepos**: the script scans a few conventional paths (`package.json`,
  `backend/pyproject.toml`, `frontend/package.json`). If your layout differs, pass `--path` and
  edit `version_files()` — it is a short list on purpose, so it never rewrites a file you did
  not mean to touch.
- **Never** force-push a release branch or move a published tag: version immutability is what
  makes rollbacks and `git describe` meaningful.

## What it will not do

- It will not tag an unmerged branch, or release from a dirty tree.
- It will not edit the changelog by hand — the notes come from commits you can inspect with
  `git log vOLD..HEAD`.
- It will not publish packages (npm/PyPI). Release notes and tags, yes; publishing stays a
  separate, explicit step.

## If the repo has no conventional commits

Either adopt them (`feat`/`fix`/`chore` in the subject) or pass `--bump` and accept that the
changelog will be a list of subjects. A release process that produces a good changelog is
mostly a commit-message habit; this skill only makes the habit cheap.
