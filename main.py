# main.py — API sync + async (jobs) pour création vidéo, sans sous-titres (full encode dans le job)
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

from video_generator import generate_video
from captions import build_ass_from_srt

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
KEEP_TMP  = os.getenv("KEEP_TMP", "1") == "1"

# Fallback global vers ton webhook Make si rien n'est fourni dans la requête
# (tu peux aussi le surcharger via la variable d'env FINISH_WEBHOOK)
DEFAULT_FINISH_WEBHOOK = os.getenv(
    "DEFAULT_FINISH_WEBHOOK_URL",
    "https://hook.eu2.make.com/5mjade7l6ys678hrwenbtvxc645y2hlx"
)

app = Flask(__name__)
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
app.logger.setLevel(LOG_LEVEL)

JOBS: Dict[str, Dict[str, Any]] = {}
JLOCK = Lock()

def _parse_int(s: Any, default: int) -> int:
    try: return int(s)
    except Exception: return default

def _parse_float(s: Any, default: float) -> float:
    try: return float(s)
    except Exception: return default

# -------------------- CORRECTION (impersonation) --------------------
def _gdrive_service():
    """
    Utilise le Service Account avec Domain-Wide Delegation pour créer les fichiers
    AU NOM de l'utilisateur Workspace (owner_email).
    """
    path = os.getenv("GOOGLE_CREDS", "/etc/secrets/credentials.json")
    scopes = ["https://www.googleapis.com/auth/drive"]
    owner_email = os.getenv("OWNER_EMAIL", "ktrium@wwwjeneveuxpastravailler.com")
    creds = Credentials.from_service_account_file(path, scopes=scopes, subject=owner_email)
    return build("drive", "v3", credentials=creds, cache_discovery=False)
# --------------------------------------------------------------------

def _gdrive_upload(file_path: str, file_name: str, folder_id: Optional[str], logger, req_id: str):
    svc = _gdrive_service()
    meta = {"name": file_name}
    if folder_id: meta["parents"] = [folder_id]
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
            if not page_token: break

        if not files:
            logger.warning(f"[{req_id}] ⚠️ aucun fichier audio trouvé dans le dossier {folder_id}")
            return None, 0

        import random, re
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

def _normalize_plan(plan_str: str) -> List[Dict[str, Any]]:
    """
    Normalise l'entrée "plan" reçue depuis Make pour la passer à generate_video().
    Accepte:
      - un string JSON (objet avec {"plan":[...]} ou directement un array [...]),
      - un dict (avec clé "plan" ou directement la liste),
    Tolérance d'erreur:
        # Tolérance: tenter d'extraire un objet { ... } ou un array [ ... ] imbriqué dans du bruit
            obj_s, arr_s = None, None
    """
    try:
        if isinstance(plan_str, (dict, list)):
            raw = plan_str
        else:
            try:
                raw = json.loads(plan_str)
            except Exception:
                # Tolérance: tenter d'extraire un objet { ... } ou un array [ ... ] imbriqué dans du bruit
                obj_s, arr_s = None, None
                m = re.search(r"(\{.*\})", plan_str, re.DOTALL)
                if m: obj_s = m.group(1)
                m = re.search(r"(\[.*\])", plan_str, re.DOTALL)
                if m: arr_s = m.group(1)
                if obj_s:
                    raw = json.loads(obj_s)
                elif arr_s:
                    raw = json.loads(arr_s)
                else:
                    raise

        if isinstance(raw, dict) and "plan" in raw:
            segments = raw["plan"]
        elif isinstance(raw, list):
            segments = raw
        elif isinstance(raw, dict):
            segments = [raw]
        else:
            raise ValueError("plan must be JSON (object with 'plan' or array of segments)")

        if not isinstance(segments, list):
            raise ValueError("plan must be a JSON array")
        if not segments:
            raise ValueError("plan is empty")

        out: List[Dict[str, Any]] = []
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            s = dict(seg)

            if not s.get("gif_url"):
                for alt in ("url", "video_url", "source_url"):
                    v = s.get(alt)
                    if isinstance(v, str) and v:
                        s["gif_url"] = v
                        break

            if "duration" in s:
                try: s["duration"] = float(s["duration"])
                except Exception: s["duration"] = None

            out.append(s)
        return out
    except Exception as e:
        raise ValueError(f"invalid plan: {e}")

def _post_finish_webhook(url: Optional[str], success: bool, output_name: str, compte: Optional[str], contenue: Optional[str]):
    if not url or not _requests:
        return
    try:
        payload = {
            "success": success,
            "file_name": output_name,
            "compte": compte,
            "Contenue": contenue,
        }
        _requests.post(url, json=payload, timeout=10)
    except Exception:
        pass

def _resolve_finish_webhook_from_request(req) -> Optional[str]:
    w = (
        (req.form.get("finish_webhook") if hasattr(req, "form") else None)
        or (req.args.get("finish_webhook") if hasattr(req, "args") else None)
        or os.getenv("FINISH_WEBHOOK")
        or DEFAULT_FINISH_WEBHOOK
    )
    if w and str(w).strip().lower() in ("", "none", "false", "0"):
        return None
    return w

