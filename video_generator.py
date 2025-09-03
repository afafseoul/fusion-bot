# video_generator.py — coupe par plan (ré-encodage temporel) + SRT par segment,
# avec garde-fou "pré-encodé strict" (aucune correction de format).
#
# Si strict_preencoded=True : on VÉRIFIE (codec/pix_fmt/résolution/FPS).
# -> Si un segment n'est PAS conforme => RuntimeError("Mauvais format, malgré pré-encode")
# -> Sinon on encode le segment pour respecter exactement la durée (comme l'ancien code).

import os, time, shutil, subprocess, logging, urllib.request, json, shlex, re, io
from typing import Any, Dict, List, Tuple

from utils.text_overlay import make_segment_srt, SUB_STYLE_CAPCUT

# (Fallback Google Drive si le webContentLink renvoie du HTML)
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

UA = "Mozilla/5.0 (compatible; RenderBot/1.0)"
DEFAULT_SUB_STYLE = SUB_STYLE_CAPCUT
FFMPEG_THREADS = os.getenv("FFMPEG_THREADS", "").strip()
GOOGLE_CREDS_PATH = os.getenv("GOOGLE_CREDS", "/etc/secrets/credentials.json")

# ---- Styles de sous-titres ----
SUB_FALLBACK_FONT = os.getenv("SUB_FONT_FALLBACK", "DejaVuSans").strip() or "DejaVuSans"
SUB_STYLE_PHILO = (
    f"Fontname={SUB_FALLBACK_FONT},Fontsize=42,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00202020,Outline=3,Shadow=0,BorderStyle=1,"
    "Alignment=2,MarginL=60,MarginR=60,MarginV=96,Spacing=0,ScaleX=100,ScaleY=100"
)
STYLE_SUB_MAP = {"philo": SUB_STYLE_PHILO, "default": DEFAULT_SUB_STYLE, "capcut": DEFAULT_SUB_STYLE}

# -------------------- utils ffmpeg --------------------
def _run(cmd: str, logger: logging.Logger, req_id: str):
    if FFMPEG_THREADS:
        cmd = f"{cmd} -threads {FFMPEG_THREADS}"
    logger.info(f"[{req_id}] CMD: {cmd}")
    p = subprocess.run(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    logger.info(f"[{req_id}] STDERR: {p.stdout}")
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")

def _ffprobe_json(path: str) -> dict:
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-print_format","json","-show_format","-show_streams", path],
            stderr=subprocess.STDOUT, timeout=10
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

def _probe_video_props(path: str) -> Dict[str, Any]:
    """Retourne codec, pix_fmt, width, height, fps (float) pour la première piste vidéo."""
    info = _ffprobe_json(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {}) or {}
    codec = v.get("codec_name")
    pix_fmt = v.get("pix_fmt")
    w, h = v.get("width"), v.get("height")
    # FPS depuis avg_frame_rate ou r_frame_rate
    fr = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/0"
    fps = 0.0
    try:
        num, den = fr.split("/")
        num = float(num); den = float(den)
        fps = num/den if den else 0.0
    except Exception:
        fps = 0.0
    return {"codec": codec, "pix_fmt": pix_fmt, "width": int(w or 0), "height": int(h or 0), "fps": float(fps)}

def _is_good_preencoded_mp4(path: str, required_fps: int, logger: logging.Logger, req_id: str) -> bool:
    """Exigences strictes pour dire qu'un fichier est 'pré-encodé' OK."""
    p = _probe_video_props(path)
    logger.info(f"[{req_id}] precheck {path} codec={p['codec']} pix_fmt={p['pix_fmt']} "
                f"res={p['width']}x{p['height']} fps~{p['fps']:.3f}")
    # codec/pix_fmt
    if p["codec"] != "h264" or p["pix_fmt"] != "yuv420p":
        return False
    # résolution stricte verticale 1080x1920 (tu peux assouplir si besoin)
    if not (p["width"] == 1080 and p["height"] == 1920):
        return False
    # fps tolérance (ex 29.97 vs 30)
    if required_fps:
        if abs(p["fps"] - float(required_fps)) > 1.0:
            return False
    return True

# -------------------- Google Drive fallback --------------------
def _gdrive_service():
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=scopes)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def _extract_drive_id(url: str) -> str | None:
    m = re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", url)
    if m: return m.group(1)
    m = re.search(r"/file/d/([A-Za-z0-9_-]{10,})", url)
    if m: return m.group(1)
    return None

