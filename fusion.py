from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'

@app.route('/', methods=['GET'])
def index():
    return "✅ Fusion Bot is running!"

@app.route('/start', methods=['POST'])
def start_fusion():
    try:
        data = request.get_json(force=True)

        # Récupération des champs
        client = data.get("client")
        video_name = data.get("video_name")
        if not client or not video_name:
            return jsonify({"status": "error", "message": "Missing 'client' or 'video_name'"}), 400

        # Authentification Google
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        drive_service = build('drive', 'v3', credentials=credentials)

        # Étape 1 : trouver le dossier "SOCIAL POSTING"
        social_response = drive_service.files().list(
            q="name='SOCIAL POSTING' and mimeType='application/vnd.google-apps.folder' and trashed = false",
            spaces='drive'
        ).execute()
        if not social_response['files']:
            return jsonify({"status": "error", "message": "Folder 'SOCIAL POSTING' not found"}), 404

        social_folder_id = social_response['files'][0]['id']

        # Étape 2 : trouver le dossier du client dans SOCIAL POSTING
        client_response = drive_service.files().list(
            q=f"name='{client}' and mimeType='application/vnd.google-apps.folder' and '{social_folder_id}' in parents and trashed = false",
            spaces='drive'
        ).execute()
        if not client_response['files']:
            return jsonify({"status": "error", "message": f"Client folder '{client}' not found in SOCIAL POSTING"}), 404

        client_folder_id = client_response['files'][0]['id']

        # Étape 3 : vérifier que la vidéo existe
        video_response = drive_service.files().list(
            q=f"name='{video_name}' and '{client_folder_id}' in parents and trashed = false",
            spaces='drive'
        ).execute()
        if not video_response['files']:
            return jsonify({"status": "error", "message": f"Video '{video_name}' not found in {client} folder"}), 404

        return jsonify({
            "status": "success",
            "message": f"Video '{video_name}' found in folder '{client}' ✅"
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
