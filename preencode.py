# preencode.py — pré-encodage GIF/vidéo -> MP4 optimisé (1080x1920)
import os, tempfile, subprocess, urllib.request, logging
from typing import Optional, Dict
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# -------- Google Drive helper --------
def _gdrive_service():
    path = os.getenv("GOOGLE_CREDS", "/etc/secrets/credentials.json")
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(path, scopes=scopes)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def upload_to_drive(local_path: str, folder_id: str, logger: logging.Logger, req_id: str) -> Dict[str, str]:
    svc = _gdrive_service()
    meta = {"name": os.path.basename(local_path)}
    if folder_id:
        meta["parents"] = [folder_id]

    media = MediaFileUpload(local_path, mimetype="video/mp4", resumable=False)
    resp = svc.files().create(body=meta, media_body=media,
                              fields="id,webViewLink,webContentLink",
                              supportsAllDrives=True).execute()
    logger.info(f"[{req_id}] pré-encodage upload ok id={resp.get('id')}")
    return resp

# -------- Pré-encode GIF -> MP4 --------
def preencode_file(gif_url: str, output_name: str, drive_folder_id: str,
                   logger: logging.Logger, req_id: str) -> Dict[str, str]:
    tmp_dir = tempfile.mkdtemp(prefix="preencode_")
    local_in = os.path.join(tmp_dir, "input.gif")
    local_out = os.path.join(tmp_dir, output_name)

    # 1. Télécharger le GIF / vidéo
    urllib.request.urlretrieve(gif_url, local_in)
    logger.info(f"[{req_id}] pré-encodage download ok -> {local_in}")

    # 2. Encodage optimisé (scale 1080x1920, pad, fps 30, H.264)
    cmd = [
        "ffmpeg", "-y", "-i", local_in,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
               "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,fps=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "28",
        "-movflags", "+faststart",
        local_out
    ]
    logger.info(f"[{req_id}] CMD pré-encodage: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    # 3. Upload sur Drive
    resp = upload_to_drive(local_out, drive_folder_id, logger, req_id)

    return {"status": "ok", "drive_file_id": resp.get("id"),
            "drive_webViewLink": resp.get("webViewLink")}
