from flask import Flask, request, jsonify
import os
import tempfile
import uuid
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

        # Output options (defaults for Reels/Shorts)
        width = int(data.get("width", 1080))
        height = int(data.get("height", 1920))
        fps = int(data.get("fps", 30))
        audio_url = data.get("audio_url")  # direct URL to mp3/wav/ogg

        # Unique working directory per request
        workdir = os.path.join(tempfile.gettempdir(), f"fusionbot_{uuid.uuid4().hex}")
        os.makedirs(workdir, exist_ok=True)

        output_path = os.path.join(workdir, data.get("output_name", "output.mp4"))

        result_path = generate_video_from_plan(
            plan=data["plan"],
            output_path=output_path,
            size=(width, height),
            fps=fps,
            audio_url=audio_url,
            workdir=workdir
        )

        return jsonify({"status": "success", "path": result_path}), 200

    except Exception as e:
        # Log full error to Render logs
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # For local testing only. On Render, use gunicorn via Procfile.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
