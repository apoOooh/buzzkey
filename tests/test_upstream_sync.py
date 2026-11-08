import importlib.util
import subprocess
from pathlib import Path

import pytest

SYNC_PATH = Path(__file__).resolve().parents[1] / "tools" / "release" / "upstream_sync.py"
spec = importlib.util.spec_from_file_location("upstream_sync", SYNC_PATH)
upstream_sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream_sync)

PYPROJECT = '[project]\nname = "whisper-key-local"\nversion = "1.9.6+bk.1"\n'


def test_only_newer_stable_tags_count():
    assert upstream_sync.is_newer("v1.10.0", PYPROJECT)
    assert not upstream_sync.is_newer("v1.9.6", PYPROJECT)
    assert not upstream_sync.is_newer("v1.9.5", PYPROJECT)
    assert not upstream_sync.is_newer("v1.11.0b1", PYPROJECT)
    assert not upstream_sync.is_newer("buzzkey-2.0.0-bk.1", PYPROJECT)


def test_release_tag_matches_release_workflow_rule():
    assert upstream_sync.release_tag("1.10.0+bk.1") == "buzzkey-1.10.0-bk.1"


def test_pyproject_conflict_on_version_takes_upstream():
    text = (
        "[project]\n"
        "<<<<<<< HEAD\n"
        'version = "1.9.6+bk.1"\n'
        "=======\n"
        'version = "1.10.0"\n'
        ">>>>>>> v1.10.0\n"
        'description = "x"\n'
    )
    resolved = upstream_sync.resolve_pyproject(text)
    assert resolved == '[project]\nversion = "1.10.0"\ndescription = "x"\n'
    assert upstream_sync.set_version(resolved, "1.10.0+bk.1").count('version = "1.10.0+bk.1"') == 1


def test_pyproject_conflict_beyond_version_aborts():
    text = (
        "<<<<<<< HEAD\n"
        'version = "1.9.6+bk.1"\n'
        '"numpy>=2.0",\n'
        "||||||| base\n"
        'version = "1.9.6"\n'
        "=======\n"
        'version = "1.10.0"\n'
        ">>>>>>> v1.10.0\n"
    )
    with pytest.raises(upstream_sync.SyncError):
        upstream_sync.resolve_pyproject(text)


def test_changelog_conflict_keeps_both_sides_ours_first():
    text = (
        "## [Unreleased]\n\n"
        "<<<<<<< HEAD\n"
        "### Added\n- ours\n\n"
        "=======\n"
        "## [1.10.0]\n- theirs\n\n"
        ">>>>>>> v1.10.0\n"
        "## [1.9.6]\n"
    )
    assert upstream_sync.resolve_changelog(text) == (
        "## [Unreleased]\n\n### Added\n- ours\n\n## [1.10.0]\n- theirs\n\n## [1.9.6]\n"
    )


def test_unterminated_conflict_aborts():
    with pytest.raises(upstream_sync.SyncError):
        upstream_sync.resolve_changelog("<<<<<<< HEAD\nx\n=======\n")


def test_cut_changelog_moves_unreleased_into_release_section():
    text = "# buzzkey changelog\n\n## [Unreleased]\n\n- **Feature** text\n  more\n\n## [1.9.6+bk.1]\n\nold\n"
    cut = upstream_sync.cut_changelog(text, "1.10.0", "1.10.0+bk.1")
    assert cut.startswith("# buzzkey changelog\n\n## [Unreleased]\n\n## [1.10.0+bk.1]\n\n")
    assert "Based on whisper-key-local 1.10.0." in cut
    release = cut.split("## [1.10.0+bk.1]")[1].split("## [1.9.6+bk.1]")[0]
    assert "`v1.10.0`" in release and "- **Feature** text\n  more" in release
    assert cut.endswith("## [1.9.6+bk.1]\n\nold\n")


