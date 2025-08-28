# video_generator.py — réencodage UNIFORME + sous-titres (SRT fourni ou auto depuis le plan)
import os, time, shutil, subprocess, logging, urllib.request, json, shlex
from typing import Any, Dict, List, Tuple

UA = "Mozilla/5.0 (compatible; RenderBot/1.0)"

DEFAULT_SUB_STYLE = (
    "FontName=DejaVu Sans,"
    "Fontsize=40,"
    "PrimaryColour=&H00FFFFFF&,"   # blanc
    "OutlineColour=&H00000000&,"   # noir
    "BorderStyle=3,Outline=2,Shadow=0,"
    "Alignment=2,"                 # bas-centre
    "MarginV=60"
)

# ------------------- utils ffmpeg -------------------
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
    """(has_video, is_gif) pour un fichier local"""
    info = _ffprobe_json(path)
    fm = (info.get("format",{}) or {}).get("format_name","") or ""
    has_video = any((s or {}).get("codec_type") == "video" for s in info.get("streams",[]) )
    is_gif = ("gif" in fm.lower())
    return has_video, is_gif

# ------------------- SRT helpers -------------------
def _fmt_srt_time(sec: float) -> str:
    if sec < 0: sec = 0.0
    ms = int(round(sec * 1000.0))
    hh = ms // 3_600_000
    ms %= 3_600_000
    mm = ms // 60_000
    ms %= 60_000
    ss = ms // 1000
    ms %= 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"

def _build_srt_from_plan(plan: List[Dict[str, Any]]) -> str:
    """Construit un SRT à partir des champs du plan:
       - si 'start_time' existe -> utilisé, sinon cumul des 'duration'
       - 'text' = contenu affiché
    """
    lines: List[str] = []
    t = 0.0
    idx = 1
    for seg in plan:
        txt = str(seg.get("text") or "").strip()
        if not txt:
            # rien à sous-titrer pour ce segment
            dur = float(seg.get("duration") or 0.0)
            t += max(0.0, dur) if seg.get("start_time") is None else 0.0
            continue

        dur = float(seg.get("duration") or 0.0)
        start = float(seg.get("start_time")) if seg.get("start_time") is not None else t
        end = max(start + max(0.0, dur), start + 0.2)

        start_s = _fmt_srt_time(start)
        end_s   = _fmt_srt_time(end)
        # éclater les \n éventuels sans fioritures
        block = f"{idx}\n{start_s} --> {end_s}\n{txt}\n"
        lines.append(block)

        idx += 1
        if seg.get("start_time") is None:
            t = end
    return "\n".join(lines).strip() + ("\n" if lines else "")

# ------------------- download -------------------
def _download(url: str, dst_noext: str, logger: logging.Logger, req_id: str) -> str:
    """
    Télécharge URL (Drive/Giphy/etc.). Retourne le chemin final AVEC la bonne extension.
    Si Content-Type=gif -> .gif ; mp4 -> .mp4 ; sinon -> tente via l’URL, sinon .bin.
    """
    os.makedirs(os.path.dirname(dst_noext), exist_ok=True)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.pinterest.com/"
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        ct = (r.info().get_content_type() or "").lower()
        if "gif" in ct:
            ext = ".gif"
        elif "mp4" in ct or "video/" in ct:
            ext = ".mp4"
        else:
            low = url.lower()
            if ".gif" in low:   ext = ".gif"
            elif ".mp4" in low: ext = ".mp4"
            else:               ext = ".bin"
        dst = dst_noext + ext
        with open(dst, "wb") as f:
            shutil.copyfileobj(r, f)

    size = os.path.getsize(dst)
    if size <= 0:
        raise RuntimeError("downloaded file is empty")
    logger.info(f"[{req_id}] download ok -> {dst} size={size}B ct={ct}")

    # Sécu: si .bin ou si ça ressemble à de l'HTML, on lève une erreur claire.
    if dst.endswith(".bin"):
        try:
            head = open(dst, "rb").read(512).lower()
        except Exception:
            head = b""
        if b"<html" in head or b"<!doctype html" in head:
            raise RuntimeError(
                "Downloaded file is not a valid media (got HTML). "
                "Assure le partage public et/ou utilise l’URL directe (drive.usercontent...)."
            )

    return dst

