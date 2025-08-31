# main.py — API sync + async (jobs) pour création vidéo
import os, json, time, tempfile, logging, shutil, subprocess, traceback, random, re
from uuid import uuid4
from typing import Any, Dict, List, Optional, Tuple
from flask import Flask, request, jsonify, g

from threading import Thread, Lock
try:
    import requests as _requests  # pour le callback
except Exception:
    _requests = None
import urllib.request, urllib.error

from video_generator import generate_video

# --- Google Drive (clé dans Secret Files : credentials.json) ---
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
# ---------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
KEEP_TMP  = os.getenv("KEEP_TMP", "1") == "1"   # garder /tmp par défaut

app = Flask(__name__)
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
app.logger.setLevel(logging.getLogger().level)

# -------------------- Google Drive helpers --------------------
def _gdrive_service():
    """
    IMPORTANT: scope 'drive' (ou 'drive.readonly') pour lister les fichiers
    PARTAGÉS au service account. 'drive.file' ne suffit pas.
    """
    path = os.getenv("GOOGLE_CREDS", "/etc/secrets/credentials.json")
    scopes = [
        "https://www.googleapis.com/auth/drive",  # plein accès (upload + list + download)
        # si tu veux limiter en lecture seule, remplace par:
        # "https://www.googleapis.com/auth/drive.readonly",
    ]
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
    """
    Choisit un fichier audio aléatoire dans un dossier Drive et le télécharge.
    Retourne (chemin_local, delay_sec). Le délai est détecté via un suffixe @NN
    juste avant l’extension (ex: 'MaZic@55.mp3' -> delay_sec=55).
    """
    try:
        svc = _gdrive_service()

        # Requête plus large: audio/* ou extension mp3/wav/m4a
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

        # extrait un éventuel @NN avant l'extension -> seconds
        delay_sec = 0
        m = re.search(r"@(\d+)(?=\.[^.]+$)", fname)
        if m:
            try:
                delay_sec = int(m.group(1))
            except Exception:
                delay_sec = 0

        return local, delay_sec
    except Exception as e:
        logger.exception(f"[{req_id}] erreur download musique: {e}")
        return None, 0
# ----------------------------------------------------

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
    try:
        return int(s)
    except Exception:
        return default

def _parse_float(s: Any, default: float) -> float:
    try:
        return float(s)
    except Exception:
        return default

