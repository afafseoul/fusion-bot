import os
import requests
from moviepy.editor import *
from utils.text_overlay import generate_text_overlay


def download_file_from_drive(audio_filename, drive_folder_id, save_path):
    # Format Google Drive URL (publique + fichier directement téléchargeable)
    url = f"https://drive.google.com/uc?export=download&id={drive_folder_id}"
    print(f"[INFO] 📥 Téléchargement audio depuis Google Drive : {url}")

    response = requests.get(url, stream=True)
    if response.status_code != 200:
        raise Exception(f"Erreur téléchargement audio : {response.status_code}")

    with open(save_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"[INFO] ✅ Audio téléchargé → {save_path}")


def download_gif(gif_url, save_path):
    response = requests.get(gif_url, stream=True)
    if response.status_code != 200:
        raise Exception(f"Erreur téléchargement GIF : {gif_url}")

    with open(save_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)


def generate_video(audio_filename, drive_folder_id, subtitles, output_name, temp_dir):
    try:
        # Téléchargement de l'audio
        audio_path = os.path.join(temp_dir, audio_filename)
        download_file_from_drive(audio_filename, drive_folder_id, audio_path)

        clips = []
        for idx, item in enumerate(subtitles):
            gif_url = item["gif_url"]
            gif_path = os.path.join(temp_dir, f"gif_{idx}.gif")
            download_gif(gif_url, gif_path)

            start = float(item["start"])
            end = float(item["end"])
            duration = end - start
            text = item["text"]

            # Chargement et découpe du GIF
            gif_clip = VideoFileClip(gif_path).subclip(0, duration)
            gif_clip = gif_clip.set_duration(duration)

            # Génération des sous-titres
            subtitle_clip = generate_subtitle_clips(text, duration, gif_clip.size)
            final_clip = CompositeVideoClip([gif_clip, subtitle_clip])
            clips.append(final_clip)

        final_video = concatenate_videoclips(clips, method="compose")
        final_video = final_video.set_audio(AudioFileClip(audio_path))

        output_path = os.path.join(temp_dir, output_name)
        final_video.write_videofile(output_path, codec="libx264", audio_codec="aac")

        print(f"[✅] Vidéo générée : {output_path}")
        return output_path

    except Exception as e:
        raise Exception(f"[❌] Erreur dans generate_video: {str(e)}")
