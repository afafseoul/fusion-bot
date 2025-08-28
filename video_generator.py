# -*- coding: utf-8 -*-
# video_generator.py — réencodage UNIFORME par segment (codec/résolution/FPS/timescale)

import os, time, math, shutil, subprocess, logging, urllib.request, json, shlex
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse, parse_qs

UA = "Mozilla/5.0 (compatible; RenderBot/1.0)"

# -------------------- utils ffmpeg --------------------
def _run(cmd: str, logger: logging.Logger, req_id: str):
    logger.info(f"[{req_id}] CMD: {cmd}")
    p = subprocess.run(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    logger.info(f"[{req_id}] STDERR: {p.stdout}")
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")

def _ffprobe_duration(path: str) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=noprint_wrappers=1:nokey=1", path],
            stderr=subprocess.STDOUT
        ).decode("utf-8","ignore").strip()
        return max(0.0, float(out))
    except Exception:
        return 0.0

def _ffprobe_json(path: str) -> dict:
    try:
        out = subprocess.check_output([
            "ffprobe","-v","error","-print_format","json",
            "-show_format","-show_streams", path
        ], stderr=subprocess.STDOUT, timeout=10)
        return json.loads(out.decode("utf-8","ignore"))
    except Exception:
        return {}

def _kind(path: str) -> Tuple[bool, bool]:
    """Retourne (has_video, is_gif) pour un fichier local."""
    info = _ffprobe_json(path)
    fm = (info.get("format",{}) or {}).get("format_name","")
    has_video = any((s or {}).get("codec_type") == "video" for s in info.get("streams",[]) )
    is_gif = ("gif" in fm)
    return has_video, is_gif
# ------------------------------------------------------

# --------------- Google Drive helpers ----------------
def _normalize_drive_url(url: str) -> str:
    """
    Accepte:
      - https://drive.google.com/file/d/FILE_ID/view?usp=sharing
      - https://drive.google.com/uc?id=FILE_ID&export=download
    Renvoie l'URL CDN directe robuste:
      - https://drive.usercontent.google.com/download?id=FILE_ID&export=download
    """
    try:
        u = urlparse(url)
        host = (u.netloc or "").lower()
        if "drive.google.com" in host or "drive.usercontent.google.com" in host:
            q = parse_qs(u.query or "")
            fid = q.get("id", [None])[0]
            if not fid and "/file/d/" in u.path:
                # /file/d/<ID>/view
                fid = u.path.split("/file/d/")[1].split("/")[0]
            if fid:
                return f"https://drive.usercontent.google.com/download?id={fid}&export=download"
    except Exception:
        pass
    return url

