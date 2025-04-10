# === PARAMÈTRES ===
music_folder_name = "Music"
ready_to_post_folder_name = "ReadyToPost"

# === TROUVER LA VIDÉO ===
video_files = drive_service.files().list(
    q=f"'{client_folder_id}' in parents and name = '{video_name}' and trashed = false",
    fields="files(id, name)"
).execute().get("files", [])

if not video_files:
    return jsonify({"error": f"Vidéo '{video_name}' introuvable"}), 404

video_file = video_files[0]
video_file_id = video_file["id"]

# === TROUVER LE DOSSIER MUSIC ===
music_folders = drive_service.files().list(
    q=f"'{client_folder_id}' in parents and name = '{music_folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
    fields="files(id, name)"
).execute().get("files", [])

if not music_folders:
    return jsonify({"error": "Dossier 'Music' introuvable"}), 404

music_folder_id = music_folders[0]["id"]

# === TROUVER UNE MUSIQUE (n'importe laquelle pour l’instant) ===
music_files = drive_service.files().list(
    q=f"'{music_folder_id}' in parents and trashed = false and mimeType contains 'audio'",
    fields="files(id, name)"
).execute().get("files", [])

if not music_files:
    return jsonify({"error": "Aucune musique trouvée dans 'Music'"}), 404

music_file = random.choice(music_files)
music_name = music_file["name"]
music_file_id = music_file["id"]

# === EXTRAIRE LE DÉCALAGE TEMPOREL DEPUIS LE NOM DE LA MUSIQUE ===
import re
match = re.search(r'@(\d+)', music_name)
start_offset = int(match.group(1)) if match else 0

# === TÉLÉCHARGER LA VIDÉO ET LA MUSIQUE ===
from pathlib import Path

temp_video_path = "/tmp/video.mp4"
temp_music_path = "/tmp/music.mp3"
final_output_path = "/tmp/output.mp4"

with open(temp_video_path, "wb") as f:
    f.write(drive_service.files().get_media(fileId=video_file_id).execute())

with open(temp_music_path, "wb") as f:
    f.write(drive_service.files().get_media(fileId=music_file_id).execute())

# === FUSION VIA FFMPEG ===
import subprocess

cmd = [
    "ffmpeg", "-y",
    "-i", temp_video_path,
    "-ss", str(start_offset),
    "-i", temp_music_path,
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy",
    "-shortest",
    final_output_path
]

subprocess.run(cmd, check=True)

# === TROUVER / CRÉER DOSSIER ReadyToPost ===
ready_folders = drive_service.files().list(
    q=f"'{client_folder_id}' in parents and name = '{ready_to_post_folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
    fields="files(id, name)"
).execute().get("files", [])

if ready_folders:
    ready_folder_id = ready_folders[0]["id"]
else:
    ready_folder = drive_service.files().create(
        body={
            "name": ready_to_post_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [client_folder_id]
        },
        fields="id"
    ).execute()
    ready_folder_id = ready_folder["id"]

# === UPLOAD DU FICHIER FINAL ===
from googleapiclient.http import MediaFileUpload

final_filename = f"fusion-{video_name}"
media = MediaFileUpload(final_output_path, resumable=True, mimetype="video/mp4")

drive_service.files().create(
    body={
        "name": final_filename,
        "parents": [ready_folder_id],
        "mimeType": "video/mp4"
    },
    media_body=media
).execute()
