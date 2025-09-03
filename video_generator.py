# video_generator.py — PREENCODE STRICT (no re-encode) + ancien pipeline pour le reste
import os, time, shutil, subprocess, logging, urllib.request, json, shlex, re
from typing import Any, Dict, List, Tuple
from utils.text_overlay import make_segment_srt, SUB_STYLE_CAPCUT

UA = "Mozilla/5.0 (compatible; RenderBot/1.0)"
DEFAULT_SUB_STYLE = SUB_STYLE_CAPCUT  # style CapCut

# ---- Style "philo" (on ne l'utilise pas si burn_subs=0, mais on garde la compat) ----
SUB_STYLE_PHILO = (
    "Fontname=Arial,Fontsize=42,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00202020,Outline=3,Shadow=0,BorderStyle=1,"
    "Alignment=2,MarginL=60,MarginR=60,MarginV=96,Spacing=0,ScaleX=100,ScaleY=100"
)
STYLE_SUB_MAP = {"philo": SUB_STYLE_PHILO, "default": DEFAULT_SUB_STYLE, "capcut": DEFAULT_SUB_STYLE}

# -------------------- utils shell/ffprobe --------------------
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

def _fps_from_stream(s: dict) -> float:
    fr = s.get("avg_frame_rate") or s.get("r_frame_rate") or ""
    if isinstance(fr, str) and "/" in fr:
        a,b = fr.split("/",1)
        try:
            a = float(a); b = float(b); 
            return 0.0 if b == 0 else a/b
        except Exception:
            return 0.0
    try:
        return float(fr) if fr else 0.0
    except Exception:
        return 0.0

def _probe_video_props(path: str) -> Dict[str, Any]:
    info = _ffprobe_json(path)
    out = {
        "has_video": False, "is_gif": (path.lower().endswith(".gif")),
        "codec": None, "pix_fmt": None, "w": 0, "h": 0, "fps": 0.0, "duration": 0.0
    }
    fmt = info.get("format") or {}
    try:
        out["duration"] = float(fmt.get("duration") or 0.0)
    except Exception:
        out["duration"] = 0.0
    for s in info.get("streams", []):
        if (s or {}).get("codec_type") == "video":
            out["has_video"] = True
            out["codec"]   = s.get("codec_name")
            out["pix_fmt"] = s.get("pix_fmt")
            out["w"]       = int(s.get("width") or 0)
            out["h"]       = int(s.get("height") or 0)
            out["fps"]     = _fps_from_stream(s)
            break
    # gif heuristique
    fm = (fmt.get("format_name","") or "").lower()
    if "gif" in fm:
        out["is_gif"] = True
    return out

def _kind(path: str) -> Tuple[bool, bool]:
    p = _probe_video_props(path)
    return p["has_video"], p["is_gif"]

# -------------------- download --------------------
def _download(url: str, dst_noext: str, logger: logging.Logger, req_id: str) -> str:
    """
    Télécharge tel quel (webContentLink Drive OK).
    Si la réponse est HTML -> on lève une erreur (il faut un lien direct).
    """
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
    # anti HTML
    head = open(dst, "rb").read(512).lower()
    if b"<html" in head or b"<!doctype html" in head:
        raise RuntimeError("Downloaded file is not media (got HTML). Lien Drive direct requis.")
    logger.info(f"[{req_id}] downloaded (HTTP) -> {dst}")
    return dst

# -------------------- PREENCODE STRICT CHECKS --------------------
def _is_good_preencoded_mp4(path: str, want_w: int, want_h: int, want_fps: int,
                            logger: logging.Logger, req_id: str) -> Tuple[bool, str, Dict[str, Any]]:
    props = _probe_video_props(path)
    logger.info(f"[{req_id}] precheck {path} codec={props['codec']} pix_fmt={props['pix_fmt']} "
                f"res={props['w']}x{props['h']} fps~{props['fps']:.3f} dur={props['duration']:.3f}")
    # tolérances très faibles (fps à ±0.05)
    reasons = []
    if not props["has_video"]: reasons.append("no_video_stream")
    if props["codec"] != "h264": reasons.append(f"codec={props['codec']}")
    if props["pix_fmt"] != "yuv420p": reasons.append(f"pix_fmt={props['pix_fmt']}")
    if props["w"] != want_w or props["h"] != want_h: reasons.append(f"res={props['w']}x{props['h']}")
    if abs(props["fps"] - float(want_fps)) > 0.05: reasons.append(f"fps={props['fps']:.3f}")
    return (len(reasons) == 0), (", ".join(reasons) if reasons else ""), props

