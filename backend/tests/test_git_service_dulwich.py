from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from app.services.git_service import GitService, GitServiceError


def test_pure_python_git_lifecycle_preserves_versions_and_binary_files(tmp_path) -> None:
    service = GitService(str(tmp_path / "repos"))
    service.create_repo("team", "skill")

    first_sha = service.publish_version(
        "team",
        "skill",
        {"SKILL.md": "---\nname: skill\n---\n", "nested/value.txt": "old\n"},
        "v1.0.0",
        "DuckDock Tester",
        "tester@example.test",
    )
    second_sha = service.publish_version_bytes(
        "team",
        "skill",
        {"nested/value.txt": b"new\n", "assets/pixel.bin": b"\x00\xff"},
        "v1.1.0",
        "DuckDock Tester",
        "tester@example.test",
    )

    assert len(first_sha) == 40
    assert len(second_sha) == 40
    assert first_sha != second_sha
    assert set(service.list_tags("team", "skill")) == {"v1.0.0", "v1.1.0"}
    assert service.get_commit_sha("team", "skill", "v1.1.0") == second_sha
    assert service.get_version_files_bytes("team", "skill", "v1.1.0") == {
        "SKILL.md": b"---\nname: skill\n---\n",
        "assets/pixel.bin": b"\x00\xff",
        "nested/value.txt": b"new\n",
    }
    diff = service.diff("team", "skill", "v1.0.0", "v1.1.0")
    assert "-old" in diff
    assert "+new" in diff
    assert len(service.version_fingerprint("team", "skill", "v1.1.0")) == 64

    with tarfile.open(fileobj=io.BytesIO(service.clone_to_tarball("team", "skill", "v1.1.0"))) as archive:
        assert "skill/assets/pixel.bin" in archive.getnames()
    with zipfile.ZipFile(io.BytesIO(service.clone_to_zip("team", "skill", "v1.1.0"))) as archive:
        assert archive.read("assets/pixel.bin") == b"\x00\xff"

    service.fork_repo("team", "skill", "team", "fork")
    assert service.get_version_files_bytes("team", "fork", "v1.0.0")["nested/value.txt"] == b"old\n"

    service.delete_tag("team", "skill", "v1.0.0")
    with pytest.raises(GitServiceError, match="not found"):
        service.get_commit_sha("team", "skill", "v1.0.0")


def test_git_author_identity_rejects_header_injection(tmp_path) -> None:
    service = GitService(str(tmp_path / "repos"))
    service.create_repo("team", "skill")

    with pytest.raises(GitServiceError, match="author identity"):
        service.publish_version(
            "team",
            "skill",
            {"SKILL.md": "safe"},
            "v1.0.0",
            "attacker\ncommitter Evil",
            "attacker@example.test",
        )
