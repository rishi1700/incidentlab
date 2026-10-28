"""IncidentLab upload app: store files on /data, record metadata in Postgres."""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

log = logging.getLogger("incidentlab")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://lab:lab@db:5432/lab")
CONNECT_TIMEOUT = 2   # seconds; keeps /health inside the 3s check limit when db is paused
DATA_DIR = Path("/data")
TMP_DIR = DATA_DIR / "tmp"
UPLOAD_DIR = DATA_DIR / "uploads"


def init_db() -> None:
    with psycopg.connect(DATABASE_URL, connect_timeout=CONNECT_TIMEOUT) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS files ("
            "id TEXT PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "size BIGINT NOT NULL, "
            "sha256 CHAR(64) NOT NULL)"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/upload")
def upload(file: UploadFile):
    file_id = secrets.token_hex(16)
    tmp_path = TMP_DIR / f"{file_id}.part"
    final_path = UPLOAD_DIR / file_id
    digest = hashlib.sha256()
    size = 0
    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "wb") as out:
            while chunk := file.file.read(65536):
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
    finally:
        file.file.close()
    hexdigest = digest.hexdigest()
    os.replace(tmp_path, final_path)
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=CONNECT_TIMEOUT) as conn:
            conn.execute(
                "INSERT INTO files (id, name, size, sha256) VALUES (%s, %s, %s, %s)",
                (file_id, file.filename or "upload", size, hexdigest),
            )
    except Exception:
        log.exception("database insert failed")
        try:
            final_path.unlink()
        except OSError:
            pass
        raise
    return {"id": file_id, "sha256": hexdigest}


@app.get("/files/{file_id}")
def download(file_id: str):
    if not file_id or not all(c in "0123456789abcdef" for c in file_id):
        raise HTTPException(status_code=404, detail="not found")
    with psycopg.connect(DATABASE_URL, connect_timeout=CONNECT_TIMEOUT) as conn:
        row = conn.execute(
            "SELECT name FROM files WHERE id = %s", (file_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    path = UPLOAD_DIR / file_id
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, filename=row[0])


@app.delete("/files/{file_id}")
def delete_file(file_id: str):
    if not file_id or not all(c in "0123456789abcdef" for c in file_id):
        raise HTTPException(status_code=404, detail="not found")
    with psycopg.connect(DATABASE_URL, connect_timeout=CONNECT_TIMEOUT) as conn:
        row = conn.execute(
            "DELETE FROM files WHERE id = %s RETURNING name", (file_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    (UPLOAD_DIR / file_id).unlink(missing_ok=True)
    return {"deleted": file_id}


@app.get("/health")
def health():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=CONNECT_TIMEOUT) as conn:
            conn.execute("SELECT 1").fetchone()
        probe = UPLOAD_DIR / f".health-{secrets.token_hex(8)}"
        with open(probe, "wb") as f:
            f.write(b"ok")
        probe.unlink()
    except Exception:
        log.exception("health check failed")
        return JSONResponse(status_code=503, content={"status": "unhealthy"})
    return {"status": "ok"}