def _looks_like_html(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(256).lower()
        return head.startswith(b"<!doctype html") or head.startswith(b"<html")
    except Exception:
        return False
# ------------------------------------------------------

def _download(url: str, dst_noext: str, logger: logging.Logger, req_id: str) -> str:
    """
    Télécharge URL (Drive/Giphy/etc.). Retourne le chemin final AVEC la bonne extension.
    - Si Content-Type=gif -> .gif ; mp4 -> .mp4 ; sinon -> tente via l’URL, sinon .bin
    - Réécrit au besoin les URLs Google Drive pour éviter les pages d'interstitiel.
    - Refuse les réponses HTML (fichier non public/quota).
    """
    os.makedirs(os.path.dirname(dst_noext), exist_ok=True)

    final_url = _normalize_drive_url(url)

    req = urllib.request.Request(
        final_url,
        headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.pinterest.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        ct = (r.info().get_content_type() or "").lower()
        if "gif" in ct:
            ext = ".gif"
        elif "mp4" in ct or "video/" in ct:
            ext = ".mp4"
        else:
            low = final_url.lower()
            if ".gif" in low:   ext = ".gif"
            elif ".mp4" in low: ext = ".mp4"
            else:               ext = ".bin"
        dst = dst_noext + ext
        with open(dst, "wb") as f:
            shutil.copyfileobj(r, f)

    size = os.path.getsize(dst)
    if size <= 0:
        raise RuntimeError("downloaded file is empty")
    # Drive peut renvoyer une page HTML si partagé en privé / quota dépassé
    if "text/html" in ct or _looks_like_html(dst):
        raise RuntimeError(
            "Downloaded file is not a valid media (got HTML). "
            "Assure le partage public et/ou utilise l’URL directe (drive.usercontent...)."
        )

    logger.info(f"[{req_id}] download ok -> {dst} size={size}B ct={ct}")
    return dst

def _encode_uniform(src: str, dst: str, width: int, height: int, fps: int, need_dur: float,
                    logger: logging.Logger, req_id: str):
    """
    Uniformise chaque segment en H.264 WxH@fps + yuv420p.
    - GIF local  : -ignore_loop 0 (pas de -stream_loop)
    - MP4 local  : -stream_loop -1 (boucle) + -t need_dur
    - M3U8 (URL) : lecture réseau directe + headers UA/Referer + -t need_dur
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps}"
    )

    src_low = src.lower()
    is_m3u8 = (src_low.startswith("http") and ".m3u8" in src_low)

    if is_m3u8:
        # Headers utiles pour les CDN de pinimg
        headers = f"User-Agent: {UA}\\r\\nReferer: https://www.pinterest.com/\\r\\n"
        in_flags = (
            f'-headers "{headers}" '
            '-protocol_whitelist "file,http,https,tcp,tls,crypto" '
            f'-t {need_dur:.3f} -i {shlex.quote(src)}'
        )
    elif src_low.endswith(".gif"):
        in_flags = f'-ignore_loop 0 -t {need_dur:.3f} -i {shlex.quote(src)}'
    else:
        in_flags = f'-stream_loop -1 -t {need_dur:.3f} -i {shlex.quote(src)}'

    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"{in_flags} "
        f'-vf "{vf}" -pix_fmt yuv420p -r {fps} -vsync cfr '
        "-c:v libx264 -preset superfast -crf 26 "
        "-movflags +faststart "
        "-video_track_timescale 90000 "
        f"{shlex.quote(dst)}"
    )
    _run(cmd, logger, req_id)

def _concat_copy_strict(parts: List[str], out_path: str, logger: logging.Logger, req_id: str) -> str:
    """
    Concat en COPY via demuxer. Les segments sont déjà uniformisés.
    Ajoute genpts/avoid_negative_ts pour éviter les warnings de DTS.
    """
    lst = out_path + ".txt"
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")

    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"-f concat -safe 0 -i {shlex.quote(lst)} "
        "-fflags +genpts -avoid_negative_ts make_zero "
        "-c copy -movflags +faststart "
        f"{shlex.quote(out_path)}"
    )
    try:
        _run(cmd, logger, req_id)
        return "concat_copy"
    except Exception:
        # Fallback (rare) : ré-encode le final via concat filter (toujours uniforme → rapide)
        inputs = " ".join(f"-i {shlex.quote(p)}" for p in parts)
        n = len(parts)
        maps = "".join(f"[{i}:v:0]" for i in range(n))
        cmd2 = (
            f"ffmpeg -y -hide_banner -loglevel error {inputs} "
            f"-filter_complex \"{maps}concat=n={n}:v=1:a=0[v]\" "
            "-map \"[v]\" -c:v libx264 -preset superfast -crf 26 "
            "-pix_fmt yuv420p -movflags +faststart -r 30 "
            "-video_track_timescale 90000 "
            f"{shlex.quote(out_path)}"
        )
        _run(cmd2, logger, req_id)
        return "concat_filter"

def _mux_audio(video_path: str, audio_path: str, out_path: str, logger: logging.Logger, req_id: str):
    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"-i {shlex.quote(video_path)} -i {shlex.quote(audio_path)} "
        "-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 128k "
        "-shortest -movflags +faststart "
        f"{shlex.quote(out_path)}"
    )
    _run(cmd, logger, req_id)

def generate_video(
    plan: List[Dict[str, Any]],
    audio_path: str,
    output_name: str,
    temp_dir: str,
    width: int,
    height: int,
    fps: int,
    logger: logging.Logger,
    req_id: str,
    global_srt: str = None
):
    parts: List[str] = []

    for i, seg in enumerate(plan):
        url = seg.get("gif_url") or seg.get("url") or seg.get("video_url")
        if not url:
            raise ValueError(f"plan[{i}] missing 'gif_url' or 'url'")
        try:
            dur = float(seg.get("duration") or 0.0)
        except Exception:
            dur = 0.0
        if dur <= 0.0:
            dur = 0.5  # mini sécurité si pas de durée fournie

        logger.info(f"[{req_id}] seg#{i} url={url} dur_req={dur:.3f}")

        # HLS Pinterest: ne pas télécharger → lecture directe de l'URL .m3u8
        if url.lower().startswith("http") and ".m3u8" in url.lower():
            src_for_encode = url
        else:
            base = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}")
            src_for_encode = _download(url, base, logger, req_id)
            has_video, is_gif = _kind(src_for_encode)
            if not (has_video or is_gif):
                raise RuntimeError(
                    "Downloaded file is not a valid video/gif. "
                    "Drive peut avoir renvoyé une page HTML. "
                    "Assure le partage public et utilise 'uc?export=download&id=...' ou l'URL usercontent."
                )

        part_path = os.path.join(temp_dir, f"part_{i:03d}.mp4")
        _encode_uniform(
            src=src_for_encode, dst=part_path, width=width, height=height, fps=fps,
            need_dur=dur, logger=logger, req_id=req_id
        )
        parts.append(part_path)

    if not parts:
        raise ValueError("empty parts")

    # Concat vidéo
    video_only = os.path.join(temp_dir, "_video.mp4")
    mode = _concat_copy_strict(parts, video_only, logger, req_id)

    # Mux audio
    out_path = os.path.join(temp_dir, output_name)
    _mux_audio(video_only, audio_path, out_path, logger, req_id)

    debug = {
        "status": "ok",
        "items": len(parts),
        "mode": mode,
        "notes": "uniformize segments (libx264,yuv420p,WxH,fps,timescale) -> concat copy -> mux audio"
    }
    return out_path, debug
