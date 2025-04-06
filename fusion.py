-from flask import Flask, request, jsonify
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
        # Extraction JSON du corps de la requête
        data = request.get_json(force=True)

        # Vérifie que les champs requis sont présents
        client = data['client']
        video_name = data['video_name']

        # Connexion à Google Drive
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        drive_service = build('drive', 'v3', credentials=credentials)

        # Test basique : Lister les fichiers dans SOCIAL POSTING/client
        parent_folder = "SOCIAL POSTING"
        response = drive_service.files().list(
            q=f"name='{client}' and mimeType='application/vnd.google-apps.folder'",
            spaces='drive'
        ).execute()

        folders = response.get('files', [])
        if not folders:
            return jsonify({"status": "error", "message": "Client folder not found."}), 404

        client_folder_id = folders[0]['id']

        return jsonify({"status": "success", "message": f"Connected. Folder ID: {client_folder_id}"}), 200

    except KeyError as e:
        return jsonify({"status": "error", "message": f"Missing parameter: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500
