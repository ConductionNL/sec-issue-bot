from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests


class JiraClient:
    """Minimal Jira REST client for creating issues and attaching files.

    Requires the following environment variables:
    - JIRA_URL: Base URL of your Jira instance (e.g. https://your-domain.atlassian.net)
    - Authentication (pick one):
        - Cloud: JIRA_EMAIL + JIRA_API_TOKEN (Basic auth)
        - Server/DC: JIRA_USERNAME + JIRA_PASSWORD (Basic auth)
        - Server/DC: JIRA_PAT (Personal Access Token; Bearer auth)
    - JIRA_PROJECT_KEY: Project key (e.g. SEC)
    - JIRA_ISSUE_TYPE: Issue type name (default: Task)
    - JIRA_ISSUE_TYPE_ID: Issue type ID (optional; if set, takes precedence over name)
    """

    def __init__(self) -> None:
        """
        Initialize Jira client with configuration and authentication from environment.

        Required environment variables include JIRA_URL and JIRA_PROJECT_KEY, plus one of the
        supported authentication methods.

        @return None
        """
        self.base_url: str = (os.getenv("JIRA_URL") or "").strip()
        # Auth variants
        self.email: str = (os.getenv("JIRA_EMAIL") or "").strip()
        self.api_token: str = (os.getenv("JIRA_API_TOKEN") or "").strip()
        self.username: str = (os.getenv("JIRA_USERNAME") or "").strip()
        self.password: str = (os.getenv("JIRA_PASSWORD") or "").strip()
        self.pat: str = (os.getenv("JIRA_PAT") or "").strip()
        self.project_key: str = (os.getenv("JIRA_PROJECT_KEY") or "").strip()
        self.issue_type: str = (
            os.getenv("JIRA_ISSUE_TYPE") or "Task"
        ).strip() or "Task"
        self.issue_type_id: str = (os.getenv("JIRA_ISSUE_TYPE_ID") or "").strip()
        self._api_version_selected: str | None = None
        self._api_versions = ["3", "2", "latest"]

        if not self.base_url or not self.project_key:
            raise RuntimeError(
                "Missing Jira configuration. Please set JIRA_URL and JIRA_PROJECT_KEY. Also set one of: "
                "(JIRA_EMAIL + JIRA_API_TOKEN), (JIRA_USERNAME + JIRA_PASSWORD), or JIRA_PAT."
            )

        self._session = requests.Session()
        # Configure auth
        if self.pat:
            # Bearer token for Server/DC PAT
            self._session.headers.update({"Authorization": f"Bearer {self.pat}"})
        elif self.email and self.api_token:
            self._session.auth = (self.email, self.api_token)
        elif self.username and self.password:
            self._session.auth = (self.username, self.password)
        else:
            raise RuntimeError(
                "No Jira credentials provided. Set (JIRA_EMAIL + JIRA_API_TOKEN) or (JIRA_USERNAME + JIRA_PASSWORD) or JIRA_PAT."
            )
        self._session.headers.update({"Accept": "application/json"})

    def _api(self, path: str) -> str:
        """
        Join the base URL with a REST path.

        @param path: REST path beginning with '/'.
        @return str: Absolute URL string.
        """
        return f"{self.base_url.rstrip('/')}{path}"

    def _candidate_bases(self) -> list[str]:
        """
        Produce candidate base URLs to try, with and without '/jira' suffix.

        @return list[str]: Ordered unique list of base URL candidates.
        """
        base = self.base_url.rstrip("/")
        # Always try both variants: without and with '/jira'
        if base.lower().endswith("/jira"):
            base_no = base[: -len("/jira")] or "/"
        else:
            base_no = base
        base_with = f"{base_no.rstrip('/')}/jira"
        # Deduplicate while preserving order
        seen = set()
        candidates: list[str] = []
        for b in [base_no, base_with]:
            if b not in seen:
                candidates.append(b)
                seen.add(b)
        return candidates

    def create_issue(
        self,
        summary: Optional[str],
        description: str,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a Jira issue with a minimal ADF description and optional extra fields.

        @param summary: Optional issue summary (trimmed to 255 chars if present).
        @param description: Markdown-like text for description (converted to ADF).
        @param extra_fields: Optional additional fields payload to merge.
        @return Dict[str, Any]: JSON response of the created issue.
        """

        def _description_to_adf(md_text: str) -> Dict[str, Any]:
            """
            Convert Markdown-ish text into a minimal Jira ADF document.

            @param md_text: Description text with headings (#) and paragraphs.
            @return Dict[str, Any]: ADF node tree.
            """
            # Minimal ADF: split by lines; support Markdown-style headings (#, ##) and paragraphs
            lines = (md_text or "").splitlines()
            content: list[Dict[str, Any]] = []
            for line in lines:
                s = line.rstrip("\n")
                if s.strip() == "":
                    content.append({"type": "paragraph", "content": []})
                    continue
                # Detect heading syntax: one or more # followed by space
                i = 0
                while i < len(s) and s[i] == "#":
                    i += 1
                if i > 0 and i <= 6 and i < len(s) and s[i] == " ":
                    heading_text = s[i + 1 :].lstrip()
                    content.append(
                        {
                            "type": "heading",
                            "attrs": {"level": i},
                            "content": [{"type": "text", "text": heading_text}],
                        }
                    )
                else:
                    content.append(
                        {"type": "paragraph", "content": [{"type": "text", "text": s}]}
                    )
            if not content:
                content = [{"type": "paragraph", "content": []}]
            return {"type": "doc", "version": 1, "content": content}

        adf_description = _description_to_adf(description)
        fields: Dict[str, Any] = {
            "project": {"key": self.project_key},
            "description": adf_description,
            "issuetype": (
                {"id": self.issue_type_id}
                if self.issue_type_id
                else {"name": self.issue_type}
            ),
        }
        if isinstance(summary, str) and summary.strip():
            fields["summary"] = summary.strip()[:255]
        payload: Dict[str, Any] = {"fields": fields}
        if isinstance(extra_fields, dict) and extra_fields:
            # Merge additional fields (e.g., customfield_10061) into fields payload
            payload["fields"].update(extra_fields)
        # Try v3, then v2 if 404
        versions_to_try = (
            [self._api_version_selected] if self._api_version_selected else []
        )
        versions_to_try += [v for v in self._api_versions if v not in versions_to_try]
        # Try with possible context paths: given base_url, and base_url + '/jira' if not already containing it
        base_candidates = self._candidate_bases()
        attempts: list[tuple[str, int, str]] = []  # (url, status, snippet)
        for base in base_candidates:
            for ver in versions_to_try:
                url = f"{base.rstrip('/')}/rest/api/{ver}/issue"
                resp = self._session.post(
                    url, json=payload, timeout=30, allow_redirects=False
                )
                snippet = resp.text[:300] if resp.text else ""
                if 300 <= resp.status_code < 400:
                    loc = resp.headers.get("Location", "")
                    raise RuntimeError(
                        f"Jira create issue received redirect ({resp.status_code}) to {loc}. This usually indicates authentication failure. Verify credentials for {url}."
                    )
                if resp.status_code == 404:
                    attempts.append((url, resp.status_code, snippet))
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Jira create issue failed: {resp.status_code} {snippet}"
                    )
                # success
                self.base_url = base
                self._api_version_selected = ver
                return resp.json()
        # If we got here, all tried versions failed with 404
        detail = "; ".join([f"{u} -> {s}" for (u, s, _t) in attempts])
        last_snippet = attempts[-1][2] if attempts else ""
        raise RuntimeError(
            f"Jira create issue failed with 404 on all versions ({detail}). Last response snippet: {last_snippet}"
        )

    def update_issue(self, issue_key: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update fields on an existing Jira issue.

        @param issue_key: Issue key (e.g., SEC-123).
        @param fields: Fields to update under 'fields'.
        @return Dict[str, Any]: Update result indicator.
        """
        if not isinstance(fields, dict) or not fields:
            return {"updated": False, "reason": "no fields supplied"}
        versions_to_try = (
            [self._api_version_selected] if self._api_version_selected else []
        )
        versions_to_try += [v for v in self._api_versions if v not in versions_to_try]
        base_candidates = self._candidate_bases()
        attempts: list[tuple[str, int, str]] = []
        for base in base_candidates:
            for ver in versions_to_try:
                url = f"{base.rstrip('/')}/rest/api/{ver}/issue/{issue_key}"
                resp = self._session.put(
                    url, json={"fields": fields}, timeout=30, allow_redirects=False
                )
                snippet = resp.text[:300] if resp.text else ""
                if 300 <= resp.status_code < 400:
                    loc = resp.headers.get("Location", "")
                    raise RuntimeError(
                        f"Jira update received redirect ({resp.status_code}) to {loc}. This usually indicates authentication failure. Verify credentials for {url}."
                    )
                if resp.status_code == 404:
                    attempts.append((url, resp.status_code, snippet))
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Jira update failed: {resp.status_code} {snippet}"
                    )
                # success
                self.base_url = base
                self._api_version_selected = ver
                return {"updated": True}
        detail = "; ".join([f"{u} -> {s}" for (u, s, _t) in attempts])
        last_snippet = attempts[-1][2] if attempts else ""
        raise RuntimeError(
            f"Jira update failed with 404 on all versions ({detail}). Last response snippet: {last_snippet}"
        )

    def attach_markdown(
        self, issue_key: str, filename: str, content: str
    ) -> Optional[Any]:
        """
        Attach a markdown file as an attachment to a Jira issue.

        @param issue_key: Issue key (e.g., SEC-123).
        @param filename: Name of the attachment file.
        @param content: Markdown content to upload.
        @return Optional[Any]: JSON response on success, None if response is non-JSON.
        """
        headers = {"X-Atlassian-Token": "no-check"}
        files = {"file": (filename, content.encode("utf-8"), "text/markdown")}
        versions_to_try = (
            [self._api_version_selected] if self._api_version_selected else []
        )
        versions_to_try += [v for v in self._api_versions if v not in versions_to_try]
        base_candidates = self._candidate_bases()
        attempts: list[tuple[str, int, str]] = []
        for base in base_candidates:
            for ver in versions_to_try:
                url = f"{base.rstrip('/')}/rest/api/{ver}/issue/{issue_key}/attachments"
                resp = self._session.post(
                    url, headers=headers, files=files, timeout=30, allow_redirects=False
                )
                snippet = resp.text[:300] if resp.text else ""
                if 300 <= resp.status_code < 400:
                    loc = resp.headers.get("Location", "")
                    raise RuntimeError(
                        f"Jira attach received redirect ({resp.status_code}) to {loc}. This usually indicates authentication failure. Verify credentials for {url}."
                    )
                if resp.status_code == 404:
                    attempts.append((url, resp.status_code, snippet))
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Jira attach failed: {resp.status_code} {snippet}"
                    )
                try:
                    # Persist selected base/version on success
                    self.base_url = base
                    self._api_version_selected = ver
                    return resp.json()
                except Exception:
                    return None
        detail = "; ".join([f"{u} -> {s}" for (u, s, _t) in attempts])
        last_snippet = attempts[-1][2] if attempts else ""
        raise RuntimeError(
            f"Jira attach failed with 404 on all versions ({detail}). Last response snippet: {last_snippet}"
        )