def _normalize_plan(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8","ignore")
    if isinstance(raw, str):
        try:
            if LOG_LEVEL == "DEBUG":
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

@app.get("/")
def root():
    return jsonify(ok=True, service="fusion-bot", ts=int(time.time()))

# =========================
#         SYNC API
# =========================
@app.post("/create-video")
def create_video():
    workdir = None
    try:
        output_name = request.form["output_name"]
        width  = _parse_int(request.form.get("width", 1080), 1080)
        height = _parse_int(request.form.get("height", 1920), 1920)
        fps    = _parse_int(request.form.get("fps", 30), 30)
        plan_str = request.form["plan"]
        audio_file = request.files["audio_file"]
        global_srt = request.form.get("global_srt")
        burn_mode  = request.form.get("burn_mode", os.getenv("BURN_MODE", "segment"))
        burn_flag  = str(request.form.get("burn_subs", "1")).lower().strip()
        burn_active = burn_flag not in ("0", "false", "no", "none", "")

        # >>>>> NOUVEAU : style / sous-titres
        style         = request.form.get("style", "default")
        subtitle_mode = request.form.get("subtitle_mode", "sentence")
        word_mode     = request.form.get("word_mode", "accumulate")

        # musique optionnelle
        music_folder_id = request.form.get("music_folder_id")
        music_volume    = _parse_float(request.form.get("music_volume", 0.25), 0.25)

        drive_folder_id = request.form.get("drive_folder_id") or request.args.get("drive_folder_id")

        app.logger.info(f"[{g.req_id}] fields ok name={output_name} {width}x{height}@{fps} audio={getattr(audio_file,'filename',None)} style={style} smode={subtitle_mode} wmode={word_mode}")

        plan = _normalize_plan(plan_str)

        try:
            total_dur = sum(float(max(0.0, (seg.get("duration") or 0))) for seg in plan)
        except Exception:
            total_dur = 0.0
        free_tmp = shutil.disk_usage("/tmp").free // (1024*1024)
        app.logger.info(f"[{g.req_id}] preflight tmp: free={free_tmp}MB need≈~{int(total_dur*0.35)}MB")

        workdir = tempfile.mkdtemp(prefix="fusionbot_")
        debug_dir = os.path.join(workdir, "debug")
        os.makedirs(debug_dir, exist_ok=True)

        # audio voix
        audio_path = os.path.join(workdir, "voice.mp3")
        audio_file.save(audio_path)
        audio_size = os.path.getsize(audio_path)
        audio_dur  = _ffprobe_duration(audio_path)
        app.logger.info(f"[{g.req_id}] audio path={audio_path} size={audio_size}B dur={audio_dur:.3f}s")

        # musique BG (si dossier fourni)
        music_path, music_delay = (None, 0)
        if music_folder_id:
            music_path, music_delay = _gdrive_pick_and_download_music(music_folder_id, workdir, app.logger, g.req_id)
            if music_path:
                app.logger.info(f"[{g.req_id}] musique DL ok -> {music_path} delay={music_delay}s vol={music_volume}")

        with open(os.path.join(debug_dir, "plan_input.json"), "w", encoding="utf-8") as f:
            json.dump({"plan": plan}, f, ensure_ascii=False, indent=2)

        out_path, gen_debug = generate_video(
            plan=plan,
            audio_path=audio_path,
            output_name=output_name,
            temp_dir=workdir,
            width=width, height=height, fps=fps,
            logger=app.logger, req_id=g.req_id,
            global_srt=global_srt,
            burn_mode=("none" if not burn_active else burn_mode),
            # >>>>> NOUVEAU : style / sous-titres
            style=style,
            subtitle_mode=subtitle_mode,
            word_mode=word_mode,
            # musique
            music_path=music_path,
            music_delay=music_delay,
            music_volume=music_volume,
        )

        out_size = os.path.getsize(out_path)
        out_dur  = _ffprobe_duration(out_path)
        app.logger.info(f"[{g.req_id}] OUTPUT path={out_path} size={out_size}B dur={out_dur:.3f}s")

        resp = {
            "status":"success","output_path":out_path,
            "width":width,"height":height,"fps":fps,"items":len(plan),
            "out_size": out_size, "out_duration": out_dur,
            "debug": gen_debug
        }

        if drive_folder_id:
            try:
                gd = _gdrive_upload(out_path, output_name, drive_folder_id, app.logger, g.req_id)
                resp.update({"drive_file_id": gd.get("id"), "drive_webViewLink": gd.get("webViewLink")})
            except Exception as e:
                app.logger.exception(f"[{g.req_id}] drive upload failed: {e}")
                resp["drive_error"] = str(e)

        return jsonify(resp)

    except Exception as e:
        app.logger.error(f"[{getattr(g,'req_id','?')}] create-video failed: {e}\n{traceback.format_exc()}")
        return jsonify(error="internal error", detail=str(e)), 500
    finally:
        try:
            if not KEEP_TMP and os.getenv("CLEAN_TMP") == "1" and workdir and os.path.isdir(workdir):
                shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

# =========================
#        ASYNC API
# =========================

JOBS: Dict[str, Dict[str, Any]] = {}
JLOCK = Lock()

def _set_job(jid: str, **kw):
    with JLOCK:
        JOBS[jid] = {**JOBS.get(jid, {}), **kw}

def _post_callback(url: str, payload: Dict[str, Any]):
    try:
        if _requests:
            _requests.post(url, json=payload, timeout=10)
        else:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass

def _worker_create_video(jid: str, fields: Dict[str, Any]):
    workdir = None
    req_id = fields.get("req_id", jid)
    callback_url = fields.get("callback_url")
    try:
        with app.app_context():
            g.req_id = req_id

            output_name   = fields["output_name"]
            width         = _parse_int(fields.get("width", 1080), 1080)
            height        = _parse_int(fields.get("height", 1920), 1920)
            fps           = _parse_int(fields.get("fps", 30), 30)
            plan_str      = fields["plan"]
            audio_srcpath = fields["audio_path"]
            global_srt    = fields.get("global_srt")
            burn_mode     = fields.get("burn_mode", os.getenv("BURN_MODE", "segment"))
            burn_flag     = str(fields.get("burn_subs", "1")).lower().strip()
            burn_active   = burn_flag not in ("0", "false", "no", "none", "")

            # >>>>> NOUVEAU : style / sous-titres
            style         = fields.get("style", "default")
            subtitle_mode = fields.get("subtitle_mode", "sentence")
            word_mode     = fields.get("word_mode", "accumulate")

            drive_folder_id = fields.get("drive_folder_id")

            # musique
            music_folder_id = fields.get("music_folder_id")
            music_volume    = _parse_float(fields.get("music_volume", 0.25), 0.25)

            app.logger.info(f"[{req_id}] (async) start job {jid} name={output_name} {width}x{height}@{fps} style={style} smode={subtitle_mode} wmode={word_mode}")

            plan = _normalize_plan(plan_str)

            try:
                total_dur = sum(float(max(0.0, (seg.get("duration") or 0))) for seg in plan)
            except Exception:
                total_dur = 0.0
            free_tmp = shutil.disk_usage("/tmp").free // (1024*1024)
            app.logger.info(f"[{req_id}] preflight tmp: free={free_tmp}MB need≈~{int(total_dur*0.35)}MB")

            workdir = tempfile.mkdtemp(prefix=f"fusionjob_{jid}_")
            debug_dir = os.path.join(workdir, "debug")
            os.makedirs(debug_dir, exist_ok=True)

            # voix
            audio_path = os.path.join(workdir, "voice.mp3")
            shutil.copy2(audio_srcpath, audio_path)
            audio_size = os.path.getsize(audio_path)
            audio_dur  = _ffprobe_duration(audio_path)
            app.logger.info(f"[{req_id}] audio path={audio_path} size={audio_size}B dur={audio_dur:.3f}s")

            # musique BG
            music_path, music_delay = (None, 0)
            if music_folder_id:
                music_path, music_delay = _gdrive_pick_and_download_music(music_folder_id, workdir, app.logger, req_id)
                if music_path:
                    app.logger.info(f"[{req_id}] musique DL ok -> {music_path} delay={music_delay}s vol={music_volume}")

            with open(os.path.join(debug_dir, "plan_input.json"), "w", encoding="utf-8") as f:
                json.dump({"plan": plan}, f, ensure_ascii=False, indent=2)

            _set_job(jid, status="running", stage="encoding", updated_at=int(time.time()))

            out_path, gen_debug = generate_video(
                plan=plan,
                audio_path=audio_path,
                output_name=output_name,
                temp_dir=workdir,
                width=width, height=height, fps=fps,
                logger=app.logger, req_id=req_id,
                global_srt=global_srt,
                burn_mode=("none" if not burn_active else burn_mode),
                # >>>>> NOUVEAU : style / sous-titres
                style=style,
                subtitle_mode=subtitle_mode,
                word_mode=word_mode,
                # musique
                music_path=music_path,
                music_delay=music_delay,
                music_volume=music_volume,
            )

            out_size = os.path.getsize(out_path)
            out_dur  = _ffprobe_duration(out_path)
            app.logger.info(f"[{req_id}] (async) OUTPUT path={out_path} size={out_size}B dur={out_dur:.3f}s")

            with open(os.path.join(debug_dir, "generator_debug.json"), "w", encoding="utf-8") as f:
                json.dump(gen_debug, f, ensure_ascii=False, indent=2)

            result = {
                "status": "success",
                "job_id": jid,
                "output_path": out_path,
                "width": width, "height": height, "fps": fps,
                "items": len(plan),
                "out_size": out_size, "out_duration": out_dur,
                "workdir": workdir,
                "req_id": req_id,
            }

            if drive_folder_id:
                try:
                    gd = _gdrive_upload(out_path, output_name, drive_folder_id, app.logger, req_id)
                    result.update({
                        "drive_file_id": gd.get("id"),
                        "drive_webViewLink": gd.get("webViewLink"),
                    })
                except Exception as e:
                    app.logger.exception(f"[{req_id}] drive upload failed: {e}")
                    result["drive_error"] = str(e)

            _set_job(jid, **result)

            if callback_url:
                _post_callback(callback_url, {"job_id": jid, **result})

    except Exception as e:
        err = {"status": "error", "job_id": jid, "message": str(e), "req_id": req_id}
        _set_job(jid, **err)
        app.logger.error(f"[{req_id}] job error: {e}\n{traceback.format_exc()}")
        if callback_url:
            try: _post_callback(callback_url, {"job_id": jid, **err})
            except Exception: pass
    finally:
        try:
            if not KEEP_TMP and os.getenv("CLEAN_TMP") == "1" and workdir and os.path.isdir(workdir):
                shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

@app.post("/create-video-async")
def create_video_async():
    jid = request.form.get("job_id") or str(uuid4())
    req_id = request.headers.get("X-Request-ID", str(uuid4()))
    tmp = tempfile.mkdtemp(prefix=f"enqueue_{jid}_")

    try:
        audio = request.files["audio_file"]
        audio_local = os.path.join(tmp, "voice.mp3")
        audio.save(audio_local)

        fields = {
            "req_id": req_id,
            "output_name": request.form["output_name"],
            "width": request.form.get("width"),
            "height": request.form.get("height"),
            "fps": request.form.get("fps"),
            "plan": request.form["plan"],
            "audio_path": audio_local,
            "global_srt": request.form.get("global_srt"),
            "burn_mode": request.form.get("burn_mode", os.getenv("BURN_MODE", "segment")),
            "burn_subs": request.form.get("burn_subs", "1"),
            "drive_folder_id": request.form.get("drive_folder_id") or request.args.get("drive_folder_id"),
            "callback_url": request.form.get("callback_url"),
            # >>>>> NOUVEAU : style / sous-titres
            "style": request.form.get("style"),
            "subtitle_mode": request.form.get("subtitle_mode"),
            "word_mode": request.form.get("word_mode"),
            # musique
            "music_folder_id": request.form.get("music_folder_id"),
            "music_volume": request.form.get("music_volume"),
        }

        _set_job(jid, status="queued", job_id=jid, req_id=req_id, enqueued_at=int(time.time()))
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
        return jsonify(error="not_found", job_id=job_id), 404
    return jsonify(data)

@app.get("/jobs")
def list_jobs():
    with JLOCK:
        items = [
            {k: v for k, v in j.items() if k in ("job_id","status","req_id","enqueued_at")}
            for j in JOBS.values()
        ]
    return jsonify(items)

# =========================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
