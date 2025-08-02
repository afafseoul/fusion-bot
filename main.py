
from flask import Flask, request, jsonify
import os
import shutil
import uuid
from video_generator import generate_video

app = Flask(__name__)

@app.route("/")
def index():
    return "🟢 Render Flask backend ready."

@app.route("/create-video", methods=["POST"])
def create_video():
    try:
        data = request.get_json()

        # Vérification des champs requis
        required_keys = ["audio_filename", "drive_folder_id", "subtitles", "output_name"]
        for key in required_keys:
            if key not in data:
                return jsonify({"error": f"Missing key: {key}"}), 400

        # Création d'un dossier temporaire unique
        session_id = str(uuid.uuid4())
        temp_dir = os.path.join("temp", session_id)
        os.makedirs(temp_dir, exist_ok=True)

        print(f"[INFO] 🎬 Nouvelle requête reçue — session {session_id}")
        print(f"[INFO] 🔗 Audio : {data['audio_filename']} depuis dossier {data['drive_folder_id']}")
        print(f"[INFO] 🎞️ Nombre de GIFs : {len(data['subtitles'])}")
        print(f"[INFO] 💾 Nom de sortie : {data['output_name']}")

        # Appel de la fonction de génération (à définir dans video_generator.py)
        output_path = generate_video(
            audio_filename=data["audio_filename"],
            drive_folder_id=data["drive_folder_id"],
            subtitles=data["subtitles"],
            output_name=data["output_name"],
            temp_dir=temp_dir
        )

        return jsonify({
            "status": "success",
            "output_path": output_path
        }), 200

    except Exception as e:
        print(f"[ERROR] 💥 Erreur lors du traitement : {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
