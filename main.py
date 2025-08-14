import os, json, time, tempfile, logging, shutil
from uuid import uuid4
from typing import Any, Dict, List
from flask import Flask, request, jsonify, g

from video_generator import generate_video

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),
                    format="%(asctime)s %(levelname)s %(message)s")
app.logger.setLevel(logging.getLogger().level)

# -------- helpers preflight / plan --------
def _parse_int(val, default):
    try: return int(val)
    except: return default

def _normalize_plan(raw: Any) -> List[Dict[str, Any]]:
    if raw is None: raise ValueError("Missing 'plan'")
    if isinstance(raw, (bytes, bytearray)): raw = raw.decode("utf-8", "ignore")
    if isinstance(raw, str):
        raw = raw.strip()
        app.logger.info(f"[{g.req_id}] plan_len={len(raw)} head={raw[:300].replace(chr(10),' ')}")
        raw = json.loads(raw)
    if isinstance(raw, dict) and "plan" in raw:
        raw = raw["plan"]
    if not isinstance(raw, list):
        raise ValueError("plan must be a JSON array")
    if not raw:
        raise ValueError("plan is empty")
    return raw

def _kbps_from_env():
    v = os.getenv("VIDEO_BITRATE", "2500k").lower().strip()
    if v.endswith("k"): v = v[:-1]
    try: return int(v)
    except: return 2500

def _tmp_free_mb(path="/tmp"):
    st = os.statvfs(path)
    return int(st.f_bavail * st.f_frsize / (1024*1024))

def _estimate_video_mb(duration_s, kbps):
    try: return float(duration_s) * float(kbps) / 8000.0
    except: return 0.0
# -----------------------------------------

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

@app.get("/")
def health():
    return jsonify(ok=True, ts=int(time.time()))

@app.post("/create-video")
def create_video():
    workdir = None
    try:
        output_name = request.form["output_name"]
        width = _parse_int(request.form.get("width", 1080), 1080)
        height = _parse_int(request.form.get("height", 1920), 1920)
        fps = _parse_int(request.form.get("fps", 30), 30)
        plan_str = request.form["plan"]
        audio_file = request.files["audio_file"]

        app.logger.info(f"[{g.req_id}] fields ok name={output_name} {width}x{height}@{fps} audio={getattr(audio_file,'filename',None)}")

        plan = _normalize_plan(plan_str)

        # ---------- PRE-FLIGHT /tmp ----------
        total_dur = 0.0
        for seg in plan:
            try: total_dur += max(0.0, float(seg.get("duration", 0)))
            except: pass
        kbps = _kbps_from_env()
        free_mb = _tmp_free_mb()
        max_src_mb = int(os.getenv("MAX_SRC_MB", "60"))  # limite par média côté downloader
        need_mb = int(_estimate_video_mb(total_dur, kbps) + 2*max_src_mb + 50)  # marge
        app.logger.info(f"[{g.req_id}] preflight tmp: free={free_mb}MB need≈{need_mb}MB (dur={total_dur:.1f}s @ {kbps}kbps)")
        if free_mb < need_mb:
            return jsonify(
                error="insufficient_tmp_space",
                free_mb_mb=free_mb,
                need_mb_mb=need_mb,
                hint="reduce total duration / VIDEO_BITRATE or run 720x1280@24"
            ), 413
        # ------------------------------------

        workdir = tempfile.mkdtemp(prefix="fusionbot_")
        audio_path = os.path.join(workdir, "voice.mp3")
        audio_file.save(audio_path)

        out_path = generate_video(
            plan=plan,
            audio_path=audio_path,
            output_name=output_name,
            temp_dir=workdir,
            width=width, height=height, fps=fps,
            logger=app.logger, req_id=g.req_id
        )

        return jsonify(status="success", output_path=out_path, width=width, height=height, fps=fps, items=len(plan))
    except Exception as e:
        app.logger.exception(f"[{getattr(g,'req_id','?')}] create-video failed")
        return jsonify(error="internal error", detail=str(e)), 500
    finally:
        try:
            if os.getenv("CLEAN_TMP") == "1" and workdir and os.path.isdir(workdir):
                shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
