from flask import Flask, request, jsonify
import os, uuid, base64, json, tempfile, traceback
from video_generator import generate_video

app = Flask(__name__)

@app.get("/")
def index():
    return "🟢 Render Flask backend ready."

@app.post("/create-video")
def create_video():
    # --- 1) LOG DU RAW BODY POUR DEBUG ---
    raw = request.get_data(cache=False, as_text=True)
    print("RAW BODY (first 1000 chars) =>", raw[:1000])

    try:
        # --- 2) PARSE JSON ROBUSTE ---
        try:
            data = json.loads(raw)
        except Exception as e:
            print("❌ Invalid JSON body:", e)
            return jsonify({
                "error": "invalid_json",
                "detail": str(e),
                "tip": "Vérifie que 'audio_base64' est bien encodé en base64 ET que 'plan' est un objet/array JSON, pas une string."
            }), 400

        # --- 3) PARAMS VIDÉO ---
        output_name = data.get("output_name", f"{uuid.uuid4().hex}.mp4")
        width  = int(data.get("width", 1080))
        height = int(data.get("height", 1920))
        fps    = int(data.get("fps", 30))

        # --- 4) PLAN (ACCEPTE OBJET OU TEXTE JSON) ---
        plan = data.get("plan")
        if plan is None:
            return jsonify({"error": "Missing 'plan'"}), 400
        if isinstance(plan, str):
            # au cas où Make enverrait encore une string JSON
            try:
                plan = json.loads(plan)
            except Exception as e:
                return jsonify({"error": f"Invalid JSON in 'plan': {e}"}), 400

        # --- 5) AUDIO (clé unique 'audio_base64') ---
        audio_b64 = data.get("audio_base64")
        if not audio_b64 or not isinstance(audio_b64, str):
            return jsonify({"error": "Missing or invalid 'audio_base64'"}), 400

        try:
            decoded = base64.b64decode(audio_b64, validate=True)
        except Exception as e:
            return jsonify({"error": f"audio_base64 is not valid base64: {e}"}), 400

        # --- 6) SAUVE L'AUDIO EN FICHIER TEMP ---
        workdir = tempfile.mkdtemp(prefix="fusionbot_")
        audio_path = os.path.join(workdir, "audio.mp3")
        with open(audio_path, "wb") as f:
            f.write(decoded)

        # --- 7) GÉNÈRE LA VIDÉO ---
        output_path = generate_video(
            plan=plan,
            audio_path=audio_path,
            output_name=output_name,
            temp_dir=workdir,
            width=width,
            height=height,
            fps=fps,
        )

        # --- 8) RÉPONSE OK ---
        return jsonify({
            "status": "success",
            "output_path": output_path,
            "width": width,
            "height": height,
            "fps": fps,
            "items": len(plan) if isinstance(plan, list) else None
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # en local uniquement
    app.run(debug=True)
