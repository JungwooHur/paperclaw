#!/usr/bin/env python3
"""Upload an image directly into Notion (private, workspace-hosted) instead of a
public host like catbox. Notion image blocks then reference a `file_upload` id,
so figures never leave the owner's Notion — required for copyrighted/personal
source material, and immune to public-link rot.

Flow (Notion File Upload API):
  1. POST /v1/file_uploads            -> {id, upload_url, status:"pending"}
  2. POST <upload_url> (multipart)    -> status:"uploaded"
  3. reference it in a block:  {"type":"file_upload","file_upload":{"id":...}}

  from notion_upload import upload_image, image_block, migrate_page_catbox_images
  fid = upload_image("/tmp/fig.png")
  block = image_block(fid)            # ready to PATCH into /blocks/<page>/children

NOTE: a created upload expires (~1h) until it is attached to a block, so upload
and attach in the same run.
"""
import json
import mimetypes
import os
import subprocess
import time
import urllib.error
import urllib.request

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def _headers():
    tok = os.environ.get("NOTION_TOKEN")
    if not tok:
        raise SystemExit("NOTION_TOKEN must be set")
    return {"Authorization": f"Bearer {tok}", "Notion-Version": VERSION}


def _api(method, path, body=None, tries=10):
    last = None
    for a in range(tries):
        try:
            h = dict(_headers()); h["Content-Type"] = "application/json"
            req = urllib.request.Request(
                API + path, data=json.dumps(body).encode() if body else None,
                method=method, headers=h)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 5)) + 2 * a); continue
            raise
    raise last


def upload_image(path, tries=4):
    """Upload a local image file to Notion; return its file_upload id (or None)."""
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(path)[0] or "image/png"
    for _ in range(tries):
        try:
            up = _api("POST", "/file_uploads", {"filename": name, "content_type": ctype})
            fid, url = up["id"], up.get("upload_url", f"{API}/file_uploads/{up['id']}/send")
            # multipart send via curl (urllib multipart is fiddly); honor Notion headers
            r = subprocess.run(
                ["curl", "-s", "-X", "POST", url,
                 "-H", f"Authorization: Bearer {os.environ['NOTION_TOKEN']}",
                 "-H", f"Notion-Version: {VERSION}",
                 "-F", f"file=@{path};type={ctype}"],
                capture_output=True, text=True, timeout=180)
            if json.loads(r.stdout).get("status") == "uploaded":
                return fid
        except Exception:
            pass
        time.sleep(2)
    return None


def image_block(file_upload_id):
    return {"object": "block", "type": "image",
            "image": {"type": "file_upload", "file_upload": {"id": file_upload_id}}}


# ---- migration: swap a page's external catbox/litterbox images to Notion uploads

_PUBLIC_HOSTS = ("catbox.moe", "litterbox.catbox.moe")


def _download(url, dest):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            f.write(r.read())
        return os.path.getsize(dest) > 200
    except Exception:
        return False


def fetch_blocks(page_id):
    blocks, cur = [], None
    while True:
        d = _api("GET", f"/blocks/{page_id}/children?page_size=100"
                 + (f"&start_cursor={cur}" if cur else ""))
        blocks += d.get("results", [])
        if not d.get("has_more"):
            return blocks
        cur = d["next_cursor"]


def migrate_page_catbox_images(page_id, workdir, apply=False):
    """Replace each external catbox/litterbox image block with a Notion-hosted
    upload of the same image, in place (preserves position). Returns a report.

    A Notion image block cannot have its file swapped by PATCH, so we insert the
    new (file_upload) image right after the old one, then archive the old.
    """
    os.makedirs(workdir, exist_ok=True)
    blocks = fetch_blocks(page_id)
    report = {"page": page_id, "found": 0, "migrated": 0, "failed": []}
    for b in blocks:
        if b["type"] != "image":
            continue
        img = b["image"]
        if img.get("type") != "external":
            continue
        url = img.get("external", {}).get("url", "")
        if not any(h in url for h in _PUBLIC_HOSTS):
            continue
        report["found"] += 1
        if not apply:
            continue
        ext = os.path.splitext(url.split("?")[0])[1] or ".png"
        dest = os.path.join(workdir, f"{b['id'][-12:]}{ext}")
        if not _download(url, dest):
            report["failed"].append(url); continue
        fid = upload_image(dest)
        if not fid:
            report["failed"].append(url); continue
        # insert new image after the old, then archive the old
        _api("PATCH", f"/blocks/{page_id}/children",
             {"children": [image_block(fid)], "after": b["id"]})
        _api("PATCH", f"/blocks/{b['id']}", {"archived": True})
        report["migrated"] += 1
        time.sleep(0.4)
    return report


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    rep = migrate_page_catbox_images(
        a.page, a.workdir or f"/tmp/imgmig_{a.page[:8]}", a.apply)
    print(json.dumps(rep, indent=2))
