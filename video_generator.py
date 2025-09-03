# video_generator.py — encode segments + burn SRT PAR SEGMENT (style CapCut|philo)
import os, time, shutil, subprocess, logging, urllib.request, json, shlex, re
from typing import Any, Dict, List, Tuple
from utils.text_overlay import make_segment_srt, SUB_STYLE_CAPCUT

UA = "Mozilla/5.0 (compatible; RenderBot/1.0)"
DEFAULT_SUB_STYLE = SUB_STYLE_CAPCUT
SUB_FALLBACK_FONT = os.getenv("SUB_FONT_FALLBACK", "DejaVuSans").strip() or "DejaVuSans"

SUB_STYLE_PHILO = (
    f"Fontname={SUB_FALLBACK_FONT},Fontsize=42,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00202020,Outline=3,Shadow=0,BorderStyle=1,"
    "Alignment=2,MarginL=60,MarginR=60,MarginV=96,Spacing=0,ScaleX=100,ScaleY=100"
)
STYLE_SUB_MAP = {"philo": SUB_STYLE_PHILO, "default": DEFAULT_SUB_STYLE, "capcut": DEFAULT_SUB_STYLE}

FFMPEG_THREADS = os.getenv("FFMPEG_THREADS", "").strip()

# -------------------- helpers shell & probe --------------------
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

def _fps_from_stream(s: dict) -> float:
    val = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
    try:
        n, d = val.split("/")
        n = int(n); d = int(d) or 1
        return float(n)/d
    except Exception:
        return 0.0

def _is_good_mp4(path: str, logger, req_id: str) -> bool:
    """
    Tolérant (<=1080x1920). Le strict (exact 1080x1920@30 h264/yuv420p) est contrôlé en amont par /create-video-async.
    """
    info = _ffprobe_json(path)
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            codec, pix_fmt = s.get("codec_name"), s.get("pix_fmt")
            w, h = s.get("width", 0), s.get("height", 0)
            logger.info(f"[{req_id}] check {path} codec={codec} pix_fmt={pix_fmt} res={w}x{h}")
            return codec == "h264" and pix_fmt == "yuv420p" and w <= 1080 and h <= 1920
    return False

# -------------------- download & kind --------------------
def _guess_ext_from_url_or_ct(url: str, content_type: str) -> str:
    if content_type:
        ct = content_type.lower()
        if "mp4" in ct: return ".mp4"
        if "gif" in ct: return ".gif"
        if "quicktime" in ct or "mov" in ct: return ".mov"
        if "webm" in ct: return ".webm"
    u = (url or "").lower()
    for ext in (".mp4",".gif",".mov",".webm",".m4v"):
        if ext in u:
            return ext
    return ".mp4"  # fallback raisonnable

def _download(url: str, dst_noext: str, logger: logging.Logger, req_id: str) -> str:
    """
    Télécharge URL (Drive uc?id=..., webContentLink, Giphy, etc.) et renvoie le chemin final avec extension.
    """
    os.makedirs(os.path.dirname(dst_noext), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://example.com"})
    with urllib.request.urlopen(req, timeout=60) as r:
        ct = r.headers.get("Content-Type") or ""
        ext = _guess_ext_from_url_or_ct(url, ct)
        dst = dst_noext + ext
        with open(dst, "wb") as f:
            shutil.copyfileobj(r, f)
    logger.info(f"[{req_id}] downloaded -> {dst}")
    return dst

def _kind(path: str) -> Tuple[bool, bool]:
    """
    Retourne (has_video, is_gif) via ffprobe + extension.
    """
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-select_streams","v:0","-show_streams","-of","json", path],
            stderr=subprocess.STDOUT, timeout=10
        ).decode("utf-8","ignore")
        info = json.loads(out)
        has_video = any((s.get("codec_type") == "video") for s in info.get("streams", []))
    except Exception:
        has_video = False
    is_gif = path.lower().endswith(".gif")
    return has_video, is_gif

