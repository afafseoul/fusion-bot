import os, json, time, tempfile, logging, shutil
from uuid import uuid4
from typing import Any, Dict, List
from flask import Flask, request, jsonify, g

from video_generator import generate_video

app = Flask(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s"
)
app.logger.setLevel(logging.getLogger().level)

@app.before_request
def _start():
    g.req_id = request.headers.get("X-Request-ID", str(uuid4()))
    g.t0 = time.time()
    app.logger.info(f"[{g.req_id}] --> {request.method} {request.path} ct={request.content_type} len={request.content_length}")

@app.after_request
def _end(resp):
    dt = (time.time() - g.t0) * 1000
    resp.headers["X-Request-ID"] = g.req_id
    app.logger.info(f"[{g.req_id}] <-- {resp.status_code} {dt:.1f}ms")
    return resp

def _parse_int(val, default):
    try:
        return int(val)
    except:
        return default

def _normalize_plan(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        raise ValueError("Missing 'plan'")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
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

@app.get("/")
def health():
    return jsonify(ok=True, ts=int(time.time()))

@app.post("/create-video")
def create_video():
    workdir = None
    try:
        output_name = request.form["output_name"]
        width = _parse_int(request.form.get("width", 1080), 1080)     # ignorés en FAST_COPY
        height = _parse_int(request.form.get("height", 1920), 1920)   # ignorés en FAST_COPY
        fps = _parse_int(request.form.get("fps", 30), 30)             # ignorés en FAST_COPY
        plan_str = request.form["plan"]
        audio_file = request.files["audio_file"]
        global_srt = request.form.get("global_srt")

        app.logger.info(f"[{g.req_id}] fields ok name={output_name} {width}x{height}@{fps} audio={getattr(audio_file,'filename',None)}")

        plan = _normalize_plan(plan_str)

        # log prévision simple
        try:
            total_dur = sum(float(max(0.0, (seg.get("duration") or 0))) for seg in plan)
        except Exception:
            total_dur = 0.0
        free_tmp = shutil.disk_usage("/tmp").free // (1024 * 1024)
        app.logger.info(f"[{g.req_id}] preflight tmp: free={free_tmp}MB need≈~{int(total_dur*0.35)}MB (approx copy concat)")

        workdir = tempfile.mkdtemp(prefix="fusionbot_")
        audio_path = os.path.join(workdir, "voice.mp3")
        audio_file.save(audio_path)

        out_path = generate_video(
            plan=plan,
            audio_path=audio_path,
            output_name=output_name,
            temp_dir=workdir,
            width=width, height=height, fps=fps,
            logger=app.logger, req_id=g.req_id,
            global_srt=global_srt,
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
