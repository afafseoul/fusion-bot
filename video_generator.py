# video_generator.py — encode segments + burn SRT PAR SEGMENT (style CapCut) + concat copy
# + mux final (voix seule OU voix + musique BG avec délai/volume) en une seule commande ffmpeg
import os, time, shutil, subprocess, logging, urllib.request, json, shlex, re
from typing import Any, Dict, List, Tuple
from utils.text_overlay import make_segment_srt, SUB_STYLE_CAPCUT

UA = "Mozilla/5.0 (compatible; RenderBot/1.0)"
DEFAULT_SUB_STYLE = SUB_STYLE_CAPCUT  # style CapCut

def _run(cmd: str, logger: logging.Logger, req_id: str):
    logger.info(f"[{req_id}] CMD: {cmd}")
    p = subprocess.run(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    logger.info(f"[{req_id}] STDERR: {p.stdout}")
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")

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
    info = _ffprobe_json(path)
    fm = (info.get("format",{}) or {}).get("format_name","") or ""
    has_video = any((s or {}).get("codec_type") == "video" for s in info.get("streams",[]))
    is_gif = ("gif" in fm.lower()) or path.lower().endswith(".gif")
    return has_video, is_gif

def _download(url: str, dst_noext: str, logger: logging.Logger, req_id: str) -> str:
    os.makedirs(os.path.dirname(dst_noext), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.pinterest.com/"})
    with urllib.request.urlopen(req, timeout=45) as r:
        ct = (r.info().get_content_type() or "").lower()
        if "gif" in ct: ext = ".gif"
        elif "mp4" in ct or "video/" in ct: ext = ".mp4"
        else:
            low = url.lower()
            if ".gif" in low: ext = ".gif"
            elif ".mp4" in low: ext = ".mp4"
            else: ext = ".bin"
        dst = dst_noext + ext
        with open(dst, "wb") as f: shutil.copyfileobj(r, f)
    if os.path.getsize(dst) <= 0:
        raise RuntimeError("downloaded file is empty")
    if dst.endswith(".bin"):
        head = open(dst, "rb").read(512).lower()
        if b"<html" in head or b"<!doctype html" in head:
            raise RuntimeError("Downloaded file is not media (got HTML). Lien Drive direct requis.")
    logger.info(f"[{req_id}] download ok -> {dst}")
    return dst

def _encode_uniform(src: str, dst: str, width: int, height: int, fps: int, need_dur: float,
                    logger: logging.Logger, req_id: str,
                    subs_path: str = None, sub_style: str = DEFAULT_SUB_STYLE):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}")
    if subs_path:
        vf = f"{vf},subtitles={shlex.quote(subs_path)}:force_style='{sub_style}'"

    src_low = src.lower()
    is_m3u8 = (src_low.startswith("http") and ".m3u8" in src_low)
    if is_m3u8:
        in_flags = ('-protocol_whitelist "file,http,https,tcp,tls,crypto" '
                    f'-t {need_dur:.3f} -i {shlex.quote(src)}')
    elif src_low.endswith(".gif"):
        in_flags = f'-ignore_loop 0 -t {need_dur:.3f} -i {shlex.quote(src)}'
    else:
        in_flags = f'-stream_loop -1 -t {need_dur:.3f} -i {shlex.quote(src)}'

    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"{in_flags} "
        f'-vf "{vf}" -pix_fmt yuv420p -r {fps} -vsync cfr '
        "-c:v libx264 -preset superfast -crf 26 "
        "-movflags +faststart -video_track_timescale 90000 "
        f"{shlex.quote(dst)}"
    )
    _run(cmd, logger, req_id)

def _concat_copy_strict(parts: List[str], out_path: str, logger: logging.Logger, req_id: str) -> str:
    list_path = out_path + ".txt"
    with open(list_path, "w") as f:
        for p in parts: f.write(f"file '{os.path.abspath(p)}'\n")
    cmd = ("ffmpeg -y -hide_banner -loglevel error "
           f"-f concat -safe 0 -i {shlex.quote(list_path)} "
           "-fflags +genpts -avoid_negative_ts make_zero -c copy -movflags +faststart "
           f"{shlex.quote(out_path)}")
    try:
        _run(cmd, logger, req_id); return "concat_copy"
    except Exception:
        inputs = " ".join(f"-i {shlex.quote(p)}" for p in parts)
        n = len(parts); maps = "".join(f"[{i}:v:0]" for i in range(n))
        cmd2 = (f"ffmpeg -y -hide_banner -loglevel error {inputs} "
                f"-filter_complex \"{maps}concat=n={n}:v=1:a=0[v]\" "
                "-map \"[v]\" -c:v libx264 -preset superfast -crf 26 "
                "-pix_fmt yuv420p -movflags +faststart -r 30 "
                "-video_track_timescale 90000 "
                f"{shlex.quote(out_path)}")
        _run(cmd2, logger, req_id); return "concat_filter"

