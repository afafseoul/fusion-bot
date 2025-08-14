# video_generator.py
import os
import re
import requests
from typing import Any, Dict, List
from urllib.parse import urlparse

from moviepy.editor import (
    VideoFileClip,
    AudioFileClip,
    CompositeVideoClip,
    concatenate_videoclips,
    vfx,
)
from utils.text_overlay import generate_text_overlay


# ---------- Utils ----------

def _normalize_giphy(url: str) -> str:
    """
    Convertit des URLs GIPHY de page en lien média direct.
    Gère les formes courantes :
      - https://giphy.com/gifs/<slug>-<id>
      - https://giphy.com/embed/<id>
      - https://giphy.com/<...>/<id>
      - Laisse passer les liens media[0-4].giphy.com/.../giphy.mp4 ou .gif
    """
    try:
        if not url:
            return url

        u = urlparse(url)
        host = (u.netloc or "").lower()
        path = u.path or ""

        # Déjà un lien media direct (mp4/gif) -> ne rien faire
        if "giphy.com" in host and "/media/" in path and path.endswith((".mp4", ".gif")):
            return url

        # /embed/<id>  -> media/<id>/giphy.mp4
        m = re.search(r"/embed/([A-Za-z0-9]+)", path)
        if m:
            gid = m.group(1)
            return f"https://media.giphy.com/media/{gid}/giphy.mp4"

        # /gifs/<slug>-<id>  -> media/<id>/giphy.mp4
        m = re.search(r"/gifs/.+-([A-Za-z0-9]+)$", path.rstrip("/"))
        if m:
            gid = m.group(1)
            return f"https://media.giphy.com/media/{gid}/giphy.mp4"

        # fallback : dernier segment alphanumérique comme id
        m = re.search(r"([A-Za-z0-9]+)$", path.rstrip("/"))
        if "giphy.com" in host and m:
            gid = m.group(1)
            return f"https://media.giphy.com/media/{gid}/giphy.mp4"

        return url
    except Exception:
        return url


def _download(url: str, dest: str) -> str:
    """
    Télécharge un fichier avec un User-Agent (certains CDNs répondent 403 sinon).
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FusionBot/1.0)"}
    with requests.get(url, stream=True, timeout=60, headers=headers) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
    return dest


def _hhmmssms(seconds: float) -> str:
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ---------- Core ----------

def generate_video(
    plan: List[Dict[str, Any]],
    audio_path: str,
    output_name: str,
    temp_dir: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> str:
    """
    plan: liste d'objets de la forme:
      {
        "gif_url" | "url" | "mp4" | "mp4_url": <string>,
        "duration": <float>,
        "text": <string, optionnel>,
        "start_time": <float, optionnel>,
        "subtitles": [<string>, ...] optionnel
      }
    """
    os.makedirs(temp_dir, exist_ok=True)
    clips: List[VideoFileClip] = []

    timeline = 0.0

    for idx, item in enumerate(plan or []):
        if not isinstance(item, dict):
            # tolérance : si l'item n'est pas un dict, on passe
            continue

        # 1) Récupère l’URL prioritaire (MP4 si disponible)
        url = (
            item.get("mp4")
            or item.get("mp4_url")
            or item.get("gif_url")
            or item.get("url")
            or ""
        )
        url = _normalize_giphy(url)
        if not url:
            continue

        # 2) Télécharge la source
        #    (si pas .mp4 on garde l’extension .gif, MoviePy sait lire les GIF)
        ext = ".mp4" if url.lower().endswith((".mp4", ".mov", ".webm")) else ".gif"
        src_path = os.path.join(temp_dir, f"src_{idx}{ext}")
        try:
            _download(url, src_path)
        except Exception:
            # on skip si le média ne se télécharge pas
            continue

        # 3) Durée
        dur = float(item.get("duration", 0) or 0)
        if dur <= 0:
            continue

        # 4) Clip vidéo (sans audio)
        clip = VideoFileClip(src_path).without_audio()

        # 5) Resize/letterbox en vertical 1080x1920
        #    On fixe d'abord la largeur, puis on ajoute des bandes si besoin
        clip = clip.fx(vfx.resize, width=width)
        if clip.h < height:
            clip = clip.on_color(size=(width, height), color=(0, 0, 0), pos=("center", "center"))
        elif clip.h > height:
            # si après resize largeur la hauteur dépasse, on ajuste la hauteur
            clip = clip.fx(vfx.resize, height=height).on_color(
                size=(width, height), color=(0, 0, 0), pos=("center", "center")
            )

        clip = clip.set_duration(dur)

        # 6) Overlay texte (facultatif)
        txt = (item.get("text") or "").strip()
        if txt:
            try:
                overlay = generate_text_overlay(txt, dur, (width, height))
                clip = CompositeVideoClip([clip, overlay]).set_duration(dur)
            except Exception:
                # si ImageMagick/Pillow pose souci, on continue sans overlay
                pass

        clips.append(clip)

        # 7) Sous-titres auto si absents (SRT-like en mémoire)
        if not item.get("subtitles"):
            start = float(item.get("start_time", timeline) or timeline)
            end = start + dur
            item["subtitles"] = [f"{_hhmmssms(start)} --> {_hhmmssms(end)}"]

        timeline += dur

    if not clips:
        raise ValueError("Plan vide ou médias invalides.")

    # 8) Concaténation & audio final
    video = concatenate_videoclips(clips, method="compose")
    audio = AudioFileClip(audio_path)
    final = video.set_audio(audio)

    out_path = os.path.join(temp_dir, output_name)
    # Conseil: paramètre "threads" peut accélérer selon l’instance Render
    final.write_videofile(out_path, codec="libx264", audio_codec="aac", fps=fps)

    return out_path
