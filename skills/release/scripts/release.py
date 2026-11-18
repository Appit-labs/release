#!/usr/bin/env python3
"""Cut a release from conventional commits, on GitHub or GitLab.

    release.py plan   [--bump auto|major|minor|patch] [--provider auto|github|gitlab]
    release.py apply  [--yes]        # write version+changelog, branch, commit, open PR/MR
    release.py tag    [--yes]        # after merge: tag, push, create the Release
    release.py self-test

Nothing is pushed unless --yes is passed: `plan` and every default run are read-only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CONV = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?: (?P<subject>.+)$")
GROUPS = [
    ("breaking", "Breaking changes"),
    ("feat", "Added"),
    ("fix", "Fixed"),
    ("perf", "Performance"),
    ("refactor", "Changed"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("build", "Build"),
    ("ci", "CI"),
    ("chore", "Chores"),
]


def parse_flags(argv):
    """--key=value and --key value both work; a bare --flag is True."""
    flags, args, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            if "=" in a:
                k, v = a[2:].split("=", 1)
                flags[k] = v
            elif i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                flags[a[2:]] = argv[i + 1]
                i += 1
            else:
                flags[a[2:]] = True
        else:
            args.append(a)
        i += 1
    return flags, args


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def semver(tag: str) -> tuple[int, int, int]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", tag or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def provider(root: Path) -> str:
    url = git("remote", "get-url", "origin", cwd=root, check=False)
    return "gitlab" if "gitlab" in url.lower() else "github"


def last_tag(root: Path) -> str:
    tag = git("describe", "--tags", "--abbrev=0", cwd=root, check=False)
    return tag or ""


def commits(root: Path, since: str) -> list[dict]:
    rng = f"{since}..HEAD" if since else "HEAD"
    raw = git("log", "--no-merges", "--pretty=format:%H%x09%s", rng, cwd=root, check=False)
    out = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        m = CONV.match(subject)
        out.append({
            "sha": sha[:8], "subject": subject,
            "type": (m.group("type").lower() if m else "other"),
            "scope": (m.group("scope") if m else None),
            "breaking": bool(m and m.group("breaking")) or "BREAKING CHANGE" in subject,
        })
    return out


def decide(commits_: list[dict], forced: str) -> str:
    if forced != "auto":
        return forced
    if any(c["breaking"] for c in commits_):
        return "major"
    if any(c["type"] == "feat" for c in commits_):
        return "minor"
    return "patch"


def bump(tag: str, level: str) -> str:
    major, minor, patch = semver(tag)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def changelog(commits_: list[dict], version: str) -> str:
    lines = [f"## {version}", ""]
    breaking = [c for c in commits_ if c["breaking"]]
    if breaking:
        lines += ["### Breaking changes", ""]
        lines += [f"- {c['subject']}" for c in breaking]
        lines.append("")
    for key, title in GROUPS:
        if key in ("breaking",):
            continue
        items = [c for c in commits_ if c["type"] == key and not c["breaking"]]
        if not items:
            continue
        lines += [f"### {title}", ""]
        for c in items:
            scope = f"**{c['scope']}:** " if c["scope"] else ""
            subject = re.sub(r"^[a-zA-Z]+(\([^)]+\))?!?: ", "", c["subject"])
            lines.append(f"- {scope}{subject} ({c['sha']})")
        lines.append("")
    other = [c for c in commits_ if c["type"] == "other" and not c["breaking"]]
    if other:
        lines += ["### Other", ""] + [f"- {c['subject']} ({c['sha']})" for c in other] + [""]
    return "\n".join(lines).rstrip() + "\n"


def version_files(root: Path) -> list[tuple[Path, str]]:
    """Files this repo keeps a version in, and how to rewrite them."""
    found: list[tuple[Path, str]] = []
    for name in ("package.json", "apps/web/package.json", "frontend/package.json"):
        p = root / name
        if p.exists():
            found.append((p, "json"))
    for name in ("pyproject.toml", "backend/pyproject.toml"):
        p = root / name
        if p.exists() and "version" in p.read_text(errors="replace"):
            found.append((p, "toml"))
    for name in ("VERSION", "version.txt"):
        p = root / name
        if p.exists():
            found.append((p, "plain"))
    for p in list(root.glob("*/__init__.py"))[:6]:
        if "__version__" in p.read_text(errors="replace"):
            found.append((p, "dunder"))
    return found


def write_version(path: Path, kind: str, version: str) -> bool:
    text = path.read_text()
    if kind == "json":
        data = json.loads(text)
        if data.get("version") == version:
            return False
        data["version"] = version
        path.write_text(json.dumps(data, indent=2) + "\n")
        return True
    if kind == "toml":
        new = re.sub(r'(?m)^(version\s*=\s*)"[^"]+"', rf'\1"{version}"', text, count=1)
        if new == text:
            return False
        path.write_text(new)
        return True
    if kind == "plain":
        path.write_text(version + "\n")
        return True
    if kind == "dunder":
        new = re.sub(r'(?m)^__version__\s*=\s*["\'][^"\']+["\']',
                     f'__version__ = "{version}"', text, count=1)
        if new == text:
            return False
        path.write_text(new)
        return True
    return False


def write_changelog(root: Path, notes: str) -> bool:
    for name in ("CHANGELOG.md", "CHANGELOG"):
        p = root / name
        if p.exists():
            text = p.read_text()
            anchor = re.search(r"^## ", text, re.M)
            insert = notes + ("\n" if anchor is None else "")
            p.write_text(text[: anchor.start()] + insert + text[anchor.start():] if anchor
                         else text.rstrip() + "\n\n" + notes)
            return True
    (root / "CHANGELOG.md").write_text("# Changelog\n\n" + notes)
    return True


def preflight(root: Path) -> list[str]:
    problems = []
    if git("status", "--porcelain", cwd=root):
        problems.append("working tree is dirty — commit or stash first")
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    if branch in ("HEAD", ""):
        problems.append("detached HEAD — check out the release branch")
    if git("rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}", cwd=root, check=False):
        behind = git("rev-list", "--count", f"HEAD..{branch}@{{upstream}}", cwd=root, check=False)
        if behind and int(behind) > 0:
            problems.append(f"{behind} commit(s) behind the remote — pull first")
    return problems


def cmd_plan(root: Path, forced: str, as_json: bool) -> int:
    tag = last_tag(root)
    level = decide(commits(root, tag), forced)
    version = bump(tag, level)
    files = version_files(root)
    if as_json:
        print(json.dumps({"fromTag": tag, "level": level, "version": version,
                          "commitCount": len(commits(root, tag)),
                          "versionFiles": [str(p.relative_to(root)) for p, _ in files],
                          "notes": changelog(commits(root, tag), version)}, indent=2))
        return 0
    print(f"provider   {provider(root)}")
    print(f"from       {tag or '(no tag yet)'}  →  v{version}   ({level})")
    print(f"commits    {len(commits(root, tag))} since the last tag")
    print(f"version in {', '.join(str(p.relative_to(root)) for p, _ in files) or '(none found)'}")
    problems = preflight(root)
    if problems:
        print("\npreflight problems:")
        for p in problems:
            print(f"  ! {p}")
    print()
    print(changelog(commits(root, tag), f"v{version}").rstrip())
    return 0


def cmd_apply(root: Path, yes: bool) -> int:
    problems = preflight(root)
    if problems:
        for p in problems:
            print(f"! {p}", file=sys.stderr)
        return 2
    tag = last_tag(root)
    version = bump(tag, decide(commits(root, tag), "auto"))
    branch = f"release/v{version}"
    notes = changelog(commits(root, tag), f"v{version}")
    print(f"release v{version} on {branch} ({provider(root)})")
    if not yes:
        print("\n(dry run) would: write the version into "
              f"{len(version_files(root))} file(s), prepend CHANGELOG.md, "
              f"branch {branch}, commit, push, open a {'MR' if provider(root) == 'gitlab' else 'PR'}")
        return 0
    changed = [str(p.relative_to(root)) for p, kind in version_files(root) if write_version(p, kind, version)]
    write_changelog(root, notes)
    git("checkout", "-q", "-b", branch, cwd=root)
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", f"chore(release): v{version}", cwd=root)
    git("push", "-q", "-u", "origin", branch, cwd=root)
    title = f"chore(release): v{version}"
    body = notes.replace('"', "'")
    if provider(root) == "gitlab":
        subprocess.run(["glab", "mr", "create", "--title", title, "--description", body,
                        "--yes"], cwd=str(root))
    else:
        subprocess.run(["gh", "pr", "create", "--title", title, "--body", body,
                        "--base", git("rev-parse", "--abbrev-ref", "HEAD@{u}", cwd=root, check=False) or "main"],
                       cwd=str(root))
    print(f"opened the release {'MR' if provider(root) == 'gitlab' else 'PR'} for v{version}"
          f" (version files: {', '.join(changed) or 'none'})")
    return 0


def cmd_tag(root: Path, yes: bool) -> int:
    tag = last_tag(root)
    version = bump(tag, decide(commits(root, tag), "auto"))
    notes = changelog(commits(root, tag), f"v{version}")
    if not yes:
        print(f"(dry run) would tag v{version} and publish a release with {len(notes.splitlines())} lines of notes")
        return 0
    git("tag", "-a", f"v{version}", "-m", f"v{version}", cwd=root)
    git("push", "origin", f"v{version}", cwd=root)
    if provider(root) == "gitlab":
        subprocess.run(["glab", "release", "create", f"v{version}", "--name", f"v{version}",
                        "--notes", notes], cwd=str(root))
    else:
        subprocess.run(["gh", "release", "create", f"v{version}", "--title", f"v{version}",
                        "--notes", notes], cwd=str(root))
    print(f"tagged and released v{version}")
    return 0


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        os.environ.update(env)
        git("init", "-q", "-b", "main", cwd=root)
        (root / "package.json").write_text('{"name":"x","version":"1.2.3"}\n')
        git("add", "-A", cwd=root); git("commit", "-qm", "chore: init", cwd=root)
        git("tag", "v1.2.3", cwd=root)
        (root / "a.txt").write_text("a\n"); git("add", "-A", cwd=root)
        git("commit", "-qm", "feat(api): add endpoint", cwd=root)
        (root / "b.txt").write_text("b\n"); git("add", "-A", cwd=root)
        git("commit", "-qm", "fix: stop the double submit", cwd=root)
        assert last_tag(root) == "v1.2.3"
        assert decide(commits(root, "v1.2.3"), "auto") == "minor"
        assert bump("v1.2.3", "minor") == "1.3.0"
        notes = changelog(commits(root, "v1.2.3"), "v1.3.0")
        assert "### Added" in notes and "add endpoint" in notes and "### Fixed" in notes, notes
        files = version_files(root)
        assert any(p.name == "package.json" for p, _ in files)
        write_version(root / "package.json", "json", "1.3.0")
        assert json.loads((root / "package.json").read_text())["version"] == "1.3.0"
        (root / "x.txt").write_text("x"); git("add", "-A", cwd=root)
        git("commit", "-qm", "feat!: drop v1 api", cwd=root)
        assert decide(commits(root, "v1.2.3"), "auto") == "major"
        assert changelog(commits(root, "v1.2.3"), "v2.0.0").startswith("## v2.0.0")
        assert "Breaking changes" in changelog(commits(root, "v1.2.3"), "v2.0.0")
    print("self-test ok")
    return 0


def main(argv: list[str]) -> int:
    flags, args = parse_flags(argv[1:])
    if args and args[0] == "self-test":
        return self_test()
    cmd = args[0] if args else "plan"
    root = Path(flags.get("path") or os.getcwd()).resolve()
    if cmd == "plan":
        return cmd_plan(root, str(flags.get("bump") or "auto"), bool(flags.get("json")))
    if cmd == "apply":
        return cmd_apply(root, bool(flags.get("yes")))
    if cmd == "tag":
        return cmd_tag(root, bool(flags.get("yes")))
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
