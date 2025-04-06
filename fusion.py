from flask import Flask, request
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
        data = request.get_json()
        client = data.get("client")
        video_name = data.get("video_name")

        if not client or not video_name:
            return "❌ Missing 'client' or 'video_name'", 400

        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        drive_service = build('drive', 'v3', credentials=credentials)

        print(f"✅ Connected to Drive for client: {client}")
        print(f"🎬 Video to process: {video_name}")

        # TEMPORAIRE : on retourne juste les infos pour tester la structure
        return {
            "message": "🔁 Received request",
            "client": client,
            "video_name": video_name
        }

    except Exception as e:
        print(f"❌ Internal Error: {str(e)}")
        return f"❌ Error: {str(e)}", 500