def test_cut_changelog_without_pending_entries():
    cut = upstream_sync.cut_changelog("## [Unreleased]\n\n## [1.0.0+bk.1]\n", "1.1.0", "1.1.0+bk.1")
    assert cut.count("## [") == 3
    assert cut.index("## [1.1.0+bk.1]") < cut.index("## [1.0.0+bk.1]")


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def merge_repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "dev")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "test")
    git(tmp_path, "config", "merge.conflictStyle", "merge")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [1.0.0]\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "branch", "upstream")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0+bk.1"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n- ours\n\n## [1.0.0]\n", encoding="utf-8")
    (tmp_path / "CHANGELOG-buzzkey.md").write_text("## [Unreleased]\n\n- pending\n\n## [1.0.0+bk.1]\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "buzzkey")
    git(tmp_path, "checkout", "-q", "upstream")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.1.0"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [1.1.0]\n- new\n\n## [1.0.0]\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "upstream 1.1.0")
    git(tmp_path, "tag", "v1.1.0")
    git(tmp_path, "checkout", "-q", "dev")
    return tmp_path


def start_merge(root):
    subprocess.run(["git", "merge", "--no-ff", "--no-commit", "v1.1.0"], cwd=root, capture_output=True)


def test_apply_resolves_known_conflicts_and_stages_release(merge_repo):
    start_merge(merge_repo)
    assert upstream_sync.main(["--root", str(merge_repo), "apply", "v1.1.0"]) == 0
    assert 'version = "1.1.0+bk.1"' in (merge_repo / "pyproject.toml").read_text(encoding="utf-8")
    changelog = (merge_repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- ours" in changelog and "## [1.1.0]" in changelog and "<<<<<<<" not in changelog
    buzzkey = (merge_repo / "CHANGELOG-buzzkey.md").read_text(encoding="utf-8")
    assert buzzkey.index("## [1.1.0+bk.1]") < buzzkey.index("- pending") < buzzkey.index("## [1.0.0+bk.1]")
    status = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=merge_repo,
                            capture_output=True, text=True).stdout
    assert status == ""


def test_apply_refuses_conflicts_in_code(merge_repo, capsys):
    git(merge_repo, "checkout", "-q", "upstream")
    (merge_repo / "app.py").write_text("a = 2\n", encoding="utf-8")
    git(merge_repo, "commit", "-qam", "upstream code")
    git(merge_repo, "tag", "-f", "v1.1.0")
    git(merge_repo, "checkout", "-q", "dev")
    (merge_repo / "app.py").write_text("a = 3\n", encoding="utf-8")
    git(merge_repo, "commit", "-qam", "our code")
    start_merge(merge_repo)
    assert upstream_sync.main(["--root", str(merge_repo), "apply", "v1.1.0"]) == 1
    assert "app.py" in capsys.readouterr().err


def test_check_reports_release_outputs(merge_repo, capsys):
    git(merge_repo, "checkout", "-q", "dev")
    assert upstream_sync.main(["--root", str(merge_repo), "check", "v1.1.0"]) == 0
    assert capsys.readouterr().out.split() == ["newer=true", "version=1.1.0+bk.1", "release_tag=buzzkey-1.1.0-bk.1"]
    assert upstream_sync.main(["--root", str(merge_repo), "check", "v1.0.0"]) == 0
    assert capsys.readouterr().out.split() == ["newer=false"]


def test_upstream_repo_comes_from_the_fork_link():
    text = "# buzzkey changelog\n\nbuzzkey is a fork of [whisper-key-local](https://github.com/some-owner/whisper-key-local). x\n"
    assert upstream_sync.upstream_repo(text) == "some-owner/whisper-key-local"
    with pytest.raises(upstream_sync.SyncError):
        upstream_sync.upstream_repo("no link here")


def test_real_changelog_names_an_upstream_repo():
    changelog = (SYNC_PATH.parents[2] / "CHANGELOG-buzzkey.md").read_text(encoding="utf-8")
    assert upstream_sync.upstream_repo(changelog).endswith("/whisper-key-local")


def test_changelog_union_keeps_a_blank_line_before_the_next_section():
    text = "<<<<<<< HEAD\n- ours\n=======\n## [1.10.0]\n- theirs\n>>>>>>> v1.10.0\n"
    assert upstream_sync.resolve_changelog(text) == "- ours\n\n## [1.10.0]\n- theirs\n"