# ---------------- SYNC ----------------
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

        style           = request.form.get("style", "default")
        caption_style   = request.form.get("caption_style")
        srt_text        = request.form.get("srt_text")
        music_folder_id = request.form.get("music_folder_id")
        music_volume    = _parse_float(request.form.get("music_volume", 0.25), 0.25)
        drive_folder_id = request.form.get("drive_folder_id") or request.args.get("drive_folder_id")
        finish_webhook  = _resolve_finish_webhook_from_request(request)
        # 🆕 compte (nom du compte) passé par Make dans les inputs
        compte          = request.form.get("compte") or request.args.get("compte")
        # 🆕 narration passée depuis Make (clé exactement "Contenue")
        contenue        = request.form.get("Contenue") or request.args.get("Contenue")

        # 🔀 NOUVEAU : permettre de désactiver les captions avec "0", "false", "off", "none", "no"
        if caption_style is not None:
            cs_norm = str(caption_style).strip().lower()
            if cs_norm in ("0", "false", "off", "none", "no"):
                caption_style = ""

        app.logger.info(f"[{g.req_id}] fields ok name={output_name} {width}x{height}@{fps} "
                        f"audio={getattr(audio_file,'filename',None)} style={style}")

        plan = _normalize_plan(plan_str)

        try:
            total_dur = sum(float(max(0.0, (seg.get("duration") or 0))) for seg in plan)
        except Exception:
            total_dur = 0.0
        free_tmp = shutil.disk_usage("/tmp").free // (1024*1024)
        app.logger.info(f"[{g.req_id}] preflight tmp: free={free_tmp} MiB, plan_items={len(plan)}, audio_name={getattr(audio_file,'filename',None)}")

        workdir = tempfile.mkdtemp(prefix="create_sync_")
        debug_dir = os.path.join(workdir, "debug")
        os.makedirs(debug_dir, exist_ok=True)

        audio_path = os.path.join(workdir, "voice.mp3")
        audio_file.save(audio_path)

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
            style=style,
            music_path=music_path,
            music_delay=music_delay,
            music_volume=music_volume,
        )

        # --- CAPTIONS: burn subtitles (optional) ---
        try:
            if caption_style and srt_text:
                ass_path = os.path.join(workdir if "workdir" in locals() and workdir else os.path.dirname(out_path), "captions.ass")
                ass_text = build_ass_from_srt(srt_text, preset=caption_style)
                with open(ass_path, "w", encoding="utf-8") as f:
                    f.write(ass_text)
                fps_val = int(request.form.get("fps") or 30)
                cmd = (
                    f'ffmpeg -y -i "{out_path}" -vf "subtitles={ass_path}" '
                    f'-c:v libx264 -preset veryfast -crf 22 -r {fps_val} -pix_fmt yuv420p '
                    f'-c:a copy -movflags +faststart "{out_path[:-4]}_sub.mp4"'
                )
                subprocess.check_call(cmd, shell=True)
                out_path = out_path[:-4] + "_sub.mp4"
        except Exception as e:
            app.logger.exception(f"[{g.req_id}] captions burn failed: {e}")
        # --- END CAPTIONS ---

        out_size = os.path.getsize(out_path)
        out_dur  = _ffprobe_duration(out_path)
        app.logger.info(f"[{g.req_id}] OUTPUT path={out_path} size={out_size}B dur={out_dur:.3f}s")

        with open(os.path.join(debug_dir, "generator_debug.json"), "w", encoding="utf-8") as f:
            json.dump(gen_debug, f, ensure_ascii=False, indent=2)

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
                if finish_webhook:
                    _post_finish_webhook(finish_webhook, True, output_name, compte, contenue)
            except Exception as e:
                app.logger.exception(f"[{g.req_id}] drive upload failed: {e}")
                resp["drive_error"] = str(e)
                if finish_webhook:
                    _post_finish_webhook(finish_webhook, False, output_name, compte, contenue)

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

def _set_job(jid: str, **kw):
    with JLOCK:
        JOBS[jid] = {**JOBS.get(jid, {}), **kw}

