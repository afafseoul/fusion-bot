from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os

app = Flask(__name__)

# Autorisations Drive
SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'

@app.route('/', methods=['GET'])
def index():
    return "✅ Fusion Bot is running!"

@app.route('/start', methods=['POST'])
def start_fusion():
    try:
        # Récupère les données JSON
        data = request.get_json(force=True)
        client = data['client']
        video_name = data['video_name']

        # Connexion au service Google Drive
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        drive_service = build('drive', 'v3', credentials=credentials)

        # 1. Trouver le dossier "SOCIAL POSTING"
        response = drive_service.files().list(
            q="name='SOCIAL POSTING' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            spaces='drive',
            fields="files(id, name)"
        ).execute()

        social_folders = response.get('files', [])
        if not social_folders:
            return jsonify({"status": "error", "message": "Dossier SOCIAL POSTING introuvable"}), 404

        social_posting_id = social_folders[0]['id']

        # 2. Trouver le dossier client à l’intérieur
        response = drive_service.files().list(
            q=f"name='{client}' and mimeType='application/vnd.google-apps.folder' and '{social_posting_id}' in parents and trashed=false",
            spaces='drive',
            fields="files(id, name)"
        ).execute()

        client_folders = response.get('files', [])
        if not client_folders:
            return jsonify({"status": "error", "message": f"Dossier client '{client}' introuvable"}), 404

        client_folder_id = client_folders[0]['id']

        return jsonify({
            "status": "success",
            "client": client,
            "video_name": video_name,
            "client_folder_id": client_folder_id
        }), 200

    except KeyError as e:
        return jsonify({"status": "error", "message": f"Paramètre manquant : {str(e)}"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur serveur : {str(e)}"}), 500
