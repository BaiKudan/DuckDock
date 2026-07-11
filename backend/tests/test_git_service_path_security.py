from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints.clawhub import _slug_parts
from app.schemas.namespace import NamespaceCreate
from app.services.git_service import GitService, GitServiceError


@pytest.mark.parametrize(
    ("namespace", "skill_name"),
    [
        ("../outside", "safe"),
        ("/tmp/outside", "safe"),
        ("safe\\outside", "safe"),
        ("safe", "../outside"),
        ("safe", "/tmp/outside"),
        ("safe", "nested/outside"),
    ],
)
def test_repo_path_rejects_path_components(
    tmp_path: Path,
    namespace: str,
    skill_name: str,
) -> None:
    service = GitService(str(tmp_path / "repos"))

    with pytest.raises(GitServiceError, match="Invalid"):
        service.repo_path(namespace, skill_name)


def test_delete_repo_path_rejects_paths_outside_repos_root(tmp_path: Path) -> None:
    repos_root = tmp_path / "repos"
    outside_repo = tmp_path / "outside.git"
    outside_repo.mkdir()
    service = GitService(str(repos_root))

    with pytest.raises(GitServiceError, match="escapes"):
        service.delete_repo_path(str(outside_repo))

    assert outside_repo.exists()


@pytest.mark.parametrize("stored_path", ["repos/safe.git", "safe.git"])
def test_delete_repo_path_accepts_legacy_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stored_path: str,
) -> None:
    repos_root = tmp_path / "repos"
    repo = repos_root / "safe.git"
    repo.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    service = GitService("repos")

    service.delete_repo_path(stored_path)

    assert not repo.exists()


@pytest.mark.parametrize("relative_path", ["../outside.txt", "/tmp/outside.txt"])
def test_publish_version_rejects_paths_outside_worktree(
    tmp_path: Path,
    relative_path: str,
) -> None:
    service = GitService(str(tmp_path / "repos"))
    service.create_repo("safe-namespace", "safe-skill")

    with pytest.raises(GitServiceError, match="Published file path"):
        service.publish_version(
            namespace="safe-namespace",
            skill_name="safe-skill",
            files={relative_path: "not written"},
            tag="v1.0.0",
            author_name="tester",
            author_email="tester@example.com",
        )

    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("name", ["../outside", "/tmp/outside", "safe/outside", "safe\\outside"])
def test_namespace_create_rejects_path_like_names(name: str) -> None:
    with pytest.raises(ValidationError):
        NamespaceCreate(name=name)


def test_namespace_create_normalizes_safe_name() -> None:
    assert NamespaceCreate(name="Platform-Core").name == "platform-core"


@pytest.mark.parametrize(
    "slug",
    [
        "agent.v1",
        "../agent",
        "/tmp/agent",
        "team--agent.v1",
        "team/child--agent",
    ],
)
def test_clawhub_slug_rejects_unsupported_identifiers(slug: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _slug_parts(slug)

    assert exc.value.status_code == 422


def test_clawhub_slug_normalizes_safe_identifiers() -> None:
    assert _slug_parts(" Platform--Agent_One ") == ("platform", "agent_one")
