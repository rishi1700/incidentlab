#!/usr/bin/env python3
"""Milestone 1 step 1 verifier: upload a random 1 MB file, download it, compare SHA-256."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import urllib.request

APP_URL = os.environ.get("INCIDENTLAB_APP_URL", "http://localhost:8080").rstrip("/")
SIZE = 1 * 1024 * 1024


def post_upload(payload: bytes) -> dict:
    boundary = secrets.token_hex(16)
    body = (
        f"--{boundary}\r\n".encode()
        + b'Content-Disposition: form-data; name="file"; filename="random.bin"\r\n'
        + b"Content-Type: application/octet-stream\r\n\r\n"
        + payload
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(
        f"{APP_URL}/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def get_file(file_id: str) -> bytes:
    with urllib.request.urlopen(f"{APP_URL}/files/{file_id}", timeout=30) as resp:
        return resp.read()


def main() -> int:
    payload = secrets.token_bytes(SIZE)
    sent_hash = hashlib.sha256(payload).hexdigest()
    print(f"uploading {len(payload)} random bytes to {APP_URL} ...")
    try:
        result = post_upload(payload)
    except Exception as err:
        print(f"UPLOAD FAILED: {type(err).__name__}: {err}")
        return 1
    file_id, returned_hash = result.get("id"), result.get("sha256")
    print(f"upload id={file_id} sha256={returned_hash}")
    if returned_hash != sent_hash:
        print(f"MISMATCH: server hash {returned_hash} != local hash {sent_hash}")
        return 1
    try:
        downloaded = get_file(file_id)
    except Exception as err:
        print(f"DOWNLOAD FAILED: {type(err).__name__}: {err}")
        return 1
    downloaded_hash = hashlib.sha256(downloaded).hexdigest()
    print(f"downloaded {len(downloaded)} bytes sha256={downloaded_hash}")
    if downloaded_hash != sent_hash:
        print("MISMATCH: downloaded hash != uploaded hash")
        return 1
    print("OK: upload/download SHA-256 hashes match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
