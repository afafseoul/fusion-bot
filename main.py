from flask import Flask, request, jsonify
import os, tempfile, uuid, base64, traceback

from video_generator import generate_video_from_plan

# === Google Drive ===
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account

SERVICE_ACCOUNT_PATH = "/etc/secrets/credentials.json"   # Secret Render
DEFAULT_DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")   # Optionnel (fallback)
MAKE_PUBLIC = os.getenv("DRIVE_MAKE_PUBLIC", "true").lower() == "true"

app = Flask(__name__)

def _drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH,
        scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    return build("drive", "v3", credentials=creds)

def upload_to_drive(file_path: str, folder_id: str, make_public: bool = True):
    service = _drive_service()
    meta = {"name": os.path.basename(file_path)}
    if folder_id:
        meta["parents"] = [folder_id]
    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True)

    created = service.files().create(body=meta, media_body=media, fields="id,name").execute()
    file_id = created.get("id")

    web_view, web_content = None, None
    if make_public:
        try:
            service.permissions().create(
                fileId=file_id,
                body={"role": "reader", "type": "anyone"},
                fields="id"
            ).execute()
            got = service.files().get(fileId=file_id, fields="webViewLink,webContentLink").execute()
            web_view = got.get("webViewLink")
            web_content = got.get("webContentLink")
        except HttpError:
            pass

    return {"file_id": file_id, "webViewLink": web_view, "webContentLink": web_content}

@app.get("/")
def health():
    return "🟢 fusion-bot ready"

@app.post("/create-video")
def create_video():
    try:
        data = request.get_json(force=True, silent=False)
        if not data or "plan" not in data:
            return jsonify({"error": "Missing 'plan' array in JSON"}), 400

        width  = int(data.get("width", 1080))
        height = int(data.get("height", 1920))
        fps    = int(data.get("fps", 30))

        audio_base64 = data.get("audio_base64")
        audio_url    = data.get("audio_url")

        workdir = os.path.join(tempfile.gettempdir(), f"fusionbot_{uuid.uuid4().hex}")
        os.makedirs(workdir, exist_ok=True)

        audio_path = None
        if audio_base64:
            audio_path = os.path.join(workdir, "audio.mp3")
            with open(audio_path, "wb") as f:
                f.write(base64.b64decode(audio_base64))

        output_path = os.path.join(workdir, data.get("output_name", "output.mp4"))

        # 1) Montage
        result_path = generate_video_from_plan(
            plan=data["plan"],
            output_path=output_path,
            size=(width, height),
            fps=fps,
            audio_url=(None if audio_path else audio_url),
            audio_path=audio_path,
            workdir=workdir
        )

        # 2) Upload Drive
        drive_folder_id = data.get("drive_folder_id") or DEFAULT_DRIVE_FOLDER_ID
        drive_info = None
        if drive_folder_id:
            drive_info = upload_to_drive(result_path, drive_folder_id, make_public=MAKE_PUBLIC)

        return jsonify({
            "status": "success",
            "local_path": result_path,
            "drive": drive_info
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