# ---------------- ASYNC ----------------
def _worker_create_video(jid: str, fields: Dict[str, Any]):
    workdir = None
    req_id = fields.get("req_id", jid)
    callback_url = fields.get("callback_url")
    finish_webhook = (
        fields.get("finish_webhook")
        or os.getenv("FINISH_WEBHOOK")
        or DEFAULT_FINISH_WEBHOOK
    )
    if finish_webhook and str(finish_webhook).strip().lower() in ("", "none", "false", "0"):
        finish_webhook = None

    try:
        with app.app_context():
            g.req_id = req_id

        output_name    = fields["output_name"]
        width          = _parse_int(fields.get("width") or 1080, 1080)
        height         = _parse_int(fields.get("height") or 1920, 1920)
        fps            = _parse_int(fields.get("fps") or 30, 30)
        plan_str       = fields["plan"]
        audio_path     = fields["audio_path"]
        drive_folder_id= fields.get("drive_folder_id")
        style          = fields.get("style", "default")
        music_folder_id= fields.get("music_folder_id")
        music_volume   = _parse_float(fields.get("music_volume") or 0.25, 0.25)
        compte         = fields.get("compte")
        contenue       = fields.get("Contenue")

        plan = _normalize_plan(plan_str)

        try:
            total_dur = sum(float(max(0.0, (seg.get("duration") or 0))) for seg in plan)
        except Exception:
            total_dur = 0.0

        workdir = tempfile.mkdtemp(prefix=f"job_{jid}_")
        debug_dir = os.path.join(workdir, "debug")
        os.makedirs(debug_dir, exist_ok=True)

        with open(os.path.join(debug_dir, "plan_input.json"), "w", encoding="utf-8") as f:
            json.dump({"plan": plan}, f, ensure_ascii=False, indent=2)

        music_path, music_delay = (None, 0)
        if music_folder_id:
            music_path, music_delay = _gdrive_pick_and_download_music(music_folder_id, workdir, app.logger, req_id)
            if music_path:
                app.logger.info(f"[{req_id}] musique DL ok -> {music_path} delay={music_delay}s vol={music_volume}")

        with open(os.path.join(debug_dir, "job_fields.json"), "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in fields.items() if k not in ("audio_path",)}, f, ensure_ascii=False, indent=2)

        _set_job(jid, status="running", stage="encoding", updated_at=int(time.time()))

        out_path, gen_debug = generate_video(
            plan=plan,
            audio_path=audio_path,
            output_name=output_name,
            temp_dir=workdir,
            width=width, height=height, fps=fps,
            logger=app.logger, req_id=req_id,
            style=style,
            music_path=music_path,
            music_delay=music_delay,
            music_volume=music_volume,
        )

        # --- CAPTIONS: burn subtitles (optional) ---
        try:
            caption_style = fields.get("caption_style")
            srt_text = fields.get("srt_text")

            # 🔀 NOUVEAU : même logique de désactivation pour l'async
            if isinstance(caption_style, str):
                cs_norm = caption_style.strip().lower()
                if cs_norm in ("0", "false", "off", "none", "no"):
                    caption_style = ""

            if caption_style and srt_text:
                ass_path = os.path.join(workdir if workdir else os.path.dirname(out_path), "captions.ass")
                ass_text = build_ass_from_srt(srt_text, preset=caption_style)
                with open(ass_path, "w", encoding="utf-8") as f:
                    f.write(ass_text)
                fps_val = int(fields.get("fps") or 30)
                cmd = (
                    f'ffmpeg -y -i "{out_path}" -vf "subtitles={ass_path}" '
                    f'-c:v libx264 -preset veryfast -crf 22 -r {fps_val} -pix_fmt yuv420p '
                    f'-c:a copy -movflags +faststart "{out_path[:-4]}_sub.mp4"'
                )
                subprocess.check_call(cmd, shell=True)
                out_path = out_path[:-4] + "_sub.mp4"
        except Exception as e:
            app.logger.exception(f"[{req_id}] captions burn failed: {e}")
        # --- END CAPTIONS ---

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
                if finish_webhook:
                    _post_finish_webhook(finish_webhook, True, output_name, compte, contenue)
            except Exception as e:
                app.logger.exception(f"[{req_id}] drive upload failed: {e}")
                result["drive_error"] = str(e)
                if finish_webhook:
                    _post_finish_webhook(finish_webhook, False, output_name, compte, contenue)

        _set_job(jid, **result)
        if callback_url:
            try:
                _requests.post(callback_url, json=result, timeout=10)
            except Exception:
                pass

    except Exception as e:
        app.logger.error(f"[{req_id}] worker failed: {e}\n{traceback.format_exc()}")
        _set_job(jid, status="failed", error=str(e), req_id=req_id)
    finally:
        try:
            if not KEEP_TMP and os.getenv("CLEAN_TMP") == "1" and workdir and os.path.isdir(workdir):
                shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

# ---------------- QUEUE API ----------------
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
            "drive_folder_id": request.form.get("drive_folder_id") or request.args.get("drive_folder_id"),
            "callback_url": request.form.get("callback_url"),
            "finish_webhook": request.form.get("finish_webhook") or request.args.get("finish_webhook"),
            "style": request.form.get("style"),
            "music_folder_id": request.form.get("music_folder_id"),
            "music_volume": request.form.get("music_volume"),
            "compte": request.form.get("compte") or request.args.get("compte"),
            # 🆕 on transporte tel-quel la narration "Contenue" pour l’async
            "Contenue": request.form.get("Contenue") or request.args.get("Contenue"),
            # 🆕 CAPTIONS
            "caption_style": request.form.get("caption_style"),
            "srt_text": request.form.get("srt_text"),
        }

        _set_job(jid, status="queued", job_id=jid, req_id=req_id, enqueued_at=int(time.time()))
        Thread(target=_worker_create_video, args=(jid, fields), daemon=True).start()
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

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
