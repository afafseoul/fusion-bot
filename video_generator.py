# video_generator.py — réencodage UNIFORME par segment (codec/résolution/FPS/timescale)
import os, time, math, shutil, subprocess, logging, urllib.request, json, shlex
from typing import Any, Dict, List, Tuple

UA = "Mozilla/5.0 (compatible; RenderBot/1.0)"

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

def _download(url: str, dst: str, logger: logging.Logger, req_id: str):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
    if os.path.getsize(dst) <= 0:
        raise RuntimeError("downloaded file is empty")
    logger.info(f"[{req_id}] download ok -> {dst} size={os.path.getsize(dst)}B")

def _encode_uniform(src: str, dst: str, width: int, height: int, fps: int, need_dur: float,
                    logger: logging.Logger, req_id: str):
    """
    Ré-encode chaque segment avec:
      - libx264 + yuv420p
      - scale+pad -> WxH
      - fps constant (CFR) + vsync cfr
      - timescale unifié (video_track_timescale=90000)
      - loop de la source et trim à need_dur
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps}"
    )
    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"-stream_loop -1 -t {need_dur:.3f} -i {shlex.quote(src)} "
        f"-vf \"{vf}\" -pix_fmt yuv420p -r {fps} -vsync cfr "
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
    except Exception as e:
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

        src_path = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}.mp4")
        _download(url, src_path, logger, req_id)

        part_path = os.path.join(temp_dir, f"part_{i:03d}.mp4")
        _encode_uniform(src=src_path, dst=part_path, width=width, height=height, fps=fps,
                        need_dur=dur, logger=logger, req_id=req_id)
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
