# video_generator.py — ré-encodage COMPLET dans le job (scale + fps + style), sans sous-titres
import os, time, shutil, subprocess, logging, urllib.request, json, shlex
from typing import Any, Dict, List, Tuple
from styles import vf_for_style_full

# On force par défaut un seul thread pour rester safe sur le plan Free
FFMPEG_THREADS = os.getenv("FFMPEG_THREADS", "1").strip()
FFMPEG_FILTER_THREADS = os.getenv("FFMPEG_FILTER_THREADS", "1").strip()

def _with_threads(cmd: str) -> str:
    extra = []
    if FFMPEG_FILTER_THREADS:
        extra.append(f"-filter_threads {FFMPEG_FILTER_THREADS} -filter_complex_threads {FFMPEG_FILTER_THREADS}")
    if FFMPEG_THREADS:
        extra.append(f"-threads {FFMPEG_THREADS}")
    return f"{cmd} {' '.join(extra)}".strip()

UA = "Mozilla/5.0 (compatible; RenderBot/1.0)"

# -------------------- utils ffmpeg --------------------
def _run(cmd: str, logger: logging.Logger, req_id: str):
    logger.info(f"[{req_id}] CMD: {cmd}")
    p = subprocess.run(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    logger.info(f"[{req_id}] STDERR: {p.stdout}")
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")

def _ffprobe_json(path: str) -> dict:
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-print_format","json","-show_format","-show_streams", path],
            stderr=subprocess.STDOUT, timeout=12
        )
        return json.loads(out.decode("utf-8","ignore"))
    except Exception:
        return {}

def _kind(path: str) -> Tuple[bool, bool]:
    info = _ffprobe_json(path)
    fm = (info.get("format",{}) or {}).get("format_name","") or ""
    has_video = any((s or {}).get("codec_type") == "video" for s in info.get("streams",[]))
    is_gif = ("gif" in fm.lower()) or path.lower().endswith(".gif")
    return has_video, is_gif

# -------------------- download --------------------
def _download(url: str, dst_noext: str, logger: logging.Logger, req_id: str) -> str:
    os.makedirs(os.path.dirname(dst_noext), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
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
    logger.info(f"[{req_id}] downloaded (HTTP) -> {dst}")
    return dst

# ---------------- encode FULL (scale + fps + style) --------------
def _encode_full(
    src: str, dst: str, width: int, height: int, fps: int, need_dur: float,
    logger: logging.Logger, req_id: str, style_key: str
):
    vf = vf_for_style_full(width, height, fps, style_key)

    # entrées: on duplique si besoin via stream_loop/ignore_loop
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
        f'-vf "{vf}" '
        "-pix_fmt yuv420p "
        "-c:v libx264 -preset superfast -crf 26 "
        "-an "
        "-movflags +faststart -video_track_timescale 90000 "
        f"{shlex.quote(dst)}"
    )
    _run(_with_threads(cmd), logger, req_id)

# ---------------- concat + audio --------------------
def _concat_copy_strict(parts: List[str], out_path: str, logger: logging.Logger, req_id: str) -> str:
    list_path = out_path + ".txt"
    with open(list_path, "w") as f:
        for p in parts: f.write(f"file '{os.path.abspath(p)}'\n")
    cmd = ("ffmpeg -y -hide_banner -loglevel error "
           f"-f concat -safe 0 -i {shlex.quote(list_path)} "
           "-fflags +genpts -avoid_negative_ts make_zero "
           "-c copy -movflags +faststart "
           f"{shlex.quote(out_path)}")
    try:
        _run(_with_threads(cmd), logger, req_id); return "concat_copy"
    except Exception:
        inputs = " ".join(f"-i {shlex.quote(p)}" for p in parts)
        n = len(parts); maps = "".join(f"[{i}:v:0]" for i in range(n))
        cmd2 = (f"ffmpeg -y -hide_banner -loglevel error {inputs} "
                f"-filter_complex \"{maps}concat=n={n}:v=1:a=0[v]\" "
                "-map \"[v]\" -c:v libx264 -preset superfast -crf 26 "
                "-pix_fmt yuv420p -movflags +faststart "
                f"{shlex.quote(out_path)}")
        _run(_with_threads(cmd2), logger, req_id); return "concat_filter"

