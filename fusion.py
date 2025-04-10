from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import random
import os

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'

@app.route('/', methods=['GET'])
def index():
    return "✅ Fusion Bot is running!"

@app.route('/start', methods=['POST'])
def start():
    try:
        data = request.get_json(force=True)
        client = data['client']
        video_name = data['video_name']
        print("📥 Requête reçue...")
        print(f"✅ Données JSON reçues : {data}")
        print(f"🔍 Recherche du dossier du client '{client}' dans SOCIAL POSTING...")

        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        drive_service = build('drive', 'v3', credentials=credentials)
        print("✅ Connexion à Google Drive réussie")

        # 1. Récupérer le dossier 'SOCIAL POSTING'
        main_folder_id = '1cXn22CJ8YlMftyARZcImJiMC4pSybOHE'

        # 2. Trouver le dossier du client
        response = drive_service.files().list(
            q=f"name='{client}' and mimeType='application/vnd.google-apps.folder' and '{main_folder_id}' in parents",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        folders = response.get('files', [])

        if not folders:
            return jsonify({"error": f"Dossier client '{client}' introuvable"}), 404

        client_folder_id = folders[0]['id']
        print(f"📁 Dossier du client trouvé : {client_folder_id}")

        # 3. Trouver la vidéo dans /Post-Video-AddMusic/
        video_response = drive_service.files().list(
            q=f"name='{video_name}' and '{client_folder_id}' in parents",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        video_files = video_response.get('files', [])

        if not video_files:
            return jsonify({"error": f"Vidéo '{video_name}' introuvable"}), 404

        video_id = video_files[0]['id']
        print(f"🎥 Vidéo trouvée : {video_name} (ID: {video_id})")

        # 4. Sélectionner une musique aléatoire dans le dossier /Music/
        music_folder_response = drive_service.files().list(
            q=f"name='Music' and '{client_folder_id}' in parents and mimeType='application/vnd.google-apps.folder'",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        music_folders = music_folder_response.get('files', [])

        if not music_folders:
            return jsonify({"error": "Dossier Music introuvable"}), 404

        music_folder_id = music_folders[0]['id']
        music_files = drive_service.files().list(
            q=f"'{music_folder_id}' in parents and mimeType contains 'audio/'",
            spaces='drive',
            fields='files(id, name)'
        ).execute().get('files', [])

        if not music_files:
            return jsonify({"error": "Aucune musique disponible"}), 404

        music_choice = random.choice(music_files)
        print(f"🎵 Musique sélectionnée : {music_choice['name']}")

        # 👉 Ici tu ajouteras l’appel à FFmpeg ou Colab si besoin pour fusionner
        return jsonify({
            "status": "success",
            "message": f"Fusion possible pour {video_name} avec la musique {music_choice['name']}"
        }), 200

    except KeyError as e:
        return jsonify({"error": f"Paramètre manquant : {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"Erreur serveur : {str(e)}"}), 500