def _mux_final(video_path: str,
               voice_path: str,
               out_path: str,
               logger: logging.Logger,
               req_id: str,
               music_path: str = None,
               music_delay: int = 0,
               music_volume: float = 0.25):
    """
    Mux final en une seule passe :
      - sans musique : map vidéo + voix
      - avec musique : [music] adelay+volume -> [bg]; [voice][bg] amix -> [a]; map vidéo + [a]
    On copie la vidéo (pas de ré-encodage) et on encode l'audio en AAC (mp4-friendly).
    """
    if not music_path:
        cmd = ("ffmpeg -y -hide_banner -loglevel error "
               f"-i {shlex.quote(video_path)} -i {shlex.quote(voice_path)} "
               "-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 128k "
               "-shortest -movflags +faststart "
               f"{shlex.quote(out_path)}")
        _run(cmd, logger, req_id)
        return

    delay_ms = max(0, int(music_delay) * 1000)
    adl = f"{delay_ms}|{delay_ms}"
    # Pipeline fusion.py-like + voix séparée :
    # 0:v = vidéo concat, 1:a = voix, 2:a = musique
    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"-i {shlex.quote(video_path)} -i {shlex.quote(voice_path)} -i {shlex.quote(music_path)} "
        f"-filter_complex \"[2:a]adelay={adl},volume={music_volume}[bg];"
        "[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2[a]\" "
        "-map 0:v:0 -map \"[a]\" -c:v copy -c:a aac -b:a 192k "
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
    sub_style: str = DEFAULT_SUB_STYLE,
    # paramètres tolérés en entrée
    global_srt: str = None,
    burn_mode: str = None,
    # musique BG optionnelle
    music_path: str = None,
    music_delay: int = 0,
    music_volume: float = 0.25,
    **kwargs
):
    """
    burn_mode:
      - "segment" (défaut) : sous-titres par segment (style CapCut)
      - "none"             : pas de sous-titres gravés

    music_path : chemin local MP3/Audio à mixer
    music_delay: décalage (sec) détecté via nom "xxx@55.mp3" côté main.py
    music_volume: volume relatif musique (0.0-1.0)
    """
    mode_burn = (burn_mode or "segment").lower().strip()
    burn_segments = (mode_burn != "none")

    parts: List[str] = []
    has_seg_times = burn_segments and any((seg.get("subtitles") for seg in plan))
    t_running = 0.0

    for i, seg in enumerate(plan):
        url = seg.get("gif_url") or seg.get("url") or seg.get("video_url")
        if not url:
            raise ValueError(f"plan[{i}] missing url/gif_url")
        try:
            dur = float(seg.get("duration") or 0.0)
        except Exception:
            dur = 0.0
        if dur <= 0.0:
            dur = 0.5
        start = float(seg.get("start_time")) if seg.get("start_time") is not None else t_running
        txt = (seg.get("text") or "").strip()
        logger.info(f"[{req_id}] seg#{i} start={start:.3f} dur={dur:.3f} url={url}")

        # source
        if url.lower().startswith("http") and ".m3u8" in url.lower():
            src_for_encode = url
        else:
            base = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}")
            src_for_encode = _download(url, base, logger, req_id)
            has_video, is_gif = _kind(src_for_encode)
            if not (has_video or is_gif):
                raise RuntimeError("Downloaded file is not media (got HTML). Lien Drive direct requis.")

        # SRT segment (si fenêtres && burn actif)
        seg_srt = None
        if has_seg_times:
            seg_srt = os.path.join(temp_dir, f"seg_{i:03d}.srt")
            make_segment_srt(seg.get("subtitles"), txt, start, dur, seg_srt)

        # encode uniforme (+ burn éventuel)
        part_path = os.path.join(temp_dir, f"part_{i:03d}.mp4")
        _encode_uniform(
            src_for_encode, part_path, width, height, fps, dur, logger, req_id,
            subs_path=seg_srt, sub_style=sub_style
        )
        parts.append(part_path)
        if seg.get("start_time") is None:
            t_running += dur

    if not parts:
        raise ValueError("empty parts")

    # concat (copy)
    video_only = os.path.join(temp_dir, "_video.mp4")
    concat_mode = _concat_copy_strict(parts, video_only, logger, req_id)

    # mux final (voix seule ou voix + musique)
    out_path = os.path.join(temp_dir, output_name)
    _mux_final(
        video_path=video_only,
        voice_path=audio_path,
        out_path=out_path,
        logger=logger,
        req_id=req_id,
        music_path=music_path,
        music_delay=music_delay,
        music_volume=music_volume
    )

    debug = {
        "mode": concat_mode,
        "subs": ("burned_per_segment" if has_seg_times else ("none" if not burn_segments else "no_times")),
        "items": len(parts),
        "burn_mode": mode_burn,
        "music": bool(music_path),
        "music_delay": int(music_delay) if music_path else 0,
        "music_volume": float(music_volume) if music_path else 0.0,
    }
    return out_path, debug
