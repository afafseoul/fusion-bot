import os, re, ffmpeg, requests, shutil
from typing import List, Dict, Tuple, Optional

# ---------- helpers ----------

def _normalize_gif_url(url: str) -> str:
    """
    Si on reçoit une page GIPHY du type:
      https://giphy.com/gifs/<slug>-<id>
    on la convertit en asset MP4:
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
            for chunk in r.iter_content(8192):
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

def _s(val) -> float:
    try:
        return float(val)
    except Exception:
        raise ValueError(f"start_time/duration must be numeric seconds. Got: {val}")

def _gen_srt(plan: List[Dict], srt_path: str) -> Optional[str]:
    """
    Pour chaque item du plan, si 'text' (ou 'subtitle_text'/'caption') ET 'subtitles' (array de
    fenêtres "HH:MM:SS,mmm --> HH:MM:SS,mmm") sont présents, on génère un .srt.
    """
    lines, idx = [], 1
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

# ---------- core ----------

def generate_video_from_plan(
    plan: List[Dict],
    output_path: str,
    size: Tuple[int, int] = (1080, 1920),
    fps: int = 30,
    audio_url: Optional[str] = None,
    audio_path: Optional[str] = None,
    workdir: Optional[str] = None,
) -> str:
    width, height = size
    os.makedirs(workdir or os.path.dirname(output_path) or ".", exist_ok=True)
    workdir = workdir or os.path.dirname(output_path) or "."

    # 1) préparer les segments (tri par start_time)
    segments = []
    for i, item in enumerate(sorted(plan, key=lambda x: x.get("start_time", 0))):
        src = _ensure_media(item["gif_url"], workdir, i)
        seg = os.path.join(workdir, f"seg_{i:03d}.mp4")
        duration = _s(item.get("duration", 0))

        # mise à l’échelle, padding 1080x1920, fps, et coupe à duration
        v = ffmpeg.input(src)  # .gif ou .mp4
        v = (v.filter("scale", width, -2)
               .filter("pad", width, height, "(ow-iw)/2", "(oh-ih)/2")
               .filter("fps", fps))
        out = ffmpeg.output(v, seg, vcodec="libx264", pix_fmt="yuv420p", r=fps, t=duration, loglevel="error")
        ffmpeg.run(out, overwrite_output=True)
        segments.append(seg)

    # 2) concat
    concat_txt = os.path.join(workdir, "concat.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in segments:
            f.write(f"file '{p}'\n")

    concatenated = os.path.join(workdir, "concat.mp4")
    concat_stream = ffmpeg.input(concat_txt, f="concat", safe=0)
    out = ffmpeg.output(concat_stream, concatenated, vcodec="libx264", pix_fmt="yuv420p", r=fps, loglevel="error")
    ffmpeg.run(out, overwrite_output=True)

    # 3) audio (local > url)
    if not audio_path and audio_url:
        audio_path = os.path.join(workdir, "audio" + (os.path.splitext(audio_url)[1] or ".mp3"))
        _download(audio_url, audio_path)

    with_audio = os.path.join(workdir, "video_audio.mp4")
    if audio_path:
        v = ffmpeg.input(concatenated)
        a = ffmpeg.input(audio_path)
        out = ffmpeg.output(v, a, with_audio, vcodec="libx264", acodec="aac", audio_bitrate="192k",
                            shortest=1, r=fps, loglevel="error")
        ffmpeg.run(out, overwrite_output=True)
    else:
        with_audio = concatenated

    # 4) sous-titres (si fournis)
    srt_file = _gen_srt(plan, os.path.join(workdir, "subs.srt"))
    if srt_file:
        style = (
            "FontName=Noto Sans,Fontsize=52,"
            "PrimaryColour=&H00FFFFFF&,OutlineColour=&H00101010&,"
            "BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV=70"
        )
        v = ffmpeg.input(with_audio).filter("subtitles", srt_file, force_style=style)
        out = ffmpeg.output(v, output_path, vcodec="libx264", pix_fmt="yuv420p", r=fps, loglevel="error")
        ffmpeg.run(out, overwrite_output=True)
    else:
        if with_audio != output_path:
            shutil.move(with_audio, output_path)

    return output_path
