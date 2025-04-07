from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'
ROOT_FOLDER_ID = "1cXn22CJ8YIMftyARZClmJiMC4pSybOHE"  # ID du dossier "SOCIAL POSTING"

@app.route('/', methods=['GET'])
def index():
    return "✅ Fusion Bot is running!"

@app.route('/start', methods=['POST'])
def start_fusion():
    try:
        # Récupération des données
        data = request.get_json(force=True)
        client = data['client']
        video_name = data['video_name']

        # Connexion Drive
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        drive_service = build('drive', 'v3', credentials=credentials)

        # 🔍 Cherche le dossier client dans "SOCIAL POSTING" via son ID parent
        response = drive_service.files().list(
            q=f"name='{client}' and '{ROOT_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id, name)"
        ).execute()

        folders = response.get('files', [])
        if not folders:
            return jsonify({"status": "error", "message": f"Dossier client '{client}' introuvable."}), 404

        client_folder_id = folders[0]['id']
        return jsonify({"status": "success", "message": f"Dossier trouvé : {client_folder_id}"}), 200

    except KeyError as e:
        return jsonify({"status": "error", "message": f"Paramètre manquant : {str(e)}"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur serveur : {str(e)}"}), 500
