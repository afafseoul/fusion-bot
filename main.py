from flask import Flask, request, jsonify
import os, uuid, base64, json, tempfile, traceback
from video_generator import generate_video

app = Flask(__name__)

@app.get("/")
def index():
    return "🟢 Render Flask backend ready."

@app.post("/create-video")
def create_video():
    try:
        data = request.get_json(force=True, silent=False) or {}

        # ---------- paramètres vidéo ----------
        output_name = data.get("output_name", f"{uuid.uuid4().hex}.mp4")
        width  = int(data.get("width", 1080))
        height = int(data.get("height", 1920))
        fps    = int(data.get("fps", 30))

        # ---------- plan (string -> objet) ----------
        raw_plan = data.get("plan")
        if raw_plan is None:
            return jsonify({"error": "Missing 'plan'"}), 400
        if isinstance(raw_plan, str):
            try:
                plan = json.loads(raw_plan)
            except Exception as e:
                return jsonify({"error": f"Invalid JSON in 'plan': {e}"}), 400
        else:
            plan = raw_plan

        # ---------- audio (clé tolérante + b64/binaire) ----------
        audio_b64 = (
            data.get("audio_base64")
            or data.get("audio_bas e64")  # tolère l’espace dans la clé
        )
        if audio_b64 is None:
            return jsonify({"error": "Missing 'audio_base64'"}), 400

        # Si on a reçu du binaire par erreur, on essaie d’encoder nous-mêmes
        try:
            # test: si ce n’est PAS du base64 valide, ça lèvera une exception
            base64.b64decode(audio_b64, validate=True)
            decoded = base64.b64decode(audio_b64)
        except Exception:
            # probable binaire brut -> on l’encode
            if isinstance(audio_b64, str):
                decoded = audio_b64.encode("latin1", "ignore")
            else:
                decoded = bytes(audio_b64)
        # ---------- fichier audio local ----------
        workdir = tempfile.mkdtemp(prefix="fusionbot_")
        audio_path = os.path.join(workdir, "audio.mp3")
        with open(audio_path, "wb") as f:
            f.write(decoded)

        # ---------- génération vidéo ----------
        output_path = generate_video(
            plan=plan,
            audio_path=audio_path,
            output_name=output_name,
            temp_dir=workdir,
            width=width,
            height=height,
            fps=fps,
        )

        return jsonify({"status": "success", "output_path": output_path}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