def _drive_download_by_id(file_id: str, dst_path: str, logger: logging.Logger, req_id: str) -> Tuple[str,str]:
    svc = _gdrive_service()
    meta = svc.files().get(fileId=file_id, fields="id,name,mimeType", supportsAllDrives=True).execute()
    mime = meta.get("mimeType") or ""
    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dst_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, req)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
    logger.info(f"[{req_id}] drive downloaded -> {dst_path} (mime={mime})")
    return dst_path, mime

# -------------------- download --------------------
def _download(url: str, dst_noext: str, logger: logging.Logger, req_id: str) -> str:
    """
    1) Essaie HTTP direct (webContentLink etc.)
    2) Si HTML/non-media, tente fallback Google Drive API via id=...
    """
    os.makedirs(os.path.dirname(dst_noext), exist_ok=True)
    # 1) HTTP direct
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.pinterest.com/"})
        with urllib.request.urlopen(req, timeout=45) as r:
            ct = (r.info().get_content_type() or "").lower()
            if "gif" in ct: ext = ".gif"
            elif "mp4" in ct or ct.startswith("video/"): ext = ".mp4"
            else:
                low = url.lower()
                if ".gif" in low: ext = ".gif"
                elif ".mp4" in low: ext = ".mp4"
                else: ext = ".bin"
            dst = dst_noext + ext
            with open(dst, "wb") as f:
                shutil.copyfileobj(r, f)
        if os.path.getsize(dst) <= 0:
            raise RuntimeError("downloaded file is empty")
        if dst.endswith(".bin"):
            head = open(dst, "rb").read(512).lower()
            if b"<html" in head or b"<!doctype html" in head:
                raise ValueError("HTTP returned non-media")
        logger.info(f"[{req_id}] downloaded (HTTP) -> {dst}")
        return dst
    except Exception as e:
        logger.info(f"[{req_id}] HTTP returned non-media; falling back to Drive API for id in url if any ({e})")

    # 2) Fallback Drive
    file_id = _extract_drive_id(url)
    if not file_id:
        raise RuntimeError("Downloaded file is not media (got HTML) and no Drive id found in URL.")
    tmp_mp4 = dst_noext + ".mp4"
    path, mime = _drive_download_by_id(file_id, tmp_mp4, logger, req_id)
    if "gif" in (mime or "").lower():
        new = dst_noext + ".gif"
        os.replace(path, new); path = new
    return path

# -------------------- encodage (coupe) --------------------
def _vf_for_style(width: int, height: int, fps: int, style_key: str) -> str:
    sk = (style_key or "default").lower().strip()
    if sk != "philo":
        return f"scale={width}:{height}:force_original_aspect_ratio=decrease," \
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}"
    inner = int(min(width, height) * 0.78)
    return (
        f"scale={inner}:{inner}:force_original_aspect_ratio=decrease,"
        f"pad={inner}:{inner}:(ow-iw)/2:(oh-ih)/2:black,"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps}"
    )