def _mux_audio(video_path: str, audio_path: str, out_path: str, logger: logging.Logger, req_id: str):
    cmd = ("ffmpeg -y -hide_banner -loglevel error "
           f"-i {shlex.quote(video_path)} -i {shlex.quote(audio_path)} "
           "-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k "
           "-shortest -movflags +faststart "
           f"{shlex.quote(out_path)}")
    _run(_with_threads(cmd), logger, req_id)

def _mix_voice_with_music(voice_path: str, music_path: str, start_at_sec: int,
                          out_audio_path: str, logger: logging.Logger, req_id: str,
                          music_volume: float = 0.25):
    start_at = max(0, int(start_at_sec))
    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"-i {shlex.quote(voice_path)} "
        f"-ss {start_at} -i {shlex.quote(music_path)} "
        f"-filter_complex \"[1:a]volume={music_volume}[bg];"
        "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2,aresample=async=1[a]\" "
        "-map \"[a]\" -c:a aac -b:a 192k "
        f"{shlex.quote(out_audio_path)}"
    )
    _run(_with_threads(cmd), logger, req_id)

# ---------------- moteur principal -------------------
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
    style: str = "default",
    music_path: str = None,
    music_delay: int = 0,
    music_volume: float = 0.25,
    **kwargs
):
    style_key = (style or "default").lower().strip()

    parts: List[str] = []
    t_running = 0.0

    for i, seg in enumerate(plan):
        url = seg.get("gif_url") or seg.get("url") or seg.get("video_url")
        if not url: raise ValueError(f"plan[{i}] missing url/gif_url")
        try: dur = float(seg.get("duration") or 0.0)
        except Exception: dur = 0.0
        if dur <= 0.0: dur = 0.5
        start = float(seg.get("start_time")) if seg.get("start_time") is not None else t_running
        logger.info(f"[{req_id}] seg#{i} start={start:.3f} dur={dur:.3f} url={url}")

        # source locale
        if url.lower().startswith("http") and ".m3u8" in url.lower():
            src_for_encode = url
        else:
            base = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}")
            src_for_encode = _download(url, base, logger, req_id)
            has_video, is_gif = _kind(src_for_encode)
            if not (has_video or is_gif):
                raise RuntimeError("Downloaded file is not media (got HTML). Lien Drive direct requis.")

        # encodage complet du morceau
        part_path = os.path.join(temp_dir, f"part_{i:03d}.mp4")
        _encode_full(
            src_for_encode, part_path, width, height, fps, dur,
            logger, req_id, style_key=style_key
        )

        parts.append(part_path)
        if seg.get("start_time") is None:
            t_running += dur

    if not parts:
        raise ValueError("empty parts")

    # concat vidéo (copy si possible) puis audio/mix
    video_only = os.path.join(temp_dir, "_video.mp4")
    concat_mode = _concat_copy_strict(parts, video_only, logger, req_id)

    audio_for_mux = audio_path
    if music_path:
        mixed = os.path.join(temp_dir, "voice_mix.m4a")
        _mix_voice_with_music(
            voice_path=audio_path, music_path=music_path,
            start_at_sec=int(music_delay), out_audio_path=mixed,
            logger=logger, req_id=req_id, music_volume=float(music_volume),
        )
        audio_for_mux = mixed

    out_path = os.path.join(temp_dir, output_name)
    _mux_audio(video_only, audio_for_mux, out_path, logger, req_id)

    debug = {
        "mode": concat_mode,
        "items": len(parts),
        "style": style_key,
        "width": width, "height": height, "fps": fps,
        "music": bool(music_path),
        "music_start_at": int(music_delay) if music_path else 0,
        "music_volume": float(music_volume) if music_path else 0.0,
    }
    return out_path, debug
