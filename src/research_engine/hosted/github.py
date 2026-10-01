"""Save verified library documents to the repository, as a pull request for the owner to merge.

The hosted app's disk does not outlive a restart on a free plan, and a document one visitor adds
should be there for the next. The repository is the durable, versioned home for a company's
library: once merged, every place the company is analysed (the published site, the hosted app
after its next deploy) reads the document from there.

Only documents that passed verification are proposed, and the owner still merges each one, so an
upload can never change the permanent library by itself.

Configuration (environment): GITHUB_TOKEN — a fine-grained token limited to this one repository
with "Contents" and "Pull requests" write access; GITHUB_REPOSITORY — `owner/name`;
GITHUB_BASE_BRANCH — defaults to `main`.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..documents.library import INDEX_FILE, LIBRARY_DIR, DocumentLibrary, LibraryEntry

API = "https://api.github.com"
Opener = Callable[[urllib.request.Request], Any]


class PersistError(Exception):
    pass


class GitHubPersister:
    def __init__(self, token: str, repository: str, base: str = "main", *, opener: Opener | None = None):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
            raise ValueError("GITHUB_REPOSITORY must look like owner/name")
        self.token = token
        self.repository = repository
        self.base = base
        self._open = opener or (lambda req: urllib.request.urlopen(req, timeout=60))  # noqa: S310 - fixed host

    @classmethod
    def from_env(cls) -> GitHubPersister | None:
        token, repository = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        if not token or not repository:
            return None
        return cls(token, repository, os.environ.get("GITHUB_BASE_BRANCH", "main"))

    # ---- transport --------------------------------------------------------------------------------

    def _call(self, method: str, path: str, body: dict | None = None, *, missing_ok: bool = False) -> Any:
        req = urllib.request.Request(
            f"{API}/repos/{self.repository}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "research-engine",
                     **({"Content-Type": "application/json"} if body is not None else {})},
        )
        try:
            response = self._open(req)
            raw = response.read()
        except urllib.error.HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            raise PersistError(f"GitHub answered {exc.code} to {method} {path}") from None
        except urllib.error.URLError as exc:
            raise PersistError(f"GitHub could not be reached: {exc.reason}") from None
        return json.loads(raw) if raw else None

    def _blob(self, content: bytes) -> str:
        return self._call("POST", "/git/blobs", {"content": base64.b64encode(content).decode(), "encoding": "base64"})["sha"]

    # ---- proposal ---------------------------------------------------------------------------------

    def propose(self, company_id: str, workspace: Path, entry: LibraryEntry, *, summary: str = "") -> str:
        """Open a pull request adding `entry` (and the company, if the repository lacks it). Returns its URL."""
        library = DocumentLibrary(workspace)
        company_dir = f"companies/{company_id}"
        base_ref = self._call("GET", f"/git/ref/heads/{self.base}")
        base_sha = base_ref["object"]["sha"]
        base_tree = self._call("GET", f"/git/commits/{base_sha}")["tree"]["sha"]

        existing = self._call("GET", f"/contents/{company_dir}/{LIBRARY_DIR}/{INDEX_FILE}?ref={self.base}", missing_ok=True)
        documents = json.loads(base64.b64decode(existing["content"]))["documents"] if existing else []
        if any(d.get("id") == entry.id for d in documents):
            raise PersistError("the repository already has this document")
        record = {k: v for k, v in entry.__dict__.items() if k != "saved"}
        index_bytes = (json.dumps({"documents": [*documents, record]}, indent=2) + "\n").encode()

        tree = [{"path": f"{company_dir}/{LIBRARY_DIR}/{INDEX_FILE}", "mode": "100644", "type": "blob",
                 "sha": self._blob(index_bytes)}]
        path = library.path_of(entry)
        if path is not None:
            tree.append({"path": f"{company_dir}/{LIBRARY_DIR}/{entry.file}", "mode": "100644", "type": "blob",
                         "sha": self._blob(path.read_bytes())})
        if self._call("GET", f"/contents/{company_dir}/config.yaml?ref={self.base}", missing_ok=True) is None:
            config = Path(workspace) / "config.yaml"
            tree.append({"path": f"{company_dir}/config.yaml", "mode": "100644", "type": "blob",
                         "sha": self._blob(config.read_bytes())})

        new_tree = self._call("POST", "/git/trees", {"base_tree": base_tree, "tree": tree})["sha"]
        message = f"Add {entry.title} to the {company_id} library"
        commit = self._call("POST", "/git/commits", {"message": message, "tree": new_tree, "parents": [base_sha]})["sha"]
        branch = f"library/{company_id}-{entry.id}"
        self._call("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit})
        body = (f"A visitor added **{entry.title}** ({entry.kind.replace('_', ' ')}) for `{company_id}`.\n\n"
                f"{summary}\n\nMerging keeps it in the permanent library: the published site and the hosted app "
                "will read it on their next build.")
        pull = self._call("POST", "/pulls", {"title": message, "head": branch, "base": self.base, "body": body})
        return pull["html_url"]
