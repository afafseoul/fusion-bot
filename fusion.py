from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
import traceback

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'
SOCIAL_POSTING_FOLDER_ID = '1cXn22CJ8YIMftyARZClmJiMC4pSybOHE'  # ID du dossier SOCIAL POSTING

@app.route('/', methods=['GET'])
def index():
    return "✅ Fusion Bot is running!"

@app.route('/start', methods=['POST'])
def start_fusion():
    try:
        data = request.get_json(force=True)
        print("🟡 Reçu:", data)

        if not data or 'client' not in data or 'video_name' not in data:
            return jsonify({"status": "error", "message": "Missing client or video_name"}), 400

        client = data['client']
        video_name = data['video_name']

        print(f"🔎 Recherche du dossier pour le client '{client}'...")

        # Connexion à Google Drive
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        drive_service = build('drive', 'v3', credentials=credentials)

        # Cherche le dossier client dans SOCIAL POSTING
        query = f"'{SOCIAL_POSTING_FOLDER_ID}' in parents and name='{client}' and mimeType='application/vnd.google-apps.folder' and trashed = false"
        response = drive_service.files().list(q=query, spaces='drive').execute()
        folders = response.get('files', [])

        if not folders:
            print("❌ Dossier client introuvable")
            return jsonify({"status": "error", "message": f"Client folder '{client}' not found."}), 404

        client_folder_id = folders[0]['id']
        print(f"✅ Dossier client trouvé : {client_folder_id}")

        return jsonify({"status": "success", "message": "Connected", "folder_id": client_folder_id}), 200

    except Exception as e:
        print("❌ Exception capturée :", traceback.format_exc())
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500
