# main.py — API sync + async (jobs) pour création vidéo + pré-encodage
import os, json, time, tempfile, logging, shutil, subprocess, traceback, random, re
from uuid import uuid4
from typing import Any, Dict, List, Optional, Tuple
from flask import Flask, request, jsonify, g

from threading import Thread, Lock
try:
    import requests as _requests  # pour le callback async
except Exception:
    _requests = None
import urllib.request, urllib.error

from video_generator import generate_video, _encode_uniform

# --- Google Drive ---
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# -------------------- Config --------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
KEEP_TMP  = os.getenv("KEEP_TMP", "1") == "1"  # garde les /tmp si 1
GOOGLE_CREDS_PATH = os.getenv("GOOGLE_CREDS", "/etc/secrets/credentials.json")

app = Flask(__name__)
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
app.logger.setLevel(logging.getLogger().level)

# -------------------- Google Drive helpers --------------------
def _gdrive_service():
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=scopes)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def _sa_email() -> str:
    try:
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=["https://www.googleapis.com/auth/drive"])
        return getattr(creds, "service_account_email", "")
    except Exception:
        return ""

def _gdrive_upload(file_path: str, file_name: str, folder_id: Optional[str], logger, req_id: str):
    svc = _gdrive_service()
    meta = {"name": file_name}
    if folder_id:
        meta["parents"] = [folder_id]
    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=False)
    resp = svc.files().create(
        body=meta, media_body=media,
        fields="id,webViewLink,webContentLink",
        supportsAllDrives=True
    ).execute()
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

def _parse_int(s: Any, default: int) -> int:
    try: return int(s)
    except Exception: return default

def _parse_float(s: Any, default: float) -> float:
    try: return float(s)
    except Exception: return default

def _normalize_plan(raw: Any) -> List[Dict[str, Any]]:
    """Accepte string JSON (ou objet) et renvoie le tableau `plan`."""
    if isinstance(raw, (bytes, bytearray)): raw = raw.decode("utf-8","ignore")
    if isinstance(raw, str):
        try:
            app.logger.info(f"[{g.req_id}] plan_len={len(raw)} head={raw[:400].replace(chr(10),' ')}")
        except Exception:
            pass
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            last = raw.rfind("}")
            if last != -1:
                raw = json.loads(raw[:last+1])
            else:
                raise
    if isinstance(raw, dict) and "plan" in raw:
        raw = raw["plan"]
    if not isinstance(raw, list):
        raise ValueError("plan must be a JSON array")
    if not raw:
        raise ValueError("plan is empty")
    return raw

# -------------------- hooks --------------------
@app.before_request
def _start():
    g.req_id = request.headers.get("X-Request-ID", str(uuid4()))
    g.t0 = time.time()
    app.logger.info(f"[{g.req_id}] --> {request.method} {request.path} ct={request.content_type} len={request.content_length}")

@app.after_request
def _end(resp):
    try:
        dt = (time.time() - g.t0)
        resp.headers["X-Request-ID"] = g.req_id
        app.logger.info(f"[{g.req_id}] <-- {resp.status_code} in {dt:.3f}s")
    except Exception:
        pass
    return resp

# -------------------- Health & Debug --------------------
@app.get("/")
def root():
    return jsonify(ok=True, service="fusion-bot", ts=int(time.time()))

@app.get("/whoami-drive")
def whoami_drive():
    return jsonify(service_account_email=_sa_email())

def _drive_resolve_target_id(file_id: str) -> str:
    """
    Si file_id est un raccourci (application/vnd.google-apps.shortcut),
    on renvoie shortcutDetails.targetId; sinon on renvoie file_id tel quel.
    """
    svc = _gdrive_service()
    meta = svc.files().get(
        fileId=file_id,
        fields="id,name,mimeType,shortcutDetails/targetId,parents",
        supportsAllDrives=True
    ).execute()
    if meta.get("mimeType") == "application/vnd.google-apps.shortcut":
        tgt = (meta.get("shortcutDetails") or {}).get("targetId")
        if tgt:
            return tgt
    return file_id