def _copy_trim(src: str, dst: str, need_dur: float, logger: logging.Logger, req_id: str):
    """
    Coupe sans ré-encoder (stream copy) pour respecter la durée demandée.
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    # -t après l'entrée (output option) pour limiter — c'est précis et rapide
    cmd = ("ffmpeg -y -hide_banner -loglevel error "
           f"-i {shlex.quote(src)} -t {need_dur:.3f} "
           "-c copy -movflags +faststart -avoid_negative_ts make_zero "
           f"{shlex.quote(dst)}")
    _run(cmd, logger, req_id)
    logger.info(f"[{req_id}] ✅ trim copy -> {dst}")

# -------------------- ENCODE (ancien comportement) --------------------
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
        # ancien comportement: on boucle la source si besoin
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

# -------------------- concat / audio (inchangé) --------------------
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
           "-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k "
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

# ---- SRT mot par mot (local au segment 0..dur) ----
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
    n = len(words)
    step = max(0.08, dur / n)
    t = 0.0
    blocks = []
    for i, _w in enumerate(words):
        t1 = t
        t2 = min(dur, t + step)
        payload = (" ".join(words[:i+1]) if mode == "accumulate" else _w)
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
    **kwargs
):
    """
    Règle: si pas de sous-titres gravés ET style=default -> PREENCODE STRICT (stream copy only).
    Sinon -> ancien pipeline d'encodage (comme avant).
    """
    style_key = (style or "default").lower().strip()
    if style_key in STYLE_SUB_MAP:
        sub_style = STYLE_SUB_MAP[style_key]

    mode_burn = (burn_mode or "segment").lower().strip()
    burn_segments = (mode_burn != "none")

    # ⇩⇩ PREENCODE STRICT si VRAIMENT rien à graver
    preencode_strict = (not burn_segments) and (style_key == "default")

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

        # SRT segment (uniquement si burn actif)
        seg_srt = None
        if burn_segments:
            if subtitle_mode.lower().strip() == "word" and txt:
                seg_srt = os.path.join(temp_dir, f"seg_{i:03d}.srt")
                _make_word_srt(txt, dur, seg_srt, mode=(word_mode or "accumulate").lower().strip())
            elif has_seg_times:
                seg_srt = os.path.join(temp_dir, f"seg_{i:03d}.srt")
                make_segment_srt(seg.get("subtitles"), txt, start, dur, seg_srt)

        # --- ENCODAGE / COPIE ---
        part_path = os.path.join(temp_dir, f"part_{i:03d}.mp4")
        if preencode_strict and not seg_srt and src_for_encode.lower().endswith(".mp4"):
            ok, why, props = _is_good_preencoded_mp4(src_for_encode, width, height, fps, logger, req_id)
            if not ok:
                raise RuntimeError(f"Mauvais format, malgré pré-encodage: {why}")
            # Vérifie que la durée demandée tient dans la source (sinon on refuse)
            # (tolérance +50 ms)
            if dur > (props.get("duration") or 0.0) + 0.05:
                raise RuntimeError(f"Mauvais format, malgré pré-encodage: dur_source={props.get('duration'):.3f}s < dur_demande={dur:.3f}s")
            _copy_trim(src_for_encode, part_path, dur, logger, req_id)
        else:
            # ancien pipeline
            _encode_uniform(
                src_for_encode, part_path, width, height, fps, dur,
                logger, req_id,
                subs_path=seg_srt, sub_style=sub_style,
                style_key=style_key
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
        mixed = os.path.join(temp_dir, "voice_mix.m4a")
        _mix_voice_with_music(
            voice_path=audio_path,
            music_path=music_path,
            start_at_sec=int(music_delay),
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
        "items": len(parts),
        "burn_mode": mode_burn,
        "style": style_key,
        "subtitle_mode": subtitle_mode,
        "word_mode": word_mode,
        "music": bool(music_path),
        "music_start_at": int(music_delay) if music_path else 0,
        "music_volume": float(music_volume) if music_path else 0.0,
        "preencode_strict": bool(preencode_strict),
    }
    return out_path, debug
