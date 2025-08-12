import os, re, requests, tempfile
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, TextClip, concatenate_videoclips, vfx
from utils.text_overlay import generate_text_overlay


def _normalize_giphy(url: str) -> str:
    """
    Transforme une page GIPHY en asset vidéo direct si besoin.
    https://giphy.com/gifs/<slug>-<id> -> https://media.giphy.com/media/<id>/giphy.mp4
    """
    try:
        if "giphy.com/gifs" in url and "/media/" not in url:
            m = re.search(r"-([a-zA-Z0-9]+)$", url.rstrip("/"))
            if m:
                gid = m.group(1)
                return f"https://media.giphy.com/media/{gid}/giphy.mp4"
        return url
    except Exception:
        return url

def _download(url: str, dest: str):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
    return dest

def _hhmmssms(seconds: float):
    ms = int(round((seconds - int(seconds)) * 1000))
    s  = int(seconds) % 60
    m  = (int(seconds) // 60) % 60
    h  = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def generate_video(
    plan,
    audio_path: str,
    output_name: str,
    temp_dir: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
):
    """
    plan: liste d'objets:
      { gif_url, start_time?, duration, text?, subtitles?[] }
    """
    os.makedirs(temp_dir, exist_ok=True)
    clips = []

    timeline = 0.0
    for idx, item in enumerate(plan):
        url = _normalize_giphy(item.get("gif_url", ""))
        if not url:
            continue

        src_ext = ".mp4" if url.lower().endswith(".mp4") else ".gif"
        src_path = os.path.join(temp_dir, f"src_{idx}{src_ext}")
        _download(url, src_path)

        dur = float(item.get("duration", 0)) or 0.0
        if dur <= 0:
            continue

        # clip vidéo
        clip = VideoFileClip(src_path).without_audio()
        # resize + letterbox vertical
        clip = clip.fx(vfx.resize, width=width)
        if clip.h < height:
            clip = clip.on_color(size=(width, height), color=(0,0,0), pos=("center","center"))
        elif clip.h > height:
            clip = clip.fx(vfx.resize, height=height).on_color(size=(width, height), color=(0,0,0), pos=("center","center"))

        clip = clip.set_duration(dur)

        # overlay texte (facultatif)
        txt = (item.get("text") or "").strip()
        if txt:
            try:
                sub = generate_text_overlay(txt, dur, (width, height))
                clip = CompositeVideoClip([clip, sub]).set_duration(dur)
            except Exception:
                # si ImageMagick n'est pas dispo, on sort quand même sans overlay
                pass

        clips.append(clip)

        # injecte les sous-titres si absents
        if not item.get("subtitles"):
            start = float(item.get("start_time", timeline))
            end = start + dur
            item["subtitles"] = [f"{_hhmmssms(start)} --> {_hhmmssms(end)}"]
        timeline += dur

    if not clips:
        raise ValueError("Plan vide ou durées nulles.")

    video = concatenate_videoclips(clips, method="compose")
    audio = AudioFileClip(audio_path)
    final = video.set_audio(audio)
    out_path = os.path.join(temp_dir, output_name)
    final.write_videofile(out_path, codec="libx264", audio_codec="aac", fps=fps)

    return out_path
