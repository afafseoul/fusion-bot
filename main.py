from flask import Flask, request, jsonify
import os, uuid, base64, json, tempfile, traceback
from video_generator import generate_video

app = Flask(__name__)

def _normalize_plan(raw):
    """Retourne une liste d'objets plan à partir de str/dict/list."""
    if raw is None:
        raise ValueError("Missing 'plan'")

    # string JSON -> objet
    if isinstance(raw, str):
        raw = raw.strip()
        raw = json.loads(raw)

    # si on a reçu l'objet complet {"plan":[...], ...}
    if isinstance(raw, dict) and "plan" in raw:
        raw = raw["plan"]

    # liste: si éléments sont des strings JSON, les parser
    if isinstance(raw, list):
        if raw and all(isinstance(x, str) for x in raw):
            return [json.loads(x) for x in raw]
        return raw

    # déjà la bonne forme
    return raw

@app.get("/")
def index():
    return "🟢 Render Flask backend ready."

@app.post("/create-video")
def create_video():
    try:
        ctype = (request.content_type or "").lower()
        print("Content-Type:", ctype)

        # ---------- Cas 1 : multipart/form-data (Make envoie un fichier) ----------
        if "multipart/form-data" in ctype:
            form = request.form
            files = request.files

            output_name = form.get("output_name") or f"{uuid.uuid4().hex}.mp4"
            width       = int(form.get("width")  or 1080)
            height      = int(form.get("height") or 1920)
            fps         = int(form.get("fps")    or 30)
            # optionnel, dispo si tu l'utilises plus tard
            drive_folder_id = form.get("drive_folder_id")

            raw_plan = form.get("plan")
            if not raw_plan:
                return jsonify({"error": "Missing 'plan'"}), 400
            plan = _normalize_plan(raw_plan)

            f = files.get("audio_file")
            if not f:
                return jsonify({"error": "Missing file 'audio_file'"}), 400

            workdir = tempfile.mkdtemp(prefix="fusionbot_")
            audio_path = os.path.join(workdir, "audio.mp3")
            f.save(audio_path)

        # ---------- Cas 2 : JSON (audio_base64) ----------
        else:
            raw = request.get_data(cache=False, as_text=True)
            print("RAW BODY (first 1000 chars) =>", raw[:1000])
            try:
                data = json.loads(raw or "{}")
            except Exception as e:
                return jsonify({
                    "error": "invalid_json",
                    "detail": str(e),
                    "tip": "En multipart, retire le header Content-Type personnalisé; "
                           "en JSON, fournis 'audio_base64' et 'plan' valide."
                }), 400

            output_name = data.get("output_name") or f"{uuid.uuid4().hex}.mp4"
            width       = int(data.get("width")  or 1080)
            height      = int(data.get("height") or 1920)
            fps         = int(data.get("fps")    or 30)

            plan = _normalize_plan(data.get("plan"))

            audio_b64 = data.get("audio_base64") or data.get("audio_bas e64")
            if not audio_b64 or not isinstance(audio_b64, str):
                return jsonify({"error": "Missing or invalid 'audio_base64'"}), 400

            workdir = tempfile.mkdtemp(prefix="fusionbot_")
            audio_path = os.path.join(workdir, "audio.mp3")
            try:
                with open(audio_path, "wb") as f:
                    f.write(base64.b64decode(audio_b64))
            except Exception as e:
                return jsonify({"error": f"audio_base64 decode failed: {e}"}), 400

        # ---------- Génération ----------
        out_path = generate_video(
            plan=plan,
            audio_path=audio_path,
            output_name=output_name,
            temp_dir=workdir,
            width=width, height=height, fps=fps,
        )

        return jsonify({
            "status": "success",
            "output_path": out_path,
            "width": width, "height": height, "fps": fps,
            "items": len(plan) if isinstance(plan, list) else None
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "content_type": request.content_type}), 500

if __name__ == "__main__":
    app.run(debug=True)
