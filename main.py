from flask import Flask, request, jsonify
import os, tempfile, uuid, base64, traceback
from video_generator import generate_video_from_plan

app = Flask(__name__)

@app.get("/")
def health():
    return "🟢 fusion-bot ready"

@app.post("/create-video")
def create_video():
    try:
        data = request.get_json(force=True, silent=False)
        if not data or "plan" not in data:
            return jsonify({"error": "Missing 'plan' array in JSON"}), 400

        width  = int(data.get("width", 1080))
        height = int(data.get("height", 1920))
        fps    = int(data.get("fps", 30))

        # audio : soit base64 direct, soit URL (optionnel)
        audio_base64 = data.get("audio_base64")
        audio_url    = data.get("audio_url")

        workdir = os.path.join(tempfile.gettempdir(), f"fusionbot_{uuid.uuid4().hex}")
        os.makedirs(workdir, exist_ok=True)

        audio_path = None
        if audio_base64:
            audio_path = os.path.join(workdir, "audio.mp3")
            with open(audio_path, "wb") as f:
                f.write(base64.b64decode(audio_base64))

        output_path = os.path.join(workdir, data.get("output_name", "output.mp4"))

        result_path = generate_video_from_plan(
            plan=data["plan"],
            output_path=output_path,
            size=(width, height),
            fps=fps,
            audio_url=(None if audio_path else audio_url),
            audio_path=audio_path,
            workdir=workdir
        )

        return jsonify({"status": "success", "path": result_path}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
