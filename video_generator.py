# video_generator.py  — MODE "COPY ONLY" STRICT
import os, math, time, shutil, subprocess, logging, urllib.request, json
from typing import Any, Dict, List, Tuple

def _run(cmd: List[str], logger: logging.Logger, req_id: str):
    logger.info(f"[{req_id}] CMD: {' '.join(map(str, cmd))}")
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "ignore")
    logger.info(f"[{req_id}] STDERR: {out}")
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")

def _ffprobe_json(path: str) -> Dict[str, Any]:
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-print_format","json","-show_streams","-show_format", path],
            stderr=subprocess.STDOUT
        ).decode("utf-8","ignore")
        return json.loads(out)
    except Exception:
        return {}

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

def _ffprobe_video_fingerprint(path: str) -> Tuple[str, int, int, str, str]:
    """
    Empreinte minimale pour concat 'copy':
    (codec_name, width, height, pix_fmt, sample_aspect_ratio)
    """
    info = _ffprobe_json(path)
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return (
                str(s.get("codec_name") or ""),
                int(s.get("width") or 0),
                int(s.get("height") or 0),
                str(s.get("pix_fmt") or ""),
                str(s.get("sample_aspect_ratio") or "0:1"),
            )
    return ("", 0, 0, "", "0:1")

def _download(url: str, dst: str, logger: logging.Logger, req_id: str):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/7"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
    size = os.path.getsize(dst)
    logger.info(f"[{req_id}] download ok -> {dst} size={size}B")

def _loop_and_trim_copy(src: str, need_dur: float, workdir: str, idx: int,
                        logger: logging.Logger, req_id: str) -> str:
    """
    Répète la source en COPY jusqu'à dépasser need_dur, puis coupe en COPY.
    ZERO réencodage ici.
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
    # Coupe à la durée demandée, en copy, timestamps > 0
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
    width: int,      # ignorés
    height: int,     # ignorés
    fps: int,        # ignoré
    logger: logging.Logger,
    req_id: str,
    global_srt: str = None  # ignoré
):
    parts: List[str] = []
    prints: List[Tuple[str,int,int,str,str]] = []
    durs:   List[float] = []

    # 1) Téléchargement + boucle/coupe (TOUJOURS en COPY)
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

        part_path = _loop_and_trim_copy(src_path, dur_req, temp_dir, i, logger, req_id)
        parts.append(part_path)
        durs.append(_ffprobe_duration(part_path))
        prints.append(_ffprobe_video_fingerprint(part_path))

    if not parts:
        raise ValueError("no valid parts")

    # 2) Vérif stricte: concat COPY possible ?
    ref = prints[0]
    mismatches = []
    for i, fp in enumerate(prints):
        if fp != ref:
            mismatches.append((i, fp))
    if mismatches:
        # On ne réencode PAS => on stoppe avec un message clair.
        human = lambda fp: f"{fp[0]} {fp[1]}x{fp[2]} {fp[3]} SAR={fp[4]}"
        details = " | ".join([f"part#{i}: {human(fp)}" for i, fp in mismatches])
        ref_h = human(ref)
        raise RuntimeError(
            "COPY ONLY impossible: paramètres vidéo différents.\n"
            f"Référence: {ref_h}\n"
            f"Différents: {details}\n"
            "Solution sans réencodage: utiliser uniquement des GIF MP4 aux mêmes dimensions/format."
        )

    # 3) Liste concat avec hints 'duration' (fiabilise les PTS)
    list_all = os.path.join(temp_dir, "list_all.txt")
    with open(list_all, "w") as f:
        for idx, p in enumerate(parts):
            # Pour concat demuxer: on peut donner 'duration' AVANT le dernier 'file'.
            if idx < len(parts) - 1:
                f.write(f"file '{p}'\n")
                f.write(f"duration {durs[idx]:.6f}\n")
            else:
                f.write(f"file '{p}'\n")

    out_path = os.path.join(temp_dir, output_name)

    # 4) Concat finale 100% COPY, audio en AAC (mp4 friendly)
    #    - timescale explicite pour éviter les warnings DTS
    cmd = [
        "ffmpeg","-y",
        "-f","concat","-safe","0","-i", list_all,
        "-i", audio_path,
        "-map","0:v:0","-map","1:a:0",
        "-c:v","copy",
        "-c:a","aac",
        "-video_track_timescale","15360",
        "-shortest","-movflags","+faststart",
        out_path
    ]
    _run(cmd, logger, req_id)

    debug = {
        "status": "ok",
        "items": len(parts),
        "mode": "STRICT_COPY_ONLY",
        "fingerprint": {
            "codec": ref[0], "width": ref[1], "height": ref[2],
            "pix_fmt": ref[3], "sar": ref[4]
        },
        "notes": "no resize, no fps change, no video reencode; audio -> AAC"
    }
    return out_path, debug
