import os, json, time, tempfile, logging, shutil, subprocess, traceback
from uuid import uuid4
from typing import Any, Dict, List, Optional
from flask import Flask, request, jsonify, g

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
# --------------------------------------------------------------

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
    app.logger.info(f"[{g.req_id}] --> {request.method} {request.path} ct={request.content_type} len={request.content_length}")

@app.after_request
def _end(resp):
    dt = (time.time()-g.t0)*1000
    resp.headers["X-Request-ID"] = g.req_id
    app.logger.info(f"[{g.req_id}] <-- {resp.status_code} {dt:.1f}ms")
    return resp

def _parse_int(v, d):
    try: return int(v)
    except: return d

def _normalize_plan(raw: Any) -> List[Dict[str, Any]]:
    """
    Tolérante aux petits JSON cassés (ex: guillemet non terminé).
    Accepte:
      {"plan":[{...}, ...]}  ou directement  [{...}, ...]
    """
    if raw is None:
        raise ValueError("Missing 'plan'")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    if isinstance(raw, str):
        raw = raw.strip()
        app.logger.info(f"[{g.req_id}] plan_len={len(raw)} head={raw[:400].replace(chr(10),' ')}")
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
        audio_dur = _ffprobe_duration(audio_path)
        app.logger.info(f"[{g.req_id}] audio path={audio_path} size={audio_size}B dur={audio_dur:.3f}s")

        # dump inputs pour post-mortem
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
        )

        out_size = os.path.getsize(out_path)
        out_dur  = _ffprobe_duration(out_path)
        app.logger.info(f"[{g.req_id}] OUTPUT path={out_path} size={out_size}B dur={out_dur:.3f}s")

        # bundle debug
        with open(os.path.join(debug_dir, "generator_debug.json"), "w", encoding="utf-8") as f:
            json.dump(gen_debug, f, ensure_ascii=False, indent=2)

        resp_json = {
            "status": "success",
            "output_path": out_path,
            "width": width, "height": height, "fps": fps,
            "items": len(plan),
            "out_size": out_size, "out_duration": out_dur,
            "workdir": workdir
        }

        if drive_folder_id:
            try:
                gd = _gdrive_upload(out_path, output_name, drive_folder_id, app.logger, g.req_id)
                resp_json.update({
                    "drive_file_id": gd.get("id"),
                    "drive_webViewLink": gd.get("webViewLink"),
                })
            except Exception as e:
                app.logger.exception(f"[{g.req_id}] drive upload failed: {e}")
                resp_json["drive_error"] = str(e)

        return jsonify(resp_json)

    except Exception as e:
        app.logger.error(f"[{getattr(g,'req_id','?')}] create-video failed: {e}\n{traceback.format_exc()}")
        return jsonify(error="internal error", detail=str(e)), 500
    finally:
        try:
            if not KEEP_TMP and os.getenv("CLEAN_TMP") == "1" and workdir and os.path.isdir(workdir):
                shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