def _encode_uniform(src: str, dst: str, width: int, height: int, fps: int, need_dur: float,
                    logger: logging.Logger, req_id: str,
                    subs_path: str = None, sub_style: str = DEFAULT_SUB_STYLE,
                    style_key: str = "default"):
    """
    On ré-encode toujours le segment pour garantir la durée EXACTE (comme l'ancien code).
    Si GIF -> -ignore_loop ; sinon -> -stream_loop -1 pour boucler, + -t pour couper.
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    vf = _vf_for_style(width, height, fps, style_key)
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

# -------------------- concat + audio --------------------
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

def _mux_audio(video_path: str, audio_path: str, out_path: str, logger: logging.Logger, req_id: str):
    cmd = ("ffmpeg -y -hide_banner -loglevel error "
           f"-i {shlex.quote(video_path)} -i {shlex.quote(audio_path)} "
           "-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 128k "
           "-shortest -movflags +faststart "
           f"{shlex.quote(out_path)}")
    _run(cmd, logger, req_id)

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
    _run(cmd, logger, req_id)

# -------------------- SRT mot par mot --------------------
def _sec_to_ts(t: float) -> str:
    t = max(0.0, float(t))
    ms = int(round(t * 1000))
    h = ms // 3600000; ms -= h*3600000
    m = ms // 60000;   ms -= m*60000
    s = ms // 1000;    ms -= s*1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _make_word_srt(text: str, dur: float, out_path: str, mode: str = "accumulate"):
    txt = (text or "").strip()
    if not txt or dur <= 0:
        open(out_path, "w", encoding="utf-8").write(""); return
    words = re.findall(r"\S+", txt)
    if not words:
        open(out_path, "w", encoding="utf-8").write(""); return
    n = len(words); step = max(0.08, dur / n)
    t = 0.0; blocks = []
    for i, w in enumerate(words):
        t1, t2 = t, min(dur, t + step)
        payload = (" ".join(words[:i+1]) if mode == "accumulate" else w)
        blocks.append(f"{i+1}\n{_sec_to_ts(t1)} --> {_sec_to_ts(t2)}\n{payload}\n")
        t = t2
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))

# -------------------- generate_video --------------------
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
    style: str = "default",             # 'default' | 'philo'
    subtitle_mode: str = "sentence",    # 'sentence' | 'word'
    word_mode: str = "accumulate",      # 'accumulate' | 'replace'
    global_srt: str = None,
    burn_mode: str = None,
    music_path: str = None,
    music_delay: int = 0,
    music_volume: float = 0.25,
    strict_preencoded: bool = True,     # 🔒 actif pour l'async
    **kwargs
):
    """
    - Gardien pré-encodage (strict_preencoded=True) : vérifie MP4/H.264/yuv420p/1080x1920/FPS≈fps.
      Sinon -> RuntimeError("Mauvais format, malgré pré-encode")
    - Ensuite, on encode CHAQUE segment pour respecter sa durée (comme l'ancien code).
    """
    style_key = (style or "default").lower().strip()
    if style_key in STYLE_SUB_MAP:
        sub_style = STYLE_SUB_MAP[style_key]

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
            # en strict, on refuse
            if strict_preencoded:
                raise RuntimeError("Mauvais format, malgré pré-encode")
            src_for_encode = url
        else:
            base = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}")
            src_for_encode = _download(url, base, logger, req_id)
            has_video, is_gif = _kind(src_for_encode)
            if not has_video:
                raise RuntimeError("Downloaded file is not media (got HTML). Lien Drive direct requis.")

            # 🔒 Vérification stricte (aucune correction de format)
            if strict_preencoded:
                if is_gif or (not src_for_encode.lower().endswith(".mp4")):
                    raise RuntimeError("Mauvais format, malgré pré-encode")
                if not _is_good_preencoded_mp4(src_for_encode, required_fps=fps, logger=logger, req_id=req_id):
                    raise RuntimeError("Mauvais format, malgré pré-encode")

        # SRT segment (si demandé)
        seg_srt = None
        if burn_segments:
            if subtitle_mode.lower().strip() == "word" and txt:
                seg_srt = os.path.join(temp_dir, f"seg_{i:03d}.srt")
                _make_word_srt(txt, dur, seg_srt, mode=(word_mode or "accumulate").lower().strip())
            elif has_seg_times:
                seg_srt = os.path.join(temp_dir, f"seg_{i:03d}.srt")
                make_segment_srt(seg.get("subtitles"), txt, start, dur, seg_srt)

        # Encodage du segment (garantit la durée exacte) — comme AVANT
        part_path = os.path.join(temp_dir, f"part_{i:03d}.mp4")
        _encode_uniform(
            src_for_encode, part_path, width, height, fps, dur,
            logger, req_id, subs_path=seg_srt, sub_style=sub_style, style_key=style_key
        )
        parts.append(part_path)

        if seg.get("start_time") is None:
            t_running += dur

    if not parts:
        raise ValueError("empty parts")

    # Concat vidéo
    video_only = os.path.join(temp_dir, "_video.mp4")
    concat_mode = _concat_copy_strict(parts, video_only, logger, req_id)

    # Mix audio (voix + musique optionnelle)
    audio_for_mux = audio_path
    if music_path:
        mixed = os.path.join(temp_dir, "voice_mix.m4a")
        _mix_voice_with_music(
            voice_path=audio_path,
            music_path=music_path,
            start_at_sec=int(music_delay),
            out_audio_path=mixed,
            logger=logger, req_id=req_id,
            music_volume=float(music_volume),
        )
        audio_for_mux = mixed

    out_path = os.path.join(temp_dir, output_name)
    _mux_audio(video_only, audio_for_mux, out_path, logger, req_id)

    debug = {
        "mode": concat_mode,
        "subs": ("burned_per_segment" if has_seg_times or subtitle_mode.lower() == "word"
                 else ("none" if not burn_segments else "no_times")),
        "items": len(parts),
        "burn_mode": (burn_mode or "segment"),
        "style": style_key,
        "subtitle_mode": subtitle_mode,
        "word_mode": word_mode,
        "music": bool(music_path),
        "music_start_at": int(music_delay) if music_path else 0,
        "music_volume": float(music_volume) if music_path else 0.0,
        "strict_preencoded": bool(strict_preencoded),
    }
    return out_path, debug
