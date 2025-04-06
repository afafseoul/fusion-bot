from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'

def connect_drive():
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build('drive', 'v3', credentials=credentials)

@app.route('/', methods=['GET'])
def index():
    return "✅ Fusion Bot is running!"

@app.route('/start', methods=['POST'])
def start_fusion():
    data = request.get_json()
    client_name = data.get("client")
    video_name = data.get("video_name")

    if not client_name or not video_name:
        return jsonify({"status": "error", "message": "Missing client or video_name"}), 400

    try:
        service = connect_drive()

        # Récupère le dossier client par son nom
        folder_query = f"name = '{client_name}' and mimeType = 'application/vnd.google-apps.folder'"
        folder_result = service.files().list(q=folder_query, spaces='drive').execute()
        folder_files = folder_result.get('files', [])

        if not folder_files:
            return jsonify({"status": "error", "message": "Client folder not found"}), 404

        client_folder_id = folder_files[0]['id']

        # Récupère le sous-dossier "Post-Video-AddMusic"
        add_music_query = f"'{client_folder_id}' in parents and name='Post-Video-AddMusic' and mimeType='application/vnd.google-apps.folder'"
        add_music_folder_result = service.files().list(q=add_music_query, spaces='drive').execute()
        add_music_files = add_music_folder_result.get('files', [])

        if not add_music_files:
            return jsonify({"status": "error", "message": "Subfolder 'Post-Video-AddMusic' not found"}), 404

        add_music_folder_id = add_music_files[0]['id']

        # Vérifie si la vidéo existe bien dans "Post-Video-AddMusic"
        video_query = f"name='{video_name}' and '{add_music_folder_id}' in parents"
        video_result = service.files().list(q=video_query, spaces='drive').execute()
        video_files = video_result.get('files', [])

        if not video_files:
            return jsonify({"status": "error", "message": "Video not found"}), 404

        # Ici, tu pourras lancer ton processus de traitement vidéo/audio avec FFmpeg
        # Cette étape est juste une validation initiale.
        # Pour l'instant, je renvoie juste un message de validation :
        
        return jsonify({"status": "success", "message": "Connected and validated. Ready for processing."})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
