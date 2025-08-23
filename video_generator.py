# video_generator.py — SIMPLE "TEL QUEL" (no resize, no fps, no video reencode)
import os, math, time, shutil, subprocess, logging, urllib.request, json
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
    logger.info(f"[{req_id}] download ok -> {dst} size={os.path.getsize(dst)}B")

def _loop_and_trim_copy(src: str, need_dur: float, workdir: str, idx: int,
                        logger: logging.Logger, req_id: str) -> str:
    """
    Répète la source en COPY jusqu'à couvrir need_dur, puis coupe en COPY.
    Aucun filtre, aucun -r, aucun resize.
    """
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

def _concat_mp4_copy(parts: List[str], out_path: str, logger: logging.Logger, req_id: str):
    lst = out_path + ".txt"
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    _run([
        "ffmpeg","-y",
        "-f","concat","-safe","0","-i", lst,
        "-c","copy","-movflags","+faststart",
        out_path
    ], logger, req_id)

def _concat_ts_copy(parts: List[str], out_path: str, logger: logging.Logger, req_id: str):
    """
    Fallback sans réencodage vidéo :
    1) remux chaque MP4 en TS (annexb) en copy
    2) concat 'concat:ts1|ts2|...' en copy
    3) remux en MP4 en copy
    """
    ts_paths = []
    for i, p in enumerate(parts):
        t = p.rsplit(".",1)[0] + ".ts"
        _run(["ffmpeg","-y","-i", p, "-c","copy", "-bsf:v","h264_mp4toannexb", "-f","mpegts", t], logger, req_id)
        ts_paths.append(t)
    joined = "concat:" + "|".join(ts_paths)
    tmp_mp4 = out_path + ".video.mp4"
    _run(["ffmpeg","-y","-i", joined, "-c","copy", "-movflags","+faststart", tmp_mp4], logger, req_id)
    shutil.move(tmp_mp4, out_path)

def generate_video(
    plan: List[Dict[str, Any]],
    audio_path: str,
    output_name: str,
    temp_dir: str,
    width: int,      # ignorés
    height: int,     # ignorés
    fps: int,        # ignoré
    logger: logging.Logger,
    req_id: str,
    global_srt: str = None  # ignoré
):
    parts: List[str] = []
    for i, seg in enumerate(plan):
        url = seg.get("gif_url") or seg.get("url")
        if not url:
            raise ValueError(f"plan[{i}] missing 'gif_url' or 'url'")
        dur = float(seg.get("duration") or 0.0)
        if dur <= 0:  # si pas de durée -> on prend le clip tel quel
            dur = _ffprobe_duration(url) or 0.001
        logger.info(f"[{req_id}] seg#{i} url={url} dur_req={dur:.3f}")

        src_path = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}.mp4")
        _download(url, src_path, logger, req_id)

        part_path = _loop_and_trim_copy(src_path, dur, temp_dir, i, logger, req_id)
        parts.append(part_path)

    if not parts:
        raise ValueError("empty parts")

    # 1) Concat vidéo en COPY (MP4). Si échec (paramètres différents), fallback TS.
    video_only = os.path.join(temp_dir, "_video.mp4")
    try:
        _concat_mp4_copy(parts, video_only, logger, req_id)
        mode = "concat_mp4_copy"
    except Exception as e:
        logger.warning(f"[{req_id}] MP4 copy concat failed, trying TS copy concat: {e}")
        _concat_ts_copy(parts, video_only, logger, req_id)
        mode = "concat_ts_copy"

    # 2) Mux audio (audio -> AAC), vidéo en COPY.
    out_path = os.path.join(temp_dir, output_name)
    _run([
        "ffmpeg","-y",
        "-i", video_only,
        "-i", audio_path,
        "-map","0:v:0","-map","1:a:0",
        "-c:v","copy",
        "-c:a","aac",
        "-shortest","-movflags","+faststart",
        out_path
    ], logger, req_id)

    debug = {"status":"ok","items":len(parts),"mode":mode,"notes":"no resize, no fps, no video reencode; audio->AAC"}
    return out_path, debug
