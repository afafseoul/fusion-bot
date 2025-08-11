import os
import re
import ffmpeg
import requests
from typing import List, Dict, Tuple, Optional

# ---------- Helpers ----------

def _normalize_gif_url(url: str) -> str:
    """
    Accepte une URL média directe ou une page GIPHY.
    Si c’est une page https://giphy.com/gifs/<slug>-<id>,
    on transforme en asset MP4 direct :
    https://media.giphy.com/media/<id>/giphy.mp4
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

def _download(url: str, dest_path: str):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    return dest_path

def _ensure_media(url: str, workdir: str, index: int) -> str:
    url = _normalize_gif_url(url)
    ext = ".mp4" if url.lower().endswith(".mp4") else (
        ".gif" if url.lower().endswith(".gif") else ".bin"
    )
    path = os.path.join(workdir, f"src_{index}{ext}")
    return _download(url, path)

def _seconds(val) -> float:
    try:
        return float(val)
    except Exception:
        raise ValueError(f"Duration/start_time must be numeric seconds. Got: {val}")

def _gen_srt(plan: List[Dict], srt_path: str) -> Optional[str]:
    """
    Construit un .srt depuis le plan.
    Pour chaque item, prend 'text' (ou 'subtitle_text'/'caption')
    et les fenêtres 'subtitles' de type "HH:MM:SS,mmm --> HH:MM:SS,mmm".
    """
    lines = []
    idx = 1
    for item in plan:
        text = item.get("text") or item.get("subtitle_text") or item.get("caption")
        times = item.get("subtitles") or []
        if not text or not times:
            continue
        for t in times:
            t = str(t).strip()
            if "-->" not in t:
                continue
            start, end = [p.strip() for p in t.split("-->")]
            lines.append(str(idx))
            lines.append(f"{start} --> {end}")
            for ln in str(text).splitlines():
                lines.append(ln)
            lines.append("")
            idx += 1

    if not lines:
        return None

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return srt_path

# ---------- Core ----------

def generate_video_from_plan(
    plan: List[Dict],
    output_path: str,
    size: Tuple[int, int] = (1080, 1920),
    fps: int = 30,
    audio_url: Optional[str] = None,
    workdir: Optional[str] = None,
) -> str:
    """
    Construit une vidéo verticale à partir d’items de forme :
    {
        "gif_url": "... (mp4/gif direct ou page GIPHY)",
        "start_time": 0,
        "duration": 3.5,
        "text": "Texte à afficher pour ce segment (optionnel)",
        "subtitles": ["00:00:00,000 --> 00:00:03,500"]  # sur la timeline globale
    }
    - audio_url : URL directe d’un fichier audio (mp3/wav/ogg). Optionnel.
    - output_path : chemin final .mp4
    """
    width, height = size
    os.makedirs(workdir or os.path.dirname(output_path) or ".", exist_ok=True)
    workdir = workdir or os.path.dirname(output_path) or "."

    # 1) Téléchargement / préparation des segments
    segment_paths = []
    for i, item in enumerate(sorted(plan, key=lambda x: x.get('start_time', 0))):
        src_path = _ensure_media(item["gif_url"], workdir, i)
        seg_path = os.path.join(workdir, f"seg_{i:03d}.mp4")
        duration = _seconds(item.get("duration", 0))

        # Mise à l’échelle 1080 de large, padding en 1080x1920, fps, découpe à 'duration'
        v = ffmpeg.input(src_path, stream_loop=-1)  # loop si GIF trop court
        v = (v.filter("scale", width, -2)
               .filter("pad", width, height, "(ow-iw)/2", "(oh-ih)/2")
               .filter("fps", fps))
        out = ffmpeg.output(
            v, seg_path,
            vcodec="libx264", pix_fmt="yuv420p", r=fps, t=duration, loglevel="error"
        )
        ffmpeg.run(out, overwrite_output=True)
        segment_paths.append(seg_path)

    # 2) Concat des segments
    concat_txt = os.path.join(workdir, "concat.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in segment_paths:
            f.write(f"file '{p}'\n")

    concatenated = os.path.join(workdir, "concat.mp4")
    concat_stream = ffmpeg.input(concat_txt, f="concat", safe=0)
    out = ffmpeg.output(
        concat_stream,
        concatenated,
        vcodec="libx264",
        pix_fmt="yuv420p",
        r=fps,
        loglevel="error"
    )
    ffmpeg.run(out, overwrite_output=True)

    # 3) Ajout audio (si fourni)
    no_subs = os.path.join(workdir, "video_audio.mp4")
    if audio_url:
        audio_path = os.path.join(workdir, "audio" + (os.path.splitext(audio_url)[1] or ".mp3"))
        _download(audio_url, audio_path)
        v = ffmpeg.input(concatenated)
        a = ffmpeg.input(audio_path)
        out = ffmpeg.output(
            v, a, no_subs,
            vcodec="libx264", acodec="aac", audio_bitrate="192k",
            shortest=None,  # s’arrête au plus court
            r=fps, loglevel="error"
        )
        ffmpeg.run(out, overwrite_output=True)
    else:
        no_subs = concatenated

    # 4) Gravure des sous-titres (si 'text' + 'subtitles' présents)
    srt_path = _gen_srt(plan, os.path.join(workdir, "subs.srt"))
    if srt_path:
        style = (
            "FontName=Noto Sans,Fontsize=52,"
            "PrimaryColour=&H00FFFFFF&,OutlineColour=&H00101010&,"
            "BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV=70"
        )
        v = ffmpeg.input(no_subs).filter("subtitles", srt_path, force_style=style)
        out = ffmpeg.output(v, output_path, vcodec="libx264", pix_fmt="yuv420p", r=fps, loglevel="error")
        ffmpeg.run(out, overwrite_output=True)
    else:
        if no_subs != output_path:
            import shutil
            shutil.move(no_subs, output_path)

    return output_path
