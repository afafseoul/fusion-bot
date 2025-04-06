from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
import os
import random
import subprocess
import io

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'
credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=credentials)

# Trouver l'ID du dossier du client
def get_folder_id(parent_id, folder_name):
    query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and name='{folder_name}'"
    response = drive_service.files().list(q=query, fields="files(id)").execute()
    folders = response.get('files', [])
    return folders[0]['id'] if folders else None

# Télécharger fichier depuis Drive
def download_file(file_id, filepath):
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.FileIO(filepath, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

@app.route('/', methods=['GET'])
def index():
    return "✅ Fusion Bot is running!"

@app.route('/start', methods=['POST'])
def start_fusion():
    try:
        data = request.get_json()
        client = data["client"]
        video_name = data["video_name"]

        MAIN_FOLDER_ID = '1cXn22CJ8YIMftyARZclmJiMC4pSybOHE'

        client_folder_id = get_folder_id(MAIN_FOLDER_ID, client)
        videos_folder_id = get_folder_id(client_folder_id, 'Post-Video-AddMusic')
        musique_folder_id = get_folder_id(client_folder_id, 'Musique')
        ready_folder_id = get_folder_id(client_folder_id, 'ReadyToPost')

        # Télécharger vidéo depuis Drive
        query_video = f"'{videos_folder_id}' in parents and name='{video_name}'"
        video_result = drive_service.files().list(q=query_video, fields="files(id)").execute()
        if not video_result['files']:
            return jsonify({"error": "Vidéo introuvable"}), 404
        video_id = video_result['files'][0]['id']
        local_video_path = f"/tmp/{video_name}"
        download_file(video_id, local_video_path)

        # Choisir et télécharger une musique aléatoire
        musiques_result = drive_service.files().list(q=f"'{musique_folder_id}' in parents", fields="files(id, name)").execute()
        musique = random.choice(musiques_result['files'])
        local_music_path = f"/tmp/{musique['name']}"
        download_file(musique['id'], local_music_path)

        # Extraction du timing depuis nom du fichier musique
        music_start = 0
        if "@" in musique['name']:
            music_start = int(musique['name'].split("@")[1].split(".")[0])

        # Fusion via FFmpeg
        output_filename = f"fused_{video_name}"
        output_filepath = f"/tmp/{output_filename}"
        subprocess.run([
            "ffmpeg", "-y",
            "-i", local_video_path,
            "-ss", str(music_start), "-i", local_music_path,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-shortest",
            output_filepath
        ], check=True)

        # Upload résultat sur Drive dans ReadyToPost
        media = MediaFileUpload(output_filepath, mimetype='video/mp4')
        file_metadata = {'name': output_filename, 'parents': [ready_folder_id]}
        drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

        return jsonify({"message": "✅ Fusion réussie et vidéo uploadée"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
