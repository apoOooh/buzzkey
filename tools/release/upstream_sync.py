import argparse
import re
import subprocess
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version

STABLE_TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")
VERSION_LINE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
UNRELEASED = "## [Unreleased]"
FORK_LINK = re.compile(r"fork of \[[^\]]+\]\(https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?\)")


class SyncError(Exception):
    pass


def upstream_version(tag):
    match = STABLE_TAG.match(tag.strip())
    if not match:
        return None
    try:
        return Version(match.group(1))
    except InvalidVersion:
        return None


def upstream_repo(changelog_text):
    match = FORK_LINK.search(changelog_text)
    if not match:
        raise SyncError("CHANGELOG-buzzkey.md names no upstream fork link")
    return match.group(1)


def base_version(pyproject_text):
    match = VERSION_LINE.search(pyproject_text)
    if not match:
        raise SyncError("pyproject.toml has no version line")
    return Version(match.group(1).split("+", 1)[0])


def is_newer(tag, pyproject_text):
    candidate = upstream_version(tag)
    return candidate is not None and candidate > base_version(pyproject_text)


def buzzkey_version(upstream):
    return f"{upstream}+bk.1"


def release_tag(version):
    return "buzzkey-" + version.replace("+", "-")


def split_conflicts(text):
    parts = []
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("<<<<<<< "):
            parts.append(line)
            index += 1
            continue
        ours, theirs, target = [], [], "ours"
        index += 1
        while index < len(lines) and not lines[index].startswith(">>>>>>> "):
            current = lines[index]
            if current.startswith("||||||| "):
                target = "base"
            elif current.startswith("=======") and current.rstrip("\r\n") == "=======":
                target = "theirs"
            elif target == "ours":
                ours.append(current)
            elif target == "theirs":
                theirs.append(current)
            index += 1
        if index >= len(lines):
            raise SyncError("unterminated conflict marker")
        parts.append((ours, theirs))
        index += 1
    return parts


def join_parts(parts, choose):
    output = []
    for part in parts:
        if isinstance(part, tuple):
            output.extend(choose(*part))
        else:
            output.append(part)
    return "".join(output)


def resolve_changelog(text):
    def choose(ours, theirs):
        if ours and theirs and ours[-1].strip() and theirs[0].startswith("## "):
            return ours + ["\n"] + theirs
        return ours + theirs

    return join_parts(split_conflicts(text), choose)


def only_version_lines(lines):
    content = [line for line in lines if line.strip()]
    return bool(content) and all(VERSION_LINE.match(line.rstrip("\r\n")) for line in content)


def resolve_pyproject(text):
    def choose(ours, theirs):
        if not only_version_lines(ours):
            raise SyncError("pyproject.toml conflict beyond the version line")
        return theirs

    return join_parts(split_conflicts(text), choose)


def set_version(pyproject_text, version):
    if not VERSION_LINE.search(pyproject_text):
        raise SyncError("pyproject.toml has no version line")
    return VERSION_LINE.sub(f'version = "{version}"', pyproject_text, count=1)


def cut_changelog(text, upstream, version):
    start = text.find(UNRELEASED)
    if start < 0:
        raise SyncError("CHANGELOG-buzzkey.md has no [Unreleased] section")
    body_start = start + len(UNRELEASED)
    next_section = text.find("\n## [", body_start)
    body_end = len(text) if next_section < 0 else next_section + 1
    pending = text[body_start:body_end].strip("\n")
    section = [
        f"## [{version}]",
        "",
        f"Based on whisper-key-local {upstream}.",
        "",
        f"- **Upstream whisper-key {upstream}** merged automatically from the release tag "
        f"`v{upstream}`; upstream changes are listed in `CHANGELOG.md`.",
    ]
    if pending:
        section.append(pending)
    return text[:start] + UNRELEASED + "\n\n" + "\n".join(section) + "\n\n" + text[body_end:]


HANDLERS = {
    "CHANGELOG.md": resolve_changelog,
    "pyproject.toml": resolve_pyproject,
}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def read(path):
    return path.read_text(encoding="utf-8")


def write(path, text):
    path.write_bytes(text.encode("utf-8"))


def apply(root, upstream):
    unmerged = sorted(set(git("diff", "--name-only", "--diff-filter=U", cwd=root).split()))
    unknown = [name for name in unmerged if name not in HANDLERS]
    if unknown:
        raise SyncError("manual merge needed for: " + ", ".join(unknown))
    for name in unmerged:
        path = root / name
        write(path, HANDLERS[name](read(path)))
    version = buzzkey_version(upstream)
    pyproject = root / "pyproject.toml"
    write(pyproject, set_version(read(pyproject), version))
    changelog = root / "CHANGELOG-buzzkey.md"
    write(changelog, cut_changelog(read(changelog), upstream, version))
    git("add", "pyproject.toml", "CHANGELOG-buzzkey.md", *unmerged, cwd=root)
    return version


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check")
    check.add_argument("tag")
    commands.add_parser("upstream-repo")
    resolve = commands.add_parser("apply")
    resolve.add_argument("tag")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "upstream-repo":
            print(upstream_repo(read(root / "CHANGELOG-buzzkey.md")))
            return 0
        upstream = upstream_version(args.tag)
        if args.command == "check":
            newer = upstream is not None and is_newer(args.tag, read(root / "pyproject.toml"))
            print(f"newer={'true' if newer else 'false'}")
            if newer:
                version = buzzkey_version(upstream)
                print(f"version={version}")
                print(f"release_tag={release_tag(version)}")
            return 0
        if upstream is None:
            raise SyncError(f"{args.tag} is not a stable release tag")
        print(f"version={apply(root, upstream)}")
        return 0
    except SyncError as error:
        print(f"error={error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
