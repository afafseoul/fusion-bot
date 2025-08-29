# main.py — API sync + async (jobs) pour création vidéo
import os, json, time, tempfile, logging, shutil, subprocess, traceback
from uuid import uuid4
from typing import Any, Dict, List, Optional
from flask import Flask, request, jsonify, g

from threading import Thread, Lock
try:
    import requests as _requests  # pour le callback
except Exception:  # requests non présent ? on dégradera plus bas
    _requests = None
import urllib.request, urllib.error

from video_generator import generate_video

# --- Google Drive (clé dans Secret Files : credentials.json) ---
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
# ---------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
KEEP_TMP  = os.getenv("KEEP_TMP", "1") == "1"   # on garde par défaut pour debug

app = Flask(__name__)
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
app.logger.setLevel(logging.getLogger().level)

# -------------------- Google Drive helpers --------------------
def _gdrive_service():
    path = os.getenv("GOOGLE_CREDS", "/etc/secrets/credentials.json")
    scopes = ["https://www.googleapis.com/auth/drive.file"]
    creds = Credentials.from_service_account_file(path, scopes=scopes)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def _gdrive_upload(file_path: str, file_name: str, folder_id: Optional[str], logger, req_id: str):
    svc = _gdrive_service()
    meta = {"name": file_name}
    if folder_id:
        meta["parents"] = [folder_id]
    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=False)
    resp = svc.files().create(body=meta, media_body=media,
                              fields="id,webViewLink,webContentLink").execute()
    logger.info(f"[{req_id}] gdrive upload ok id={resp.get('id')} webViewLink={resp.get('webViewLink')}")
    return resp
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
    return {"status": "ok", "service": "fusion-bot"}, 200

# =========================
#         SYNC API
# =========================
@app.get("/")
def health():
    return jsonify(ok=True, ts=int(time.time()))

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
        burn_mode = request.form.get("burn_mode", os.getenv("BURN_MODE", "none"))
        drive_folder_id = request.form.get("drive_folder_id") or request.args.get("drive_folder_id")

        app.logger.info(f"[{g.req_id}] fields ok name={output_name} {width}x{height}@{fps} audio={getattr(audio_file,'filename',None)}")

        plan = _normalize_plan(plan_str)

        # indicatif disque
        try:
            total_dur = sum(float(max(0.0, (seg.get("duration") or 0))) for seg in plan)
        except Exception:
            total_dur = 0.0
        free_tmp = shutil.disk_usage("/tmp").free // (1024*1024)
        app.logger.info(f"[{g.req_id}] preflight tmp: free={free_tmp}MB need≈~{int(total_dur*0.35)}MB")

        workdir = tempfile.mkdtemp(prefix="fusionbot_")
        debug_dir = os.path.join(workdir, "debug")
        os.makedirs(debug_dir, exist_ok=True)

        # audio
        audio_path = os.path.join(workdir, "voice.mp3")
        audio_file.save(audio_path)
        audio_size = os.path.getsize(audio_path)
        audio_dur  = _ffprobe_duration(audio_path)
        app.logger.info(f"[{g.req_id}] audio path={audio_path} size={audio_size}B dur={audio_dur:.3f}s")

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
            burn_subs=(burn_mode != "none"),
        )

        out_size = os.path.getsize(out_path)
        out_dur  = _ffprobe_duration(out_path)
        app.logger.info(f"[{g.req_id}] OUTPUT path={out_path} size={out_size}B dur={out_dur:.3f}s")

        # bundle réponse (sans upload)
        resp = {
            "status":"success","output_path":out_path,
            "width":width,"height":height,"fps":fps,"items":len(plan),
            "out_size": out_size, "out_duration": out_dur,
            "debug": gen_debug
        }

        # upload optionnel
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

# Mémoire très simple en RAM pour le suivi des jobs
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
    """Exécute la création vidéo hors requête HTTP (thread)."""
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
            audio_srcpath = fields["audio_path"]  # déjà sauvé par le endpoint
            global_srt    = fields.get("global_srt")
            burn_mode     = fields.get("burn_mode", os.getenv("BURN_MODE", "none"))
            drive_folder_id = fields.get("drive_folder_id")

            app.logger.info(f"[{req_id}] (async) start job {jid} name={output_name} {width}x{height}@{fps}")

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

            # copie/rename audio dans le workdir pour uniformiser avec logique sync
            audio_path = os.path.join(workdir, "voice.mp3")
            shutil.copy2(audio_srcpath, audio_path)
            audio_size = os.path.getsize(audio_path)
            audio_dur  = _ffprobe_duration(audio_path)
            app.logger.info(f"[{req_id}] audio path={audio_path} size={audio_size}B dur={audio_dur:.3f}s")

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
                burn_subs=(burn_mode != "none"),
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

            # Upload Drive si demandé
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
    """Enqueue du job + sauvegarde rapide des assets pour le worker"""
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
            "burn_mode": request.form.get("burn_mode", os.getenv("BURN_MODE", "none")),
            "drive_folder_id": request.form.get("drive_folder_id") or request.args.get("drive_folder_id"),
            "callback_url": request.form.get("callback_url"),
        }

        _set_job(jid, status="queued", job_id=jid, req_id=req_id, enqueued_at=int(time.time()))
        t = Thread(target=_worker_create_video, args=(jid, fields), daemon=True)
        t.start()

        return jsonify({"status": "queued", "job_id": jid}), 202

    except Exception as e:
        # si l’enqueue échoue
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
        # renvoie un snapshot minimal
        items = [
            {k: v for k, v in j.items() if k in ("job_id","status","req_id","enqueued_at")}
            for j in JOBS.values()
        ]
    return jsonify(items)

# =========================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
