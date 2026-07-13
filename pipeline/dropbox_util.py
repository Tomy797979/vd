"""
dropbox_util.py — upload finished videos to Dropbox using a refresh token.

Credentials come from environment variables (set as GitHub Secrets), never
hard-coded:
  DROPBOX_APP_KEY
  DROPBOX_APP_SECRET
  DROPBOX_REFRESH_TOKEN

If any is missing, uploads are skipped gracefully and outputs are still
available as GitHub Actions artifacts.
"""

import os
import json
from pathlib import Path

import requests

DBX_API = "https://api.dropboxapi.com/2"
DBX_CONTENT = "https://content.dropboxapi.com/2"


def get_access_token():
    app_key = os.environ.get("DROPBOX_APP_KEY", "").strip()
    app_secret = os.environ.get("DROPBOX_APP_SECRET", "").strip()
    refresh = os.environ.get("DROPBOX_REFRESH_TOKEN", "").strip()
    if not (app_key and app_secret and refresh):
        return None, "Dropbox secrets not set (skipping upload)"
    try:
        r = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": app_key,
                "client_secret": app_secret,
            },
            timeout=15,
        )
        data = r.json()
        return data.get("access_token"), data.get("error_description", data.get("error"))
    except Exception as e:
        return None, str(e)


def _list_names(token, folder):
    try:
        r = requests.post(f"{DBX_API}/files/list_folder",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"path": folder, "recursive": False}, timeout=15)
        return {e["name"] for e in r.json().get("entries", [])} if r.status_code == 200 else set()
    except Exception:
        return set()


def _unique_name(filename, existing):
    if filename not in existing:
        return filename
    stem, ext = Path(filename).stem, Path(filename).suffix
    i = 1
    while f"{stem}_{i}{ext}" in existing:
        i += 1
    return f"{stem}_{i}{ext}"


def upload(token, file_path, folder, filename):
    folder = (folder or "/").strip()
    if not folder.startswith("/"):
        folder = "/" + folder
    api_folder = "" if folder == "/" else folder.rstrip("/")

    existing = _list_names(token, api_folder)
    final_name = _unique_name(filename, existing)
    dest = (api_folder + "/" + final_name).replace("//", "/")
    size = os.path.getsize(file_path)
    CHUNK = 148 * 1024 * 1024

    try:
        if size <= CHUNK:
            with open(file_path, "rb") as f:
                r = requests.post(f"{DBX_CONTENT}/files/upload",
                    headers={"Authorization": f"Bearer {token}",
                             "Dropbox-API-Arg": json.dumps({"path": dest, "mode": "add", "autorename": False}),
                             "Content-Type": "application/octet-stream"},
                    data=f, timeout=1200)
            if r.status_code != 200:
                return False, final_name, f"Upload error {r.status_code}: {r.text[:300]}", None
        else:
            with open(file_path, "rb") as f:
                chunk = f.read(CHUNK)
                r = requests.post(f"{DBX_CONTENT}/files/upload_session/start",
                    headers={"Authorization": f"Bearer {token}",
                             "Dropbox-API-Arg": '{"close":false}',
                             "Content-Type": "application/octet-stream"},
                    data=chunk, timeout=1200)
                if r.status_code != 200:
                    return False, final_name, f"Session start error: {r.text[:300]}", None
                sid = r.json()["session_id"]
                offset = len(chunk)
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    requests.post(f"{DBX_CONTENT}/files/upload_session/append_v2",
                        headers={"Authorization": f"Bearer {token}",
                                 "Dropbox-API-Arg": json.dumps({"cursor": {"session_id": sid, "offset": offset}, "close": False}),
                                 "Content-Type": "application/octet-stream"},
                        data=chunk, timeout=1200)
                    offset += len(chunk)
                r = requests.post(f"{DBX_CONTENT}/files/upload_session/finish",
                    headers={"Authorization": f"Bearer {token}",
                             "Dropbox-API-Arg": json.dumps({"cursor": {"session_id": sid, "offset": offset}, "commit": {"path": dest, "mode": "add"}}),
                             "Content-Type": "application/octet-stream"},
                    data=b"", timeout=1200)
                if r.status_code != 200:
                    return False, final_name, f"Session finish error: {r.text[:300]}", None

        r2 = requests.post(f"{DBX_API}/sharing/create_shared_link_with_settings",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"path": dest, "settings": {"requested_visibility": "public"}}, timeout=15)
        url = None
        if r2.status_code == 200:
            url = r2.json().get("url", "").replace("www.dropbox.com", "dl.dropboxusercontent.com").replace("?dl=0", "?dl=1")
        elif r2.status_code == 409:
            raw = r2.json().get("error", {}).get("shared_link_already_exists", {}).get("metadata", {}).get("url", "")
            if raw:
                url = raw.replace("www.dropbox.com", "dl.dropboxusercontent.com").replace("?dl=0", "?dl=1")
        return True, final_name, "OK", url
    except Exception as e:
        return False, final_name, str(e), None
