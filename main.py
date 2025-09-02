# main.py — API sync + async (jobs) pour création vidéo + pré-encodage
import os, json, time, tempfile, logging, shutil, subprocess, traceback, random, re
from uuid import uuid4
from typing import Any, Dict, List, Optional, Tuple
from flask import Flask, request, jsonify, g

from threading import Thread, Lock
try:
    import requests as _requests
except Exception:
    _requests = None
import urllib.request, urllib.error

from video_generator import generate_video, _encode_uniform

# --- Google Drive ---
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
KEEP_TMP  = os.getenv("KEEP_TMP", "1") == "1"

app = Flask(__name__)
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
app.logger.setLevel(logging.getLogger().level)

# -------------------- Google Drive helpers --------------------
def _gdrive_service():
    path = os.getenv("GOOGLE_CREDS", "/etc/secrets/credentials.json")
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(path, scopes=scopes)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def _gdrive_upload(file_path: str, file_name: str, folder_id: Optional[str], logger, req_id: str):
    svc = _gdrive_service()
    meta = {"name": file_name}
    if folder_id:
        meta["parents"] = [folder_id]
    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=False)
    resp = svc.files().create(body=meta, media_body=media,
                              fields="id,webViewLink,webContentLink",
                              supportsAllDrives=True).execute()
    logger.info(f"[{req_id}] gdrive upload ok id={resp.get('id')} webViewLink={resp.get('webViewLink')}")
    return resp

def _gdrive_pick_and_download_music(folder_id: str, workdir: str, logger, req_id: str) -> Tuple[Optional[str], int]:
    try:
        svc = _gdrive_service()
        q = (
            f"'{folder_id}' in parents and trashed=false and "
            f"(mimeType contains 'audio' or name contains '.mp3' or name contains '.wav' or name contains '.m4a')"
        )
        files: List[Dict[str, str]] = []
        page_token = None
        while True:
            resp = svc.files().list(
                q=q, spaces="drive",
                fields="nextPageToken, files(id,name,mimeType,size)",
                pageToken=page_token,
                includeItemsFromAllDrives=True, supportsAllDrives=True
            ).execute()
            files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        if not files:
            logger.warning(f"[{req_id}] ⚠️ aucun fichier audio trouvé dans le dossier {folder_id}")
            return None, 0

        pick = random.choice(files)
        fid, fname = pick["id"], pick["name"]
        local = os.path.join(workdir, f"music_{fname}")
        logger.info(f"[{req_id}] musique choisie: {fname} (id={fid})")

        req = svc.files().get_media(fileId=fid, supportsAllDrives=True)
        with open(local, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, req)
            done = False
            while not done:
                _status, done = downloader.next_chunk()

        delay_sec = 0
        m = re.search(r"@(\d+)(?=\.[^.]+$)", fname)
        if m:
            try: delay_sec = int(m.group(1))
            except Exception: delay_sec = 0

        return local, delay_sec
    except Exception as e:
        logger.exception(f"[{req_id}] erreur download musique: {e}")
        return None, 0

# -------------------- utils --------------------
def _ffprobe_duration(path: str) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1", path],
            stderr=subprocess.STDOUT
        ).decode("utf-8","ignore").strip()
        return max(0.0, float(out))
    except Exception:
        return 0.0

@app.before_request
def _start():
    g.req_id = request.headers.get("X-Request-ID", str(uuid4()))
    g.t0 = time.time()

@app.after_request
def _end(resp):
    try:
        dt = (time.time() - g.t0)
        app.logger.info(f"[{g.req_id}] {request.method} {request.path} -> {resp.status_code} in {dt:.3f}s")
    except Exception:
        pass
    return resp

def _parse_int(s: Any, default: int) -> int:
    try: return int(s)
    except Exception: return default

def _parse_float(s: Any, default: float) -> float:
    try: return float(s)
    except Exception: return default

def _normalize_plan(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, (bytes, bytearray)): raw = raw.decode("utf-8","ignore")
    if isinstance(raw, str):
        try: raw = json.loads(raw)
        except json.JSONDecodeError:
            last = raw.rfind("}")
            if last != -1: raw = json.loads(raw[:last+1])
            else: raise
    if isinstance(raw, dict) and "plan" in raw: raw = raw["plan"]
    if not isinstance(raw, list): raise ValueError("plan must be a JSON array")
    if not raw: raise ValueError("plan is empty")
    return raw

@app.get("/")
def root():
    return jsonify(ok=True, service="fusion-bot", ts=int(time.time()))

# -------------------- PRE-ENCODE --------------------
@app.post("/pre-encode")
def pre_encode():
    workdir = tempfile.mkdtemp(prefix="preenc_")
    req_id = str(uuid4())
    try:
        url = request.form["url"]
        output_name = request.form.get("output_name", "preencoded.mp4")
        drive_folder_id = request.form.get("drive_folder_id")

        local_src = os.path.join(workdir, "src")
        with urllib.request.urlopen(url) as r, open(local_src, "wb") as f:
            shutil.copyfileobj(r, f)

        local_out = os.path.join(workdir, output_name)
        _encode_uniform(local_src, local_out, 1080, 1920, 30, 30.0, app.logger, req_id)

        if drive_folder_id:
            gd = _gdrive_upload(local_out, output_name, drive_folder_id, app.logger, req_id)
            return jsonify({"status": "success", "drive_file_id": gd.get("id"), "drive_webViewLink": gd.get("webViewLink")})
        else:
            return jsonify({"status": "success", "local_path": local_out})
    except Exception as e:
        app.logger.error(f"[{req_id}] pre-encode failed: {e}\n{traceback.format_exc()}")
        return jsonify(error=str(e)), 500
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

# -------------------- CREATE-VIDEO (SYNC + ASYNC) --------------------
# >>> ton code /create-video et /create-video-async inchangé
# (garde la version que tu m’as envoyée)
# --------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
