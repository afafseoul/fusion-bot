from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
import os
import io
import random
import subprocess

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'

# Initialisation
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=credentials)

# Fonctions utilitaires
def get_file_id(name, parent_folder_id):
    query = f"name = '{name}' and '{parent_folder_id}' in parents and trashed = false"
    results = drive_service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])
    return files[0]['id'] if files else None

def get_folder_id(name, parent=None):
    query = f"mimeType = 'application/vnd.google-apps.folder' and name = '{name}' and trashed = false"
    if parent:
        query += f" and '{parent}' in parents"
    results = drive_service.files().list(q=query, fields="files(id)").execute()
    folders = results.get('files', [])
    return folders[0]['id'] if folders else None

def download_file(file_id, file_name):
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.FileIO(file_name, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()

def upload_to_ready(client, file_name):
    ready_id = get_folder_id("ReadyToPost", get_folder_id(client, get_folder_id("SOCIAL POSTING")))
    file_metadata = {'name': file_name, 'parents': [ready_id]}
    media = MediaFileUpload(file_name, resumable=True)
    drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

def pick_music(client):
    music_folder_id = get_folder_id("Music", get_folder_id(client, get_folder_id("SOCIAL POSTING")))
    musics = drive_service.files().list(q=f"'{music_folder_id}' in parents and trashed = false",
                                        fields="files(id, name)").execute().get('files', [])
    return random.choice(musics) if musics else None

def extract_start_time(music_name):
    try:
        return int(music_name.split("@")[1].split(".")[0])
    except:
        return 0

def process_video(video_name, music_name, start_time):
    output_name = "final_" + video_name
    command = [
        "ffmpeg", "-i", video_name, "-ss", str(start_time), "-i", music_name,
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-shortest", output_name
    ]
    subprocess.run(command, check=True)
    return output_name

# Routes Flask
@app.route('/', methods=['GET'])
def index():
    return "✅ Fusion Bot is running!"

@app.route('/start', methods=['POST'])
def start():
    try:
        data = request.get_json()
        client = data.get('client')
        video_name = data.get('video_name')

        if not client or not video_name:
            return "❌ client et video_name sont requis", 400

        client_folder = get_folder_id(client, get_folder_id("SOCIAL POSTING"))
        video_folder = get_folder_id("Post-Video-AddMusic", client_folder)
        video_id = get_file_id(video_name, video_folder)

        if not video_id:
            return "❌ Vidéo introuvable", 404

        download_file(video_id, video_name)

        music = pick_music(client)
        if not music:
            return "❌ Aucune musique trouvée", 404

        music_name = music["name"]
        music_id = music["id"]
        download_file(music_id, music_name)

        start_time = extract_start_time(music_name)
        final_video = process_video(video_name, music_name, start_time)

        upload_to_ready(client, final_video)

        return f"✅ Fusion réussie : {final_video}"
    except Exception as e:
        return f"❌ Erreur : {str(e)}", 500
