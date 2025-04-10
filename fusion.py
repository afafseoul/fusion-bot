from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
import traceback

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'

SOCIAL_POSTING_ID = "1cXn22CJ8YlMftyARZcImJiMC4pSybOHE"

@app.route('/', methods=['GET'])
def home():
    return "✅ Fusion Bot is alive"

@app.route('/start', methods=['POST'])
def start():
    try:
        print("📥 Requête reçue...")

        # Étape 1 : lecture JSON
        try:
            data = request.get_json(force=True)
            print("✅ Données JSON reçues :", data)
        except Exception as e:
            print("❌ JSON invalide")
            return jsonify({"status": "error", "message": "JSON mal formé", "details": str(e)}), 400

        # Étape 2 : vérif des clés
        client = data.get("client")
        video_name = data.get("video_name")
        if not client or not video_name:
            print("❌ Paramètres manquants :", {"client": client, "video_name": video_name})
            return jsonify({"status": "error", "message": "Paramètres 'client' ou 'video_name' manquants"}), 400

        print(f"🔍 Recherche du dossier du client '{client}' dans SOCIAL POSTING...")

        # Étape 3 : auth Google Drive
        try:
            credentials = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
            drive_service = build('drive', 'v3', credentials=credentials)
            print("✅ Connexion à Google Drive réussie")
        except Exception as e:
            print("❌ Erreur d'authentification Google")
            traceback.print_exc()
            return jsonify({"status": "error", "message": "Échec connexion Google Drive", "details": str(e)}), 500

        # Étape 4 : recherche du dossier client
        try:
            response = drive_service.files().list(
                q=f"name='{client}' and mimeType='application/vnd.google-apps.folder' and '{SOCIAL_POSTING_ID}' in parents",
                spaces='drive',
                fields='files(id, name)'
            ).execute()

            folders = response.get('files', [])
            if not folders:
                print("❌ Dossier client introuvable dans SOCIAL POSTING")
                return jsonify({
                    "status": "error",
                    "message": f"Dossier client '{client}' introuvable dans SOCIAL POSTING"
                }), 404

            client_folder_id = folders[0]['id']
            print(f"✅ Dossier client trouvé : {client_folder_id}")
        except Exception as e:
            print("❌ Erreur lors de la recherche du dossier client")
            traceback.print_exc()
            return jsonify({"status": "error", "message": "Erreur lors de la recherche du dossier client", "details": str(e)}), 500

        # ✅ Réponse finale (pour l’instant jusqu’ici)
        return jsonify({
            "status": "success",
            "client": client,
            "video_name": video_name,
            "client_folder_id": client_folder_id
        }), 200

    except Exception as e:
        print("💥 ERREUR INATTENDUE")
        traceback.print_exc()
        return jsonify({"status": "error", "message": "Erreur serveur inattendue", "details": str(e)}), 500
