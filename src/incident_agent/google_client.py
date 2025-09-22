from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import requests
import markdown as mdlib


class GoogleClient:
    """
    Minimal client to create a Google Doc from Markdown via an Apps Script Web App.

    Configuration via environment variables:
    - GOOGLE_WEBAPP_URL: Deployed Apps Script Web App URL
    - GOOGLE_SHARED_SECRET: Shared secret for simple auth (sent as 'token')
    - GOOGLE_FOLDER_ID: Optional Drive folder ID where to create the doc
    """

    def __init__(self) -> None:
        self.webapp_url: str = (os.getenv("GOOGLE_WEBAPP_URL") or "").strip()
        self.shared_secret: str = (os.getenv("GOOGLE_SHARED_SECRET") or "").strip()
        self.default_folder_id: str = (os.getenv("GOOGLE_FOLDER_ID") or "").strip()

        if not self.webapp_url:
            raise RuntimeError(
                "Missing GOOGLE_WEBAPP_URL for Google Apps Script endpoint"
            )
        if not self.shared_secret:
            raise RuntimeError(
                "Missing GOOGLE_SHARED_SECRET for Google Apps Script endpoint"
            )

    @staticmethod
    def _build_html(markdown_text: str) -> str:
        """
        Convert Markdown to styled HTML suitable for Google Docs import.
        """
        html_body = mdlib.markdown(
            markdown_text,
            extensions=["extra", "tables", "fenced_code", "toc"],
        )
        html_template = """<!doctype html>
<meta charset="utf-8">
<style>
  body{font-family:Arial,Helvetica,sans-serif; line-height:1.5}

  /* Main headers (# in Markdown → <h1>) */
  h1{ 
    font-family: 'Aldrich', sans-serif; 
    font-size:20pt; 
    color:#4376fc; 
    font-weight:normal; 
    margin:1.2em 0 .4em;
  }

  /* Subheaders (## in Markdown → <h2>) */
  h2{ 
    font-family: Arial, Helvetica, sans-serif; 
    font-size:16pt; 
    color:#000000; 
    font-weight:normal; 
    margin:1em 0 .3em;
  }

  pre, code {font-family:Consolas,Menlo,monospace;}
  pre {white-space: pre-wrap; background:#f6f8fa; padding:.75rem; border-radius:8px}
  table {border-collapse:collapse; margin:1em 0; width:100%}
  th, td {border:1px solid #ddd; padding:6px; vertical-align:top}
  blockquote {border-left:4px solid #ddd; margin:1em 0; padding:.5em 1em; color:#555}
</style>
<body>
{body}
</body>"""
        # Use direct placeholder replacement to avoid str.format interpreting CSS braces
        return html_template.replace("{body}", html_body)

    def create_document_from_markdown(
        self, markdown_text: str, title: str, folder_id: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Create a Google Doc from Markdown via the Apps Script webhook.

        Returns a mapping with keys: 'id' and 'link'.
        """
        html = self._build_html(markdown_text)
        payload: Dict[str, Any] = {
            "title": title,
            "html": html,
            "token": self.shared_secret,
        }
        target_folder = (folder_id or self.default_folder_id).strip()
        if target_folder:
            payload["folderId"] = target_folder

        resp = requests.post(self.webapp_url, json=payload, timeout=30)
        resp.raise_for_status()
        data = (
            resp.json()
            if resp.headers.get("Content-Type", "").startswith("application/json")
            else {}
        )
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected response from Google Apps Script")
        if "error" in data:
            raise RuntimeError(str(data.get("error")))
        return {"id": str(data.get("id", "")), "link": str(data.get("link", ""))}
