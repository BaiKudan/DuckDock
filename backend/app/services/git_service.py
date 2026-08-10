"""
GitService — Git bare repo 封装

每个 Skill 对应一个 Git bare repo，存储在：
    {REPOS_ROOT}/{namespace}/{skill_name}.git

版本 = Git tag（如 v1.0.0）
内容 = 仓库内文件（SKILL.md, system_prompt.md, examples/, tests/）
"""

import io
import shutil
import tarfile
import tempfile
import zipfile
from hashlib import sha256
from mimetypes import guess_type
from pathlib import Path
from typing import Optional

from dulwich import porcelain
from dulwich.errors import NotGitRepository
from dulwich.object_store import iter_tree_contents
from dulwich.objects import Commit, Tag
from dulwich.patch import write_tree_diff
from dulwich.repo import Repo

from app.core.config import settings
from app.core.identifiers import RESOURCE_IDENTIFIER_RE


class GitServiceError(Exception):
    pass


class GitService:
    def __init__(self, repos_root: str | None = None):
        self.repos_root = Path(repos_root or settings.REPOS_ROOT).expanduser().resolve()

    # ── Path helpers ────────────────────────────────────────────────

    @staticmethod
    def _validate_path_component(value: str, label: str) -> str:
        if not RESOURCE_IDENTIFIER_RE.fullmatch(value):
            raise GitServiceError(
                f"Invalid {label}: use 1-128 alphanumeric, underscore, or dash characters"
            )
        return value

    @staticmethod
    def _contained_path(root: Path, candidate: Path, label: str) -> Path:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
        try:
            relative = resolved_candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise GitServiceError(f"{label} escapes the configured storage root") from exc
        if not relative.parts:
            raise GitServiceError(f"{label} cannot be the storage root itself")
        return resolved_candidate

    def _repo_storage_path(self, *parts: str) -> Path:
        candidate = self.repos_root.joinpath(*parts)
        return self._contained_path(self.repos_root, candidate, "Repository path")

    @classmethod
    def _worktree_file_path(cls, worktree: Path, relative_path: str) -> Path:
        if Path(relative_path).is_absolute():
            raise GitServiceError("Published file path must be relative")
        return cls._contained_path(worktree, worktree / relative_path, "Published file path")

    def repo_path(self, namespace: str, skill_name: str) -> Path:
        safe_namespace = self._validate_path_component(namespace, "namespace")
        safe_skill_name = self._validate_path_component(skill_name, "skill name")
        return self._repo_storage_path(safe_namespace, f"{safe_skill_name}.git")

    def _open(self, namespace: str, skill_name: str) -> Repo:
        path = self.repo_path(namespace, skill_name)
        if not path.exists():
            raise GitServiceError(f"Repo not found: {path}")
        try:
            return Repo(str(path))
        except NotGitRepository as exc:
            raise GitServiceError(f"Invalid git repo: {path}") from exc

    # ── Repo lifecycle ────────────────────────────────────────────────

    def create_repo(self, namespace: str, skill_name: str) -> Path:
        """Create a new bare Git repository for a skill."""
        path = self.repo_path(namespace, skill_name)
        if path.exists():
            raise GitServiceError(f"Repo already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        Repo.init_bare(path, mkdir=True).close()
        return path

    def delete_repo(self, namespace: str, skill_name: str) -> None:
        """Permanently delete a skill's Git repository."""
        path = self.repo_path(namespace, skill_name)
        if not path.exists():
            raise GitServiceError(f"Repo not found: {path}")
        shutil.rmtree(str(path))

    def delete_repo_path(self, repo_path: str) -> None:
        """Permanently delete a skill repository by its stored path."""
        candidate = Path(repo_path).expanduser()
        if candidate.is_absolute():
            path = self._contained_path(self.repos_root, candidate, "Stored repository path")
        else:
            try:
                path = self._contained_path(
                    self.repos_root,
                    candidate,
                    "Stored repository path",
                )
            except GitServiceError:
                path = self._contained_path(
                    self.repos_root,
                    self.repos_root / candidate,
                    "Stored repository path",
                )
        if not path.exists():
            return
        shutil.rmtree(str(path))

    def delete_namespace_repos(self, namespace: str) -> None:
        """Delete all git repositories for a namespace."""
        safe_namespace = self._validate_path_component(namespace, "namespace")
        path = self._repo_storage_path(safe_namespace)
        if not path.exists():
            return
        shutil.rmtree(str(path))

    def fork_repo(self, src_ns: str, src_name: str, dst_ns: str, dst_name: str) -> Path:
        """Clone a skill repo as a new skill (fork)."""
        src_path = self.repo_path(src_ns, src_name)
        if not src_path.exists():
            raise GitServiceError(f"Source repo not found: {src_path}")
        dst_path = self.repo_path(dst_ns, dst_name)
        if dst_path.exists():
            raise GitServiceError(f"Destination repo already exists: {dst_path}")
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        cloned = porcelain.clone(str(src_path), str(dst_path), bare=True)
        cloned.close()
        return dst_path

    # ── Version management ────────────────────────────────────────────

    @staticmethod
    def _actor_identity(name: str, email: str) -> bytes:
        if any(char in name for char in "\r\n<>") or any(
            char in email for char in "\r\n<>"
        ):
            raise GitServiceError("Invalid Git author identity")
        return f"{name} <{email}>".encode("utf-8")

    def _commit_version(
        self,
        namespace: str,
        skill_name: str,
        files: dict[str, bytes],
        tag: str,
        author_name: str,
        author_email: str,
        message: str,
    ) -> str:
        repo_dir = self.repo_path(namespace, skill_name)
        if not repo_dir.exists():
            raise GitServiceError(f"Repo not found: {repo_dir}")

        temporary_root = Path(tempfile.mkdtemp(prefix="duckdock-publish-"))
        worktree = temporary_root / "worktree"
        work_repo: Repo | None = None
        try:
            work_repo = porcelain.clone(str(repo_dir), str(worktree))
            for relative_path, content in files.items():
                target = self._worktree_file_path(worktree, relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            porcelain.add(work_repo, list(files))
            actor = self._actor_identity(author_name, author_email)
            commit_id = porcelain.commit(
                work_repo,
                message=(message or f"chore: publish {tag}").encode("utf-8"),
                author=actor,
                committer=actor,
            )
            porcelain.tag_create(work_repo, tag, objectish=commit_id)
            branch_chain, _ = work_repo.refs.follow(b"HEAD")
            branch_ref = branch_chain[-1]
            tag_ref = f"refs/tags/{tag}".encode("utf-8")
            porcelain.push(
                work_repo,
                str(repo_dir),
                [b"HEAD:" + branch_ref, tag_ref + b":" + tag_ref],
                outstream=io.BytesIO(),
                errstream=io.BytesIO(),
            )
            return commit_id.decode("ascii")
        except GitServiceError:
            raise
        except Exception as exc:
            raise GitServiceError(f"Failed to publish Git version: {exc}") from exc
        finally:
            if work_repo is not None:
                work_repo.close()
            shutil.rmtree(temporary_root, ignore_errors=True)

    def publish_version(
        self,
        namespace: str,
        skill_name: str,
        files: dict[str, str],  # {relative_path: content_str}
        tag: str,
        author_name: str,
        author_email: str,
        message: str = "",
    ) -> str:
        """
        Commit files to the repo and create a version tag.
        Returns the commit SHA.
        """
        return self._commit_version(
            namespace,
            skill_name,
            {path: content.encode("utf-8") for path, content in files.items()},
            tag,
            author_name,
            author_email,
            message,
        )

    def publish_version_bytes(
        self,
        namespace: str,
        skill_name: str,
        files: dict[str, bytes],
        tag: str,
        author_name: str,
        author_email: str,
        message: str = "",
    ) -> str:
        """Commit raw UTF-8 bytes so compatibility uploads preserve exact file fingerprints."""
        return self._commit_version(
            namespace,
            skill_name,
            files,
            tag,
            author_name,
            author_email,
            message,
        )

    @staticmethod
    def _tag_ref(tag: str) -> bytes:
        return f"refs/tags/{tag}".encode("utf-8")

    @classmethod
    def _tag_commit(cls, repo: Repo, tag: str) -> Commit:
        try:
            value = repo[repo.refs[cls._tag_ref(tag)]]
            while isinstance(value, Tag):
                value = repo[value.object[1]]
        except (KeyError, ValueError) as exc:
            raise GitServiceError(f"Tag '{tag}' not found") from exc
        if not isinstance(value, Commit):
            raise GitServiceError(f"Tag '{tag}' does not reference a commit")
        return value

    def list_tags(self, namespace: str, skill_name: str) -> list[str]:
        """Return all version tags sorted by creation date (newest first)."""
        repo = self._open(namespace, skill_name)
        try:
            tags = [name.decode("utf-8") for name in repo.refs.as_dict(b"refs/tags")]
            return sorted(
                tags,
                key=lambda value: self._tag_commit(repo, value).commit_time,
                reverse=True,
            )
        finally:
            repo.close()

    def delete_tag(self, namespace: str, skill_name: str, tag: str) -> None:
        repo = self._open(namespace, skill_name)
        try:
            tag_ref = self._tag_ref(tag)
            if tag_ref not in repo.refs:
                raise GitServiceError(f"Tag '{tag}' not found")
            del repo.refs[tag_ref]
        finally:
            repo.close()

    def get_version_files(
        self, namespace: str, skill_name: str, tag: str
    ) -> dict[str, str]:
        """Return text file contents at the given tag. Binary blobs are lossy
        (utf-8 with errors=replace) — use get_version_files_bytes for fidelity."""
        return {
            path: data.decode("utf-8", errors="replace")
            for path, data in self.get_version_files_bytes(namespace, skill_name, tag).items()
        }

    def get_version_files_bytes(
        self, namespace: str, skill_name: str, tag: str
    ) -> dict[str, bytes]:
        """Return raw bytes for every file at the given tag. Preserves binary content
        (images, archives, pickles) that utf-8 decoding would corrupt."""
        repo = self._open(namespace, skill_name)
        try:
            commit = self._tag_commit(repo, tag)
            result: dict[str, bytes] = {}
            for entry in iter_tree_contents(repo.object_store, commit.tree):
                value = repo[entry.sha]
                result[entry.path.decode("utf-8")] = value.data
            return result
        finally:
            repo.close()

    def get_commit_sha(self, namespace: str, skill_name: str, tag: str) -> str:
        repo = self._open(namespace, skill_name)
        try:
            return self._tag_commit(repo, tag).id.decode("ascii")
        finally:
            repo.close()

    def diff(
        self, namespace: str, skill_name: str, tag_a: str, tag_b: str
    ) -> str:
        """Return unified diff between two tags."""
        repo = self._open(namespace, skill_name)
        try:
            commit_a = self._tag_commit(repo, tag_a)
            commit_b = self._tag_commit(repo, tag_b)
            output = io.BytesIO()
            write_tree_diff(
                output,
                repo.object_store,
                commit_a.tree,
                commit_b.tree,
            )
            return output.getvalue().decode("utf-8", errors="replace")
        finally:
            repo.close()

    def clone_to_tarball(
        self, namespace: str, skill_name: str, tag: str
    ) -> bytes:
        """Return a .tar.gz archive of the skill at the given tag."""
        files = self.get_version_files_bytes(namespace, skill_name, tag)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for path, data in sorted(files.items()):
                info = tarfile.TarInfo(name=f"{skill_name}/{path}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def clone_to_zip(
        self, namespace: str, skill_name: str, tag: str
    ) -> bytes:
        """Return a .zip archive of the skill at the given tag."""
        files = self.get_version_files_bytes(namespace, skill_name, tag)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, data in sorted(files.items()):
                archive.writestr(path, data)
        return buf.getvalue()

    def list_version_file_metadata(
        self, namespace: str, skill_name: str, tag: str
    ) -> list[dict[str, str | int | None]]:
        files = self.get_version_files_bytes(namespace, skill_name, tag)
        items: list[dict[str, str | int | None]] = []
        for path, data in sorted(files.items()):
            items.append(
                {
                    "path": path,
                    "size": len(data),
                    "sha256": sha256(data).hexdigest(),
                    "contentType": guess_type(path)[0] or "text/plain",
                }
            )
        return items

    def version_fingerprint(self, namespace: str, skill_name: str, tag: str) -> str:
        files = self.list_version_file_metadata(namespace, skill_name, tag)
        payload = "\n".join(f"{item['path']}:{item['sha256']}" for item in files)
        return sha256(payload.encode("utf-8")).hexdigest()

    def parse_skill_md(
        self, namespace: str, skill_name: str, tag: str
    ) -> Optional[dict]:
        """
        Parse SKILL.md front-matter from a version.
        Returns a dict of metadata or None if SKILL.md not found.
        """
        files = self.get_version_files(namespace, skill_name, tag)
        skill_md = files.get("SKILL.md")
        if not skill_md:
            return None
        skill_md = skill_md.lstrip("\ufeff")
        metadata: dict = {}
        if skill_md.startswith("---"):
            lines = skill_md.split("\n")
            in_front = False
            front_lines = []
            for line in lines:
                if line.strip() == "---":
                    if not in_front:
                        in_front = True
                        continue
                    else:
                        break
                if in_front:
                    front_lines.append(line)
            # Simple key: value parser (avoids yaml dependency)
            for fl in front_lines:
                if ":" in fl:
                    k, _, v = fl.partition(":")
                    metadata[k.strip()] = v.strip()
        return metadata or None


# Module-level singleton
git_service = GitService()
