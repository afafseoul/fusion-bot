# video_generator.py
import os, time, shutil, subprocess, logging, urllib.request
from typing import Any, Dict, List, Tuple

def _run(cmd: List[str], logger: logging.Logger, req_id: str):
    logger.info(f"[{req_id}] CMD: {' '.join(map(str, cmd))}")
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "ignore")
    logger.info(f"[{req_id}] STDERR: {out}")
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
    req = urllib.request.Request(url, headers={"User-Agent": "curl/7"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
    size = os.path.getsize(dst)
    logger.info(f"[{req_id}] download ok -> {dst} size={size}B")

def _loop_trim_canvas_encode(
    src: str,
    need_dur: float,
    workdir: str,
    idx: int,
    width: int,
    height: int,
    fps: int,
    logger: logging.Logger,
    req_id: str
) -> str:
    """
    1) boucle la source à l'infini (-stream_loop -1) et coupe à need_dur
    2) scale pour rentrer dans le canevas width x height (sans déformer)
    3) pad en noir pour remplir le canevas
    4) encode en H.264 ultrafast/yuv420p (segments homogènes) -> concat copy
    """
    out = os.path.join(workdir, f"part_{idx:03d}.mp4")

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",         # répète la vidéo source
        "-t", f"{need_dur:.3f}",      # coupe pile à la durée demandée
        "-i", src,
        "-vf", vf,                    # fit + bandes noires
        "-pix_fmt", "yuv420p",
        "-an",
        "-c:v", "libx264",
        "-preset", os.getenv("X264_PRESET", "ultrafast"),
        "-crf",    os.getenv("X264_CRF", "23"),
        "-r",      str(fps),          # unifie le timebase -> concat copy fiable
        "-movflags", "+faststart",
        out
    ]
    _run(cmd, logger, req_id)
    return out

def generate_video(
    plan: List[Dict[str, Any]],
    audio_path: str,
    output_name: str,
    temp_dir: str,
    width: int,      # utilisé pour le canevas (ex: 1080)
    height: int,     # utilisé pour le canevas (ex: 1920)
    fps: int,        # utilisé pour uniformiser (ex: 30)
    logger: logging.Logger,
    req_id: str,
    global_srt: str = None  # ignoré (pas de sous-titres pour l’instant)
) -> Tuple[str, Dict[str, Any]]:

    parts: List[str] = []
    for i, seg in enumerate(plan):
        url = seg.get("gif_url") or seg.get("url")
        if not url:
            raise ValueError(f"plan[{i}] missing 'gif_url' or 'url'")
        dur_req = float(seg.get("duration") or 0.0)
        logger.info(f"[{req_id}] seg#{i} url={url} dur_req={dur_req:.3f}")
        if dur_req <= 0:
            continue

        src_path = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}.mp4")
        _download(url, src_path, logger, req_id)

        part_path = _loop_trim_canvas_encode(
            src=src_path,
            need_dur=dur_req,
            workdir=temp_dir,
            idx=i,
            width=width,
            height=height,
            fps=fps,
            logger=logger,
            req_id=req_id
        )
        parts.append(part_path)

    # Liste pour concat (chemins absolus)
    list_all = os.path.join(temp_dir, "list_all.txt")
    with open(list_all, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")

    out_path = os.path.join(temp_dir, output_name)

    # Concat 100% copy (vidéo homogénéisée), audio en AAC
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", list_all,  # 0:v
        "-i", audio_path,                               # 1:a
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest", "-movflags", "+faststart",
        out_path
    ]
    _run(cmd, logger, req_id)

    debug = {
        "status": "ok",
        "items": len(parts),
        "mode": "per_part_ultrafast_encode_then_concat_copy",
        "notes": f"canvas={width}x{height}, fps={fps}, no subtitles"
    }
    return out_path, debug
