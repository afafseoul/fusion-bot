# video_generator.py
import os, math, time, shutil, subprocess, logging, urllib.request
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

def _loop_and_trim(src: str, need_dur: float, workdir: str, idx: int,
                   logger: logging.Logger, req_id: str) -> str:
    # Boucle en COPY jusqu’à dépasser la durée, puis coupe en COPY
    src_dur = _ffprobe_duration(src)
    if src_dur <= 0.05:
        reps = max(1, math.ceil((need_dur + 0.2) / 0.5))
    else:
        reps = max(1, math.ceil((need_dur + 0.2) / src_dur))

    seg_list = os.path.join(workdir, f"seg_{idx:03d}.txt")
    with open(seg_list, "w") as f:
        for _ in range(reps):
            f.write(f"file '{src}'\n")

    loop_path = os.path.join(workdir, f"loop_{idx:03d}.mp4")
    _run(["ffmpeg","-y","-f","concat","-safe","0","-i", seg_list, "-c","copy", loop_path], logger, req_id)

    part_path = os.path.join(workdir, f"part_{idx:03d}.mp4")
    _run([
        "ffmpeg","-y",
        "-t", f"{need_dur:.3f}",
        "-i", loop_path,
        "-c","copy",
        "-avoid_negative_ts","make_zero",
        part_path
    ], logger, req_id)

    return part_path

def generate_video(
    plan: List[Dict[str, Any]],
    audio_path: str,
    output_name: str,
    temp_dir: str,
    width: int,      # ignorés volontairement (pas de resize)
    height: int,     # ignorés volontairement (pas de resize)
    fps: int,        # ignoré (pas de FPS forcé)
    logger: logging.Logger,
    req_id: str,
    global_srt: str = None  # ignoré (pas de sous-titres)
) -> Tuple[str, Dict[str, Any]]:

    parts: List[str] = []
    for i, seg in enumerate(plan):
        url = seg.get("gif_url") or seg.get("url")
        if not url:
            raise ValueError(f"plan[{i}] missing 'gif_url' or 'url'")
        dur_req = float(seg.get("duration") or 0.0)
        logger.info(f"[{req_id}] seg#{i} url={url} dur_req={dur_req:.3f}")

        src_path = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}.mp4")
        _download(url, src_path, logger, req_id)

        part_path = _loop_and_trim(src_path, dur_req, temp_dir, i, logger, req_id)
        parts.append(part_path)

    list_all = os.path.join(temp_dir, "list_all.txt")
    with open(list_all, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")

    out_path = os.path.join(temp_dir, output_name)

    # Tentative 1 : concat 100% en COPY pour la vidéo (ultra-rapide),
    # on n'encode que l'audio en AAC (mp4 friendly).
    try:
        cmd = [
            "ffmpeg","-y",
            "-f","concat","-safe","0","-i", list_all,   # 0:v (collage)
            "-i", audio_path,                            # 1:a
            "-map","0:v:0","-map","1:a:0",
            "-c:v","copy",                               # pas d'encodage vidéo
            "-c:a","aac",                                # audio -> AAC (rapide)
            "-shortest","-movflags","+faststart",
            out_path
        ]
        _run(cmd, logger, req_id)
        mode = "copy_video"
    except Exception as e:
        # Fallback si COPY échoue (paramètres codecs différents) :
        # réencodage vidéo en une seule passe, preset ultrafast.
        logger.warning(f"[{req_id}] fast concat failed, fallback ultrafast reencode: {e}")
        cmd = [
            "ffmpeg","-y",
            "-f","concat","-safe","0","-i", list_all,
            "-i", audio_path,
            "-map","0:v:0","-map","1:a:0",
            "-c:v","libx264","-preset", os.getenv("X264_PRESET","ultrafast"),
            "-crf", os.getenv("X264_CRF","23"),
            "-c:a","aac",
            "-shortest","-movflags","+faststart",
            out_path
        ]
        _run(cmd, logger, req_id)
        mode = "reencode_fallback"

    debug = {
        "status": "ok",
        "items": len(parts),
        "mode": mode,
        "notes": "no resize, no fps, no subtitles"
    }
    return out_path, debug
