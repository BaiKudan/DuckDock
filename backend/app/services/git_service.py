"""
GitService — Git bare repo 封装

每个 Skill 对应一个 Git bare repo，存储在：
    {REPOS_ROOT}/{namespace}/{skill_name}.git

版本 = Git tag（如 v1.0.0）
内容 = 仓库内文件（SKILL.md, system_prompt.md, examples/, tests/）
"""

import io
import tarfile
import zipfile
from hashlib import sha256
from mimetypes import guess_type
from pathlib import Path
from typing import Optional

import git
from git import Repo, InvalidGitRepositoryError, BadName

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
        except InvalidGitRepositoryError:
            raise GitServiceError(f"Invalid git repo: {path}")

    # ── Repo lifecycle ────────────────────────────────────────────────

    def create_repo(self, namespace: str, skill_name: str) -> Path:
        """Create a new bare Git repository for a skill."""
        path = self.repo_path(namespace, skill_name)
        if path.exists():
            raise GitServiceError(f"Repo already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        Repo.init(str(path), bare=True)
        return path

    def delete_repo(self, namespace: str, skill_name: str) -> None:
        """Permanently delete a skill's Git repository."""
        import shutil
        path = self.repo_path(namespace, skill_name)
        if not path.exists():
            raise GitServiceError(f"Repo not found: {path}")
        shutil.rmtree(str(path))

    def delete_repo_path(self, repo_path: str) -> None:
        """Permanently delete a skill repository by its stored path."""
        import shutil

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
        import shutil

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
        Repo.clone_from(str(src_path), str(dst_path), bare=True)
        return dst_path

    # ── Version management ────────────────────────────────────────────

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
        repo_dir = self.repo_path(namespace, skill_name)
        if not repo_dir.exists():
            raise GitServiceError(f"Repo not found: {repo_dir}")

        # Work in a temporary clone to make the commit
        import shutil
        import tempfile

        tmp = tempfile.mkdtemp(prefix="duckdock-publish-")
        work_repo = None
        try:
            try:
                work_repo = Repo.clone_from(str(repo_dir), tmp)
            except git.exc.GitCommandError as exc:
                raise GitServiceError(f"Failed to clone repo for publish: {exc.stderr or exc}") from exc
            # Write files
            for rel_path, content in files.items():
                target = self._worktree_file_path(Path(tmp), rel_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            work_repo.index.add(list(files.keys()))
            actor = git.Actor(author_name, author_email)
            commit = work_repo.index.commit(
                message or f"chore: publish {tag}",
                author=actor,
                committer=actor,
            )
            work_repo.create_tag(tag, ref=commit)
            # Push commit and tag back to bare repo
            origin = work_repo.remote("origin")
            try:
                origin.push()
                origin.push(tags=True)
            except git.exc.GitCommandError as exc:
                raise GitServiceError(f"Failed to push published version: {exc.stderr or exc}") from exc
            return commit.hexsha
        finally:
            try:
                if work_repo is not None:
                    work_repo.close()
            except Exception:
                pass
            shutil.rmtree(tmp, ignore_errors=True)

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
        repo_dir = self.repo_path(namespace, skill_name)
        if not repo_dir.exists():
            raise GitServiceError(f"Repo not found: {repo_dir}")

        import shutil
        import tempfile

        tmp = tempfile.mkdtemp(prefix="duckdock-publish-")
        work_repo = None
        try:
            try:
                work_repo = Repo.clone_from(str(repo_dir), tmp)
            except git.exc.GitCommandError as exc:
                raise GitServiceError(f"Failed to clone repo for publish: {exc.stderr or exc}") from exc
            for rel_path, content in files.items():
                target = self._worktree_file_path(Path(tmp), rel_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            work_repo.index.add(list(files.keys()))
            actor = git.Actor(author_name, author_email)
            commit = work_repo.index.commit(
                message or f"chore: publish {tag}",
                author=actor,
                committer=actor,
            )
            work_repo.create_tag(tag, ref=commit)
            origin = work_repo.remote("origin")
            try:
                origin.push()
                origin.push(tags=True)
            except git.exc.GitCommandError as exc:
                raise GitServiceError(f"Failed to push published version: {exc.stderr or exc}") from exc
            return commit.hexsha
        finally:
            try:
                if work_repo is not None:
                    work_repo.close()
            except Exception:
                pass
            shutil.rmtree(tmp, ignore_errors=True)

    def list_tags(self, namespace: str, skill_name: str) -> list[str]:
        """Return all version tags sorted by creation date (newest first)."""
        repo = self._open(namespace, skill_name)
        return sorted(
            [t.name for t in repo.tags],
            key=lambda t: repo.tags[t].commit.committed_date if hasattr(repo.tags[t], "commit") else 0,
            reverse=True,
        )

    def delete_tag(self, namespace: str, skill_name: str, tag: str) -> None:
        repo = self._open(namespace, skill_name)
        target = next((candidate for candidate in repo.tags if candidate.name == tag), None)
        if target is None:
            raise GitServiceError(f"Tag '{tag}' not found")
        repo.delete_tag(target)

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
            commit = repo.tags[tag].commit
        except (IndexError, BadName):
            raise GitServiceError(f"Tag '{tag}' not found")
        result: dict[str, bytes] = {}
        for blob in commit.tree.traverse():
            if blob.type == "blob":
                result[blob.path] = blob.data_stream.read()
        return result

    def get_commit_sha(self, namespace: str, skill_name: str, tag: str) -> str:
        repo = self._open(namespace, skill_name)
        try:
            return repo.tags[tag].commit.hexsha
        except (IndexError, BadName):
            raise GitServiceError(f"Tag '{tag}' not found")

    def diff(
        self, namespace: str, skill_name: str, tag_a: str, tag_b: str
    ) -> str:
        """Return unified diff between two tags."""
        repo = self._open(namespace, skill_name)
        try:
            commit_a = repo.tags[tag_a].commit
            commit_b = repo.tags[tag_b].commit
        except (IndexError, BadName) as e:
            raise GitServiceError(f"Tag not found: {e}")
        diffs = commit_a.diff(commit_b, create_patch=True)
        return "\n".join(d.diff.decode("utf-8", errors="replace") for d in diffs)

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
