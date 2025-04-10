from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import os
import random
import ffmpeg

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
DRIVE_FOLDER_ID = '1cXn22CJ8YlMftyARZcImJiMC4pSybOHE'

@app.route('/')
def index():
    return "✅ Fusion Bot actif"

@app.route('/start', methods=['POST'])
def start():
    try:
        data = request.get_json()
        client = data.get("client")
        video_name = data.get("video_name")
        print("\n📥 Requête reçue...")
        print(f"✅ Données JSON reçues : {data}")

        creds = service_account.Credentials.from_service_account_file("/etc/secrets/credentials.json", scopes=SCOPES)
        drive_service = build("drive", "v3", credentials=creds)
        print("✅ Connexion à Google Drive réussie")

        # Recherche du dossier client
        response = drive_service.files().list(
            q=f"name='{client}' and mimeType='application/vnd.google-apps.folder' and '{DRIVE_FOLDER_ID}' in parents",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        folders = response.get('files', [])
        if not folders:
            return jsonify({"error": f"Dossier client '{client}' introuvable"}), 404

        client_folder_id = folders[0]['id']
        print(f"📁 Dossier du client trouvé : {client_folder_id}")

        # Recherche de la vidéo
        video_response = drive_service.files().list(
            q=f"name='{video_name}' and '{client_folder_id}' in parents",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        video_files = video_response.get('files', [])
        if not video_files:
            return jsonify({"error": f"Vidéo '{video_name}' introuvable"}), 404

        video_file = video_files[0]
        video_id = video_file['id']

        # Téléchargement de la vidéo
        video_path = f"temp_{video_name}"
        request_video = drive_service.files().get_media(fileId=video_id)
        with open(video_path, "wb") as f:
            downloader = drive_service._http.request(request_video.uri)
            f.write(downloader[1])

        print(f"📥 Vidéo téléchargée : {video_path}")

        # Choix d'une musique aléatoire
        music_folder_response = drive_service.files().list(
            q=f"name='Music' and '{client_folder_id}' in parents and mimeType='application/vnd.google-apps.folder'",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        music_folders = music_folder_response.get('files', [])
        if not music_folders:
            return jsonify({"error": "Dossier 'Music' introuvable"}), 404

        music_folder_id = music_folders[0]['id']
        music_files_response = drive_service.files().list(
            q=f"'{music_folder_id}' in parents and mimeType contains 'audio'",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        music_files = music_files_response.get('files', [])
        if not music_files:
            return jsonify({"error": "Aucune musique disponible"}), 404

        music_file = random.choice(music_files)
        music_name = music_file['name']
        music_id = music_file['id']
        music_path = f"temp_{music_name}"
        request_music = drive_service.files().get_media(fileId=music_id)
        with open(music_path, "wb") as f:
            downloader = drive_service._http.request(request_music.uri)
            f.write(downloader[1])

        print(f"🎵 Musique sélectionnée : {music_name}")

        # Fusion avec FFmpeg
        output_path = f"final_{video_name}"
        ffmpeg.input(video_path).output(music_path, output_path, shortest=None, vcodec='copy', acodec='aac').run(overwrite_output=True)

        print(f"✅ Fusion terminée : {output_path}")

        # Upload dans ReadyToPost
        ready_folder_response = drive_service.files().list(
            q=f"name='ReadyToPost' and '{client_folder_id}' in parents and mimeType='application/vnd.google-apps.folder'",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        ready_folders = ready_folder_response.get('files', [])
        if not ready_folders:
            return jsonify({"error": "Dossier 'ReadyToPost' introuvable"}), 404

        ready_folder_id = ready_folders[0]['id']

        file_metadata = {"name": output_path, "parents": [ready_folder_id]}
        media = MediaFileUpload(output_path, mimetype='video/mp4')
        drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

        print(f"🚀 Vidéo finale uploadée : {output_path}")

        # Nettoyage
        os.remove(video_path)
        os.remove(music_path)
        os.remove(output_path)

        return jsonify({"status": "ok"})

    except Exception as e:
        print(f"🚨 ERREUR : {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