# -------------------- PRE-ENCODE (file_id ONLY) --------------------
@app.post("/pre-encode")
def pre_encode():
    """
    Multipart/form-data OU JSON.
    Champs acceptés:
      - file_id (OBLIGATOIRE)
      - output_name (optionnel, .mp4 forcé si absent)
      - drive_folder_id (optionnel, upload si fourni)
    """
    workdir = tempfile.mkdtemp(prefix="preenc_")
    req_id = str(uuid4())
    try:
        data = request.get_json(silent=True) or {}
        def pick(*keys):
            for k in keys:
                v = request.form.get(k)
                if v: return v
            for k in keys:
                v = data.get(k)
                if v: return v
            return None

        file_id = pick("file_id", "id")
        output_name = pick("output_name", "out_name") or "preencoded.mp4"
        if not output_name.lower().endswith(".mp4"):
            output_name += ".mp4"
        drive_folder_id = pick("drive_folder_id", "out_folder_id", "folder_id")

        if not file_id:
            raise ValueError("Missing 'file_id'")

        # 1) Résolution des raccourcis éventuels
        try:
            resolved_id = _drive_resolve_target_id(file_id)
        except Exception as e:
            app.logger.error(f"[{req_id}] resolve_id failed for {file_id}: {e}")
            resolved_id = file_id

        # 2) Téléchargement via API Drive
        local_src = os.path.join(workdir, "src")
        svc = _gdrive_service()
        try:
            req = svc.files().get_media(fileId=resolved_id, supportsAllDrives=True)
            with open(local_src, "wb") as f:
                downloader = MediaIoBaseDownload(f, req)
                done = False
                while not done:
                    _status, done = downloader.next_chunk()
        except Exception as e:
            sa = _sa_email()
            app.logger.error(
                f"[{req_id}] Drive download 404/perm for file_id={file_id} resolved={resolved_id}. "
                f"Probablement pas partagé avec {sa}. Erreur: {e}"
            )
            raise

        # 3) Encodage 1080x1920@30 H.264 (durée 30s comme avant)
        local_out = os.path.join(workdir, output_name)
        _encode_uniform(local_src, local_out, 1080, 1920, 30, 30.0, app.logger, req_id)

        # 4) Upload Drive (si demandé)
        if drive_folder_id:
            gd = _gdrive_upload(local_out, output_name, drive_folder_id, app.logger, req_id)
            return jsonify({
                "status": "success",
                "message": "✅ Encodage terminé : 1080x1920 @30fps H.264",
                "drive_file_id": gd.get("id"),
                "drive_webViewLink": gd.get("webViewLink"),
                "drive_webContentLink": gd.get("webContentLink"),
            })
        return jsonify({
            "status": "success",
            "message": "✅ Encodage terminé : 1080x1920 @30fps H.264",
            "local_path": local_out
        })
    except Exception as e:
        app.logger.error(f"[{req_id}] pre-encode failed: {e}\n{traceback.format_exc()}")
        return jsonify(error=str(e)), 500
    finally:
        if not KEEP_TMP:
            shutil.rmtree(workdir, ignore_errors=True)

