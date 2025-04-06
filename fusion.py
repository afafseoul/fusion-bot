from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
import random
import subprocess

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'

@app.route('/', methods=['GET'])
def index():
    return "✅ Fusion Bot is running!"

@app.route('/start', methods=['POST'])
def start_fusion():
    try:
        # Charger les données envoyées depuis Make
        data = request.get_json()
        client = data.get("client")
        video_name = data.get("video_name")

        if not client or not video_name:
            return jsonify({"error": "Missing client or video_name"}), 400

        # Authentification Drive
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        drive_service = build('drive', 'v3', credentials=credentials)

        # ID du dossier Music (à adapter si besoin)
        music_folder_id = "FOLDER_ID_MUSIC"

        # Chercher les musiques disponibles
        results = drive_service.files().list(
            q=f"'{music_folder_id}' in parents and mimeType='audio/mpeg'",
            fields="files(id, name)"
        ).execute()
        music_files = results.get('files', [])

        if not music_files:
            return jsonify({"error": "No music files found"}), 404

        # Choisir une musique au hasard
        music = random.choice(music_files)
        music_name = music["name"]

        # Extraire le point de départ depuis le nom (ex: nom@8.mp3)
        if "@" in music_name:
            music_start = int(music_name.split("@")[1].split(".")[0])
        else:
            music_start = 0

        # Commande FFmpeg
        input_video_path = f"/app/Post-Video-AddMusic/{client}/{video_name}"
        input_music_path = f"/app/Musique/{client}/{music_name}"
        output_path = f"/app/ReadyToPost/{client}/{video_name}"

        ffmpeg_cmd = [
            "ffmpeg",
            "-i", input_video_path,
            "-ss", str(music_start),
            "-i", input_music_path,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-shortest",
            output_path
        ]

        subprocess.run(ffmpeg_cmd, check=True)

        return jsonify({
            "message": "✅ Video successfully processed!",
            "output_file": output_path
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