# -------------------- encode (fallback) --------------------
def _encode_uniform(src: str, dst: str, width: int, height: int, fps: int, need_dur: float,
                    logger: logging.Logger, req_id: str,
                    subs_path: str = None, sub_style: str = DEFAULT_SUB_STYLE,
                    style_key: str = "default",
                    strict: bool = False):
    """
    - Si strict=True : aucun ré-encodage autorisé (copie pure) dès lors qu'il n'y a PAS de sous-titres à graver.
      -> Si le fichier n'est pas un MP4 'bon format', on lève une erreur.
    - Si strict=False : comportement historique (ré-encode si nécessaire).
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    if src.lower().endswith(".mp4") and _is_good_mp4(src, logger, req_id) and not subs_path:
        shutil.copy2(src, dst)
        logger.info(f"[{req_id}] ✅ skip re-encode (déjà bon format) -> {dst}")
        return

    if strict and not subs_path:
        # En mode strict (async + pas de subs), on ne doit jamais réencoder.
        raise RuntimeError("Mauvais format, malgré pré-encode")

    base_vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease," \
              f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps={fps}"
    if subs_path:
        base_vf = f"{base_vf},subtitles={shlex.quote(subs_path)}:force_style='{sub_style}'"

    in_flags = f'-t {need_dur:.3f} -i {shlex.quote(src)}'
    cmd = ("ffmpeg -y -hide_banner -loglevel error "
           f"{in_flags} -vf \"{base_vf}\" -pix_fmt yuv420p -r {fps} -vsync cfr "
           "-c:v libx264 -preset superfast -crf 26 "
           "-movflags +faststart -video_track_timescale 90000 "
           f"{shlex.quote(dst)}")
    _run(cmd, logger, req_id)

# -------------------- word-by-word SRT (local) --------------------
def _fmt_ts(t: float) -> str:
    ms = int(round(t * 1000.0))
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    ms = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _make_word_srt(text: str, duration: float, srt_path: str, mode: str = "accumulate"):
    words = re.findall(r"\S+", text or "")
    if not words:
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> " + _fmt_ts(max(0.1, duration)) + "\n" + (text or "") + "\n")
        return
    step = max(duration / len(words), 0.05)
    with open(srt_path, "w", encoding="utf-8") as f:
        t0 = 0.0
        for i, w in enumerate(words):
            t1 = duration if i == len(words) - 1 else (i + 1) * step
            if mode == "replace":
                line = w
            else:
                line = " ".join(words[:i+1])
            f.write(f"{i+1}\n{_fmt_ts(t0)} --> { _fmt_ts(t1) }\n{line}\n\n")
            t0 = t1

# -------------------- concat (copy) --------------------
def _concat_copy_strict(parts: List[str], out_path: str, logger: logging.Logger, req_id: str) -> str:
    """
    Concatène des MP4 homogènes en COPY via demuxer concat.
    """
    lst = out_path + ".txt"
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"-f concat -safe 0 -i {shlex.quote(lst)} "
        "-c copy -movflags +faststart "
        f"{shlex.quote(out_path)}"
    )
    _run(cmd, logger, req_id)
    return "concat_copy"

# -------------------- mix/mux audio --------------------
def _mix_voice_with_music(voice_path: str, music_path: str, start_at_sec: int,
                          out_audio_path: str, logger: logging.Logger, req_id: str,
                          music_volume: float = 0.25):
    """
    Mixe la voix + musique (démarrage musique à start_at_sec, volume atténué).
    Sortie AAC (m4a/mp4).
    """
    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"-i {shlex.quote(voice_path)} "
        f"-ss {int(start_at_sec)} -i {shlex.quote(music_path)} "
        f"-filter_complex [1:a]volume={music_volume}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2 "
        "-c:a aac -b:a 192k "
        f"{shlex.quote(out_audio_path)}"
    )
    _run(cmd, logger, req_id)

def _mux_audio(video_path: str, audio_path: str, out_path: str,
               logger: logging.Logger, req_id: str):
    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"-i {shlex.quote(video_path)} -i {shlex.quote(audio_path)} "
        "-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k "
        "-movflags +faststart "
        f"{shlex.quote(out_path)}"
    )
    _run(cmd, logger, req_id)

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
    # NOUVEAU
    style: str = "default",             # 'default' | 'philo'
    subtitle_mode: str = "sentence",    # 'sentence' | 'word'
    word_mode: str = "accumulate",      # 'accumulate' | 'replace'
    # paramètres tolérés en entrée
    global_srt: str = None,
    burn_mode: str = None,
    # musique BG optionnelle
    music_path: str = None,
    music_delay: int = 0,       # coupe au début de la musique (ex: @55 => on démarre à 55s)
    music_volume: float = 0.25,
    strict_preencoded: bool = False,    # 🔒 si True : zéro ré-encodage autorisé (copie only)
    **kwargs
):
    """
    burn_mode:
      - "segment" (défaut) : sous-titres par segment
      - "none"             : pas de sous-titres gravés

    subtitle_mode:
      - "sentence" : EXACTEMENT comme aujourd'hui (si 'subtitles' fourni)
      - "word"     : affiche mot par mot (accumulate/replace) sur toute la durée du segment
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
            src_for_encode = url
        else:
            base = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}")
            src_for_encode = _download(url, base, logger, req_id)
            has_video, is_gif = _kind(src_for_encode)
            if not (has_video or is_gif):
                raise RuntimeError("Downloaded file is not media (got HTML). Lien Drive direct requis.")

        # SRT segment
        seg_srt = None
        if burn_segments:
            if subtitle_mode.lower().strip() == "word" and txt:
                seg_srt = os.path.join(temp_dir, f"seg_{i:03d}.srt")
                _make_word_srt(txt, dur, seg_srt, mode=(word_mode or "accumulate").lower().strip())
            elif has_seg_times:
                seg_srt = os.path.join(temp_dir, f"seg_{i:03d}.srt")
                make_segment_srt(seg.get("subtitles"), txt, start, dur, seg_srt)
            else:
                seg_srt = None  # pas de subs sans fenêtres

        # encode uniforme (+ burn éventuel)
        part_path = os.path.join(temp_dir, f"part_{i:03d}.mp4")
        _encode_uniform(
            src_for_encode, part_path, width, height, fps, dur,
            logger, req_id,
            subs_path=seg_srt, sub_style=sub_style,
            style_key=style_key,
            strict=strict_preencoded and not seg_srt  # strict seulement s'il n'y a PAS de subs à graver
        )
        parts.append(part_path)
        if seg.get("start_time") is None:
            t_running += dur

    if not parts:
        raise ValueError("empty parts")

    # concat (copy) -> préparation audio
    video_only = os.path.join(temp_dir, "_video.mp4")
    concat_mode = _concat_copy_strict(parts, video_only, logger, req_id)

    audio_for_mux = audio_path
    if music_path:
        mixed = os.path.join(temp_dir, "voice_mix.m4a")  # AAC dans conteneur m4a/mp4
        _mix_voice_with_music(
            voice_path=audio_path,
            music_path=music_path,
            start_at_sec=int(music_delay),   # commence la musique à N secondes
            out_audio_path=mixed,
            logger=logger,
            req_id=req_id,
            music_volume=float(music_volume),
        )
        audio_for_mux = mixed

    out_path = os.path.join(temp_dir, output_name)
    _mux_audio(video_only, audio_for_mux, out_path, logger, req_id)

    debug = {
        "mode": concat_mode,
        "subs": ("burned_per_segment" if has_seg_times or subtitle_mode.lower() == "word" else ("none" if not burn_segments else "no_times")),
        "items": len(parts),
        "burn_mode": mode_burn,
        "style": style_key,
        "subtitle_mode": subtitle_mode,
        "word_mode": word_mode,
        "music": bool(music_path),
        "music_start_at": int(music_delay) if music_path else 0,
        "music_volume": float(music_volume) if music_path else 0.0,
        "strict_preencoded": bool(strict_preencoded),
    }
    return out_path, debug