# -------------------- CREATE-VIDEO (SYNC) --------------------
@app.post("/create-video")
def create_video():
    """
    multipart/form-data attendu:
      - output_name (Text)
      - width, height, fps (Text)
      - plan (Text) : string JSON (voir Make -> toJSON(...))
      - audio_file (File) : MP3 voix
      - global_srt (Text, optionnel)
      - style, subtitle_mode, word_mode, burn_mode (optionnels)
      - music_folder_id, music_volume, music_delay (optionnels)
      - drive_folder_id (Text, optionnel) : upload final
    """
    workdir = None
    try:
        output_name = request.form["output_name"]
        width  = _parse_int(request.form.get("width", 1080), 1080)
        height = _parse_int(request.form.get("height", 1920), 1920)
        fps    = _parse_int(request.form.get("fps", 30), 30)

        plan_str = request.form["plan"]
        plan = _normalize_plan(plan_str)

        audio_file = request.files["audio_file"]
        global_srt = request.form.get("global_srt")
        style = request.form.get("style") or "default"           # 'default' | 'philo'
        subtitle_mode = request.form.get("subtitle_mode") or "sentence"  # 'sentence' | 'word'
        word_mode = request.form.get("word_mode") or "accumulate"        # 'accumulate' | 'replace'
        burn_mode = request.form.get("burn_mode") or "segment"    # 'segment' | 'none'

        drive_folder_id = request.form.get("drive_folder_id")
        music_folder_id = request.form.get("music_folder_id")
        music_volume = _parse_float(request.form.get("music_volume", 0.25), 0.25)
        music_delay  = _parse_int(request.form.get("music_delay", 0), 0)

        req_id = g.req_id
        app.logger.info(f"[{req_id}] create-video name={output_name} {width}x{height}@{fps} audio={getattr(audio_file,'filename',None)}")

        try:
            total_dur = sum(float(max(0.0, (seg.get("duration") or 0))) for seg in plan)
        except Exception:
            total_dur = 0.0
        free_tmp = shutil.disk_usage("/tmp").free // (1024*1024)
        app.logger.info(f"[{req_id}] preflight tmp: free={free_tmp}MB need≈~{int(total_dur*0.35)}MB")

        workdir = tempfile.mkdtemp(prefix="fusionbot_")
        debug_dir = os.path.join(workdir, "debug")
        os.makedirs(debug_dir, exist_ok=True)

        audio_path = os.path.join(workdir, "voice.mp3")
        audio_file.save(audio_path)
        audio_size = os.path.getsize(audio_path)
        audio_dur = _ffprobe_duration(audio_path)
        app.logger.info(f"[{req_id}] audio path={audio_path} size={audio_size}B dur={audio_dur:.3f}s")

        with open(os.path.join(debug_dir, "plan_input.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(plan, ensure_ascii=False, indent=2))
        if global_srt:
            with open(os.path.join(debug_dir, "global_srt.srt"), "w", encoding="utf-8") as f:
                f.write(global_srt)

        music_path, music_start = (None, 0)
        if music_folder_id:
            music_path, music_start = _gdrive_pick_and_download_music(music_folder_id, workdir, app.logger, req_id)
            if music_path:
                app.logger.info(f"[{req_id}] music picked: {music_path} @ {music_start}s vol={music_volume}")

        final_path, dbg = generate_video(
            plan=plan,
            audio_path=audio_path,
            output_name=output_name,
            temp_dir=workdir,
            width=width, height=height, fps=fps,
            logger=app.logger, req_id=req_id,
            sub_style=None,            # géré par style
            style=style,
            subtitle_mode=subtitle_mode,
            word_mode=word_mode,
            global_srt=global_srt,
            burn_mode=burn_mode,
            music_path=music_path,
            music_delay=music_start,
            music_volume=music_volume
        )

        resp = {"status":"success","output_path":final_path,"width":width,"height":height,"fps":fps, **dbg}
        if drive_folder_id:
            up = _gdrive_upload(final_path, output_name, drive_folder_id, app.logger, req_id)
            resp.update({"drive_file_id": up.get("id"), "drive_webViewLink": up.get("webViewLink"), "drive_webContentLink": up.get("webContentLink")})

        return jsonify(resp)

    except Exception as e:
        app.logger.error(f"[{g.req_id}] create-video failed: {e}\n{traceback.format_exc()}")
        return jsonify(error=str(e)), 500
    finally:
        if workdir and not KEEP_TMP:
            shutil.rmtree(workdir, ignore_errors=True)

# -------------------- CREATE-VIDEO (ASYNC + JOBS) --------------------
JOBS: Dict[str, Dict[str, Any]] = {}
JLOCK = Lock()

def _set_job(jid: str, **kw):
    with JLOCK:
        JOBS[jid] = {**JOBS.get(jid, {}), **kw}

def _worker_create_video(jid: str, fields: Dict[str, Any]):
    req_id = fields.get("req_id", str(uuid4()))
    _set_job(jid, status="running", started_at=int(time.time()), req_id=req_id)
    workdir = None
    try:
        workdir = tempfile.mkdtemp(prefix=f"job_{jid}_")
        audio_path = os.path.join(workdir, "voice.mp3")
        with open(audio_path, "wb") as f:
            f.write(fields["audio_bytes"])

        plan = fields["plan"]
        width, height, fps = fields["width"], fields["height"], fields["fps"]
        output_name = fields["output_name"]
        style = fields.get("style","default")
        subtitle_mode = fields.get("subtitle_mode","sentence")
        word_mode = fields.get("word_mode","accumulate")
        burn_mode = fields.get("burn_mode","segment")
        global_srt = fields.get("global_srt")

        music_path, music_start = (None, 0)
        if fields.get("music_folder_id"):
            music_path, music_start = _gdrive_pick_and_download_music(fields["music_folder_id"], workdir, app.logger, req_id)

        final_path, dbg = generate_video(
            plan=plan,
            audio_path=audio_path,
            output_name=output_name,
            temp_dir=workdir,
            width=width, height=height, fps=fps,
            logger=app.logger, req_id=req_id,
            style=style, subtitle_mode=subtitle_mode, word_mode=word_mode,
            global_srt=global_srt, burn_mode=burn_mode,
            music_path=music_path, music_delay=music_start, music_volume=float(fields.get("music_volume", 0.25))
        )

        out = {"status":"success","output_path":final_path,"width":width,"height":height,"fps":fps, **dbg}

        if fields.get("drive_folder_id"):
            up = _gdrive_upload(final_path, output_name, fields["drive_folder_id"], app.logger, req_id)
            out.update({"drive_file_id": up.get("id"), "drive_webViewLink": up.get("webViewLink"), "drive_webContentLink": up.get("webContentLink")})

        _set_job(jid, status="done", finished_at=int(time.time()), result=out)

        cb = fields.get("callback_url")
        if cb and _requests:
            try:
                _requests.post(cb, json={**out, "job_id": jid, "req_id": req_id}, timeout=10)
            except Exception as e:
                app.logger.warning(f"[{req_id}] callback post failed: {e}")

    except Exception as e:
        _set_job(jid, status="error", finished_at=int(time.time()),
                 error=str(e), trace=traceback.format_exc())
        app.logger.error(f"[{req_id}] worker failed: {e}\n{traceback.format_exc()}")
    finally:
        if workdir and not KEEP_TMP:
            shutil.rmtree(workdir, ignore_errors=True)

@app.post("/create-video-async")
def create_video_async():
    jid = request.form.get("job_id") or str(uuid4())
    req_id = request.headers.get("X-Request-ID", str(uuid4()))
    tmp = tempfile.mkdtemp(prefix=f"enqueue_{jid}_")

    try:
        audio = request.files["audio_file"]
        audio_local = os.path.join(tmp, "voice.mp3")
        audio.save(audio_local)
        with open(audio_local, "rb") as f:
            audio_bytes = f.read()

        plan = _normalize_plan(request.form["plan"])
        fields = {
            "req_id": req_id,
            "output_name": request.form["output_name"],
            "width": _parse_int(request.form.get("width", 1080), 1080),
            "height": _parse_int(request.form.get("height", 1920), 1920),
            "fps": _parse_int(request.form.get("fps", 30), 30),
            "plan": plan,
            "audio_bytes": audio_bytes,
            "global_srt": request.form.get("global_srt"),
            "style": request.form.get("style") or "default",
            "subtitle_mode": request.form.get("subtitle_mode") or "sentence",
            "word_mode": request.form.get("word_mode") or "accumulate",
            "burn_mode": request.form.get("burn_mode") or "segment",
            "drive_folder_id": request.form.get("drive_folder_id"),
            "music_folder_id": request.form.get("music_folder_id"),
            "music_volume": _parse_float(request.form.get("music_volume", 0.25), 0.25),
            "music_delay": _parse_int(request.form.get("music_delay", 0), 0),
            "callback_url": request.form.get("callback_url"),
        }

        _set_job(jid, status="queued", enqueued_at=int(time.time()), req_id=req_id)
        t = Thread(target=_worker_create_video, args=(jid, fields), daemon=True)
        t.start()
        return jsonify({"status": "queued", "job_id": jid}), 202

    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        app.logger.error(f"[{req_id}] enqueue failed: {e}\n{traceback.format_exc()}")
        return jsonify(error="enqueue_failed", detail=str(e)), 400

@app.get("/jobs/<job_id>")
def get_job(job_id: str):
    with JLOCK:
        data = JOBS.get(job_id)
    if not data:
        return jsonify(error="not_found"), 404
    return jsonify(data)

@app.get("/jobs")
def list_jobs():
    with JLOCK:
        items = [
            {k: v for k, v in j.items() if k in ("job_id","status","req_id","enqueued_at","started_at","finished_at")}
            for j in JOBS.values()
        ]
    return jsonify(items)

# -------------------- main --------------------
if __name__ == "__main__":
    app.logger.info(f"Service Account email: {_sa_email()}")
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