# ------------------- encode / concat / mux -------------------
def _encode_uniform(src: str, dst: str, width: int, height: int, fps: int, need_dur: float,
                    logger: logging.Logger, req_id: str):
    """
    Uniformise chaque segment en H.264 WxH@fps + yuv420p.
    - GIF local  : -ignore_loop 0 (pas de -stream_loop)
    - MP4 local  : -stream_loop -1 (boucle) + -t need_dur
    - M3U8 (URL) : lecture réseau directe + -t need_dur
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
          f"fps={fps}")

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

def _mux_audio(video_path: str, audio_path: str, out_path: str, logger: logging.Logger, req_id: str,
               srt_path: str = None, sub_style: str = DEFAULT_SUB_STYLE):
    """
    - Sans SRT : on copy la vidéo et on encode l’audio → ultra rapide.
    - Avec SRT : on BRÛLE les sous-titres (libass) pendant le mux → un seul passage final.
    """
    if srt_path:
        vf = f"subtitles={shlex.quote(srt_path)}:force_style='{sub_style}'"
        cmd = (
            "ffmpeg -y -hide_banner -loglevel error "
            f"-i {shlex.quote(video_path)} -i {shlex.quote(audio_path)} "
            f"-filter_complex \"[0:v]{vf}[v]\" "
            "-map \"[v]\" -map 1:a:0 "
            "-c:v libx264 -preset superfast -crf 26 -pix_fmt yuv420p "
            "-c:a aac -b:a 128k -shortest -movflags +faststart "
            f"{shlex.quote(out_path)}"
        )
    else:
        cmd = (
            "ffmpeg -y -hide_banner -loglevel error "
            f"-i {shlex.quote(video_path)} -i {shlex.quote(audio_path)} "
            "-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 128k "
            "-shortest -movflags +faststart "
            f"{shlex.quote(out_path)}"
        )
    _run(cmd, logger, req_id)

# ------------------- pipeline -------------------
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
    global_srt: str = None,
    burn_subs: bool = True,
    sub_style: str = DEFAULT_SUB_STYLE,
):
    parts: List[str] = []

    # 1) SRT (priorité: global fourni, sinon auto depuis le plan si on a des 'text')
    srt_path = None
    srt_text = (global_srt or "").strip()
    if not srt_text:
        if any((seg.get("text") or "").strip() for seg in plan):
            srt_text = _build_srt_from_plan(plan)
    if srt_text:
        srt_path = os.path.join(temp_dir, "captions.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_text)
        logger.info(f"[{req_id}] SRT prepared -> {srt_path} ({len(srt_text)} chars)")

    # 2) Segments
    for i, seg in enumerate(plan):
        url = seg.get("gif_url") or seg.get("url") or seg.get("video_url")
        if not url:
            raise ValueError(f"plan[{i}] missing 'gif_url' or 'url'")
        try:
            dur = float(seg.get("duration") or 0.0)
        except Exception:
            dur = 0.0
        if dur <= 0.0:
            dur = 0.5

        logger.info(f"[{req_id}] seg#{i} url={url} dur_req={dur:.3f}")

        # .m3u8: on lit en réseau (pas de download)
        if url.lower().startswith("http") and ".m3u8" in url.lower():
            src_for_encode = url
        else:
            base = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}")
            src_for_encode = _download(url, base, logger, req_id)
            has_video, is_gif = _kind(src_for_encode)
            if not (has_video or is_gif):
                raise RuntimeError(
                    "Downloaded file is not a valid media (got HTML). "
                    "Assure le partage public et/ou utilise l’URL directe (drive.usercontent...)."
                )

        part_path = os.path.join(temp_dir, f"part_{i:03d}.mp4")
        _encode_uniform(src=src_for_encode, dst=part_path, width=width, height=height, fps=fps,
                        need_dur=dur, logger=logger, req_id=req_id)
        parts.append(part_path)

    if not parts:
        raise ValueError("empty parts")

    # 3) Concat vidéo
    video_only = os.path.join(temp_dir, "_video.mp4")
    mode = _concat_copy_strict(parts, video_only, logger, req_id)

    # 4) Mux audio (+/- burn subs)
    out_path = os.path.join(temp_dir, output_name)
    _mux_audio(
        video_only, audio_path, out_path, logger, req_id,
        srt_path=(srt_path if (srt_path and burn_subs) else None),
        sub_style=sub_style or DEFAULT_SUB_STYLE
    )

    debug = {
        "status": "ok",
        "items": len(parts),
        "mode": mode,
        "burned_subs": bool(srt_path and burn_subs),
        "srt_emitted": (bool(srt_path) and not burn_subs),
        "notes": "uniformize segments -> concat copy -> mux audio (+/- burn subs via libass)"
    }
    if srt_path and not burn_subs:
        debug["srt_path"] = srt_path

    return out_path, debug
