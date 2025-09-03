# video_generator.py — plan "ancien" + pré-check préencodage + NO reformat si déjà OK
import os, time, shutil, subprocess, logging, urllib.request, json, shlex, re
from typing import Any, Dict, List, Tuple
from utils.text_overlay import make_segment_srt, SUB_STYLE_CAPCUT

UA = "Mozilla/5.0 (compatible; RenderBot/1.0)"
DEFAULT_SUB_STYLE = SUB_STYLE_CAPCUT

FFMPEG_THREADS = os.getenv("FFMPEG_THREADS", "").strip()
def _with_threads(cmd: str) -> str:
    return f"{cmd} -threads {FFMPEG_THREADS}" if FFMPEG_THREADS else cmd

# ---- Style "philo" (conservé mais tu utilises style=default) ----
SUB_STYLE_PHILO = (
    "Fontname=Arial,Fontsize=42,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00202020,Outline=3,Shadow=0,BorderStyle=1,"
    "Alignment=2,MarginL=60,MarginR=60,MarginV=96,Spacing=0,ScaleX=100,ScaleY=100"
)
STYLE_SUB_MAP = {"philo": SUB_STYLE_PHILO, "default": DEFAULT_SUB_STYLE, "capcut": DEFAULT_SUB_STYLE}

# =========================================================
#                       utils ffmpeg
# =========================================================
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

def _fps_of_stream(s: dict) -> float:
    for key in ("avg_frame_rate","r_frame_rate"):
        fr = s.get(key) or ""
        if isinstance(fr, str) and "/" in fr:
            try:
                a,b = fr.split("/"); a=float(a); b=float(b) or 1.0
                return a/b
            except Exception: pass
        try:
            return float(fr)
        except Exception: pass
    return 0.0

def _probe_props(path: str) -> Dict[str, Any]:
    info = _ffprobe_json(path)
    v = next((s for s in info.get("streams",[]) if s.get("codec_type")=="video"), {}) or {}
    dur = 0.0
    try: dur = float((info.get("format",{}) or {}).get("duration", 0.0) or 0.0)
    except Exception: dur = 0.0
    return {
        "codec": v.get("codec_name"),
        "pix_fmt": v.get("pix_fmt"),
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "fps": _fps_of_stream(v),
        "duration": max(0.0, dur),
    }

def _format_ok_for_target(props: Dict[str, Any], width: int, height: int, fps: int) -> bool:
    fps_ok = abs((props.get("fps") or 0) - fps) <= 0.5  # tolère 29.97
    return (
        props.get("codec") == "h264" and
        props.get("pix_fmt") == "yuv420p" and
        int(props.get("width") or 0) == int(width) and
        int(props.get("height") or 0) == int(height) and
        fps_ok
    )

def _kind(path: str) -> Tuple[bool, bool]:
    info = _ffprobe_json(path)
    fm = (info.get("format",{}) or {}).get("format_name","") or ""
    has_video = any((s or {}).get("codec_type") == "video" for s in info.get("streams",[]))
    is_gif = ("gif" in fm.lower()) or path.lower().endswith(".gif")
    return has_video, is_gif

# =========================================================
#                       download simple
# =========================================================
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

# =========================================================
#          chemin "STRICT PREENCODÉ" (zéro reformat)
# =========================================================
def _trim_copy(src: str, t: float, dst: str, logger, req_id: str):
    # coupe en copy (rapide, sans ré-encodage)
    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"-t {t:.3f} -i {shlex.quote(src)} "
        "-c copy -movflags +faststart "
        f"{shlex.quote(dst)}"
    )
    _run(_with_threads(cmd), logger, req_id)

def _loop_copy_noencode(src: str, need_dur: float, dst: str, logger, req_id: str, src_dur: float):
    """
    Répète le même fichier en concat COPY pour atteindre need_dur,
    avec une dernière coupe COPY pour le reliquat.
    """
    base = os.path.dirname(dst)
    lst = os.path.join(base, os.path.basename(dst) + ".list.txt")
    files: List[str] = []

    if src_dur <= 0.05:
        # éviter boucle infinie si média quasi vide
        _trim_copy(src, need_dur, dst, logger, req_id)
        return

    n_full = int(need_dur // src_dur)
    rem = max(0.0, need_dur - n_full * src_dur)

    # fichiers complets
    for i in range(n_full):
        files.append(src)

    # reliquat (coupe copy)
    tmp_rem = None
    if rem > 1e-3:
        tmp_rem = os.path.join(base, f"_rem_{int(rem*1000)}.mp4")
        _trim_copy(src, rem, tmp_rem, logger, req_id)
        files.append(tmp_rem)

    # filelist
    with open(lst, "w") as f:
        for p in files:
            f.write(f"file '{os.path.abspath(p)}'\n")

    # concat copy (génère timestamps propres)
    cmd = (
        "ffmpeg -y -hide_banner -loglevel error "
        f"-f concat -safe 0 -i {shlex.quote(lst)} "
        "-fflags +genpts -avoid_negative_ts make_zero "
        "-c copy -movflags +faststart "
        f"{shlex.quote(dst)}"
    )
    _run(_with_threads(cmd), logger, req_id)

    try:
        if tmp_rem and os.path.exists(tmp_rem): os.remove(tmp_rem)
        if os.path.exists(lst): os.remove(lst)
    except Exception:
        pass

def _encode_preencoded_copy_or_loop(
    src: str, dst: str, width: int, height: int, fps: int, need_dur: float,
    logger: logging.Logger, req_id: str
):
    props = _probe_props(src)
    logger.info(f"[{req_id}] precheck {src} codec={props.get('codec')} pix_fmt={props.get('pix_fmt')} "
                f"res={props.get('width')}x{props.get('height')} fps~{props.get('fps'):.3f} dur={props.get('duration'):.3f}")

    if not _format_ok_for_target(props, width, height, fps):
        raise RuntimeError("Mauvais format, malgré pré-encodage")

    src_dur = float(props.get("duration") or 0.0)
    if need_dur <= src_dur + 1e-3:
        _trim_copy(src, need_dur, dst, logger, req_id)
        logger.info(f"[{req_id}] ✅ copy-trim (no reformat) -> {dst}")
    else:
        _loop_copy_noencode(src, need_dur, dst, logger, req_id, src_dur)
        logger.info(f"[{req_id}] ✅ loop+concat (no reformat) -> {dst}")

# =========================================================
#       ancien encode (utilisé si subs/effets → ré-encode)
# =========================================================
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

def _encode_uniform_old(
    src: str, dst: str, width: int, height: int, fps: int, need_dur: float,
    logger: logging.Logger, req_id: str,
    subs_path: str = None, sub_style: str = DEFAULT_SUB_STYLE,
    style_key: str = "default"
):
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
    _run(_with_threads(cmd), logger, req_id)

# =========================================================
#            concat vidéo + audio/mix (comme avant)
# =========================================================
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
        # fallback filtre (rare)
        inputs = " ".join(f"-i {shlex.quote(p)}" for p in parts)
        n = len(parts); maps = "".join(f"[{i}:v:0]" for i in range(n))
        cmd2 = (f"ffmpeg -y -hide_banner -loglevel error {inputs} "
                f"-filter_complex \"{maps}concat=n={n}:v=1:a=0[v]\" "
                "-map \"[v]\" -c:v libx264 -preset superfast -crf 26 "
                "-pix_fmt yuv420p -movflags +faststart -r 30 "
                "-video_track_timescale 90000 "
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

# ---- SRT mot par mot (on garde pour plus tard) ----
def _sec_to_ts(t: float) -> str:
    t = max(0.0, float(t)); ms = int(round(t*1000))
    h = ms//3600000; ms-=h*3600000
    m = ms//60000;   ms-=m*60000
    s = ms//1000;    ms-=s*1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _make_word_srt(text: str, dur: float, out_path: str, mode: str = "accumulate"):
    txt = (text or "").strip()
    if not txt or dur <= 0:
        open(out_path,"w",encoding="utf-8").write(""); return
    words = re.findall(r"\S+", txt)
    if not words:
        open(out_path,"w",encoding="utf-8").write(""); return
    n = len(words); step = max(0.08, dur/n); t = 0.0; blocks=[]
    for i,_w in enumerate(words):
        t1=t; t2=min(dur, t+step)
        payload = (" ".join(words[:i+1]) if mode=="accumulate" else _w)
        blocks.append(f"{i+1}\n{_sec_to_ts(t1)} --> {_sec_to_ts(t2)}\n{payload}\n"); t=t2
    with open(out_path,"w",encoding="utf-8") as f: f.write("\n".join(blocks))

# =========================================================
#                    GENERATE VIDEO (plan)
# =========================================================
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
    style: str = "default",
    subtitle_mode: str = "sentence",
    word_mode: str = "accumulate",
    global_srt: str = None,
    burn_mode: str = None,
    music_path: str = None,
    music_delay: int = 0,
    music_volume: float = 0.25,
    **kwargs
):
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
        if not url: raise ValueError(f"plan[{i}] missing url/gif_url")
        try: dur = float(seg.get("duration") or 0.0)
        except Exception: dur = 0.0
        if dur <= 0.0: dur = 0.5
        start = float(seg.get("start_time")) if seg.get("start_time") is not None else t_running
        txt = (seg.get("text") or "").strip()
        logger.info(f"[{req_id}] seg#{i} start={start:.3f} dur={dur:.3f} url={url}")

        # source
        if url.lower().startswith("http") and ".m3u8" in url.lower():
            src_for_encode = url  # rare
        else:
            base = os.path.join(temp_dir, f"src_{int(time.time()*1000)}_{i}")
            src_for_encode = _download(url, base, logger, req_id)
            has_video, is_gif = _kind(src_for_encode)
            if not (has_video or is_gif):
                raise RuntimeError("Downloaded file is not media (got HTML). Lien Drive direct requis.")

        # SRT segment (désactivé si burn_mode='none')
        seg_srt = None
        if burn_segments:
            if subtitle_mode.lower().strip() == "word" and txt:
                seg_srt = os.path.join(temp_dir, f"seg_{i:03d}.srt")
                _make_word_srt(txt, dur, seg_srt, mode=(word_mode or "accumulate").lower().strip())
            elif has_seg_times:
                seg_srt = os.path.join(temp_dir, f"seg_{i:03d}.srt")
                make_segment_srt(seg.get("subtitles"), txt, start, dur, seg_srt)

        # production du morceau
        part_path = os.path.join(temp_dir, f"part_{i:03d}.mp4")

        # >>> chemin "préencodé strict" = aucun reformat si pas de subs/effets
        if not seg_srt and style_key == "default":
            _encode_preencoded_copy_or_loop(
                src_for_encode, part_path, width, height, fps, dur, logger, req_id
            )
        else:
            # cas avec sous-titres/effets → ancien encode
            _encode_uniform_old(
                src_for_encode, part_path, width, height, fps, dur,
                logger, req_id, subs_path=seg_srt, sub_style=sub_style, style_key=style_key
            )

        parts.append(part_path)
        if seg.get("start_time") is None:
            t_running += dur

    if not parts:
        raise ValueError("empty parts")

    # concat vidéo (copy) puis audio/mix
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
        "subs": ("burned_per_segment" if has_seg_times or subtitle_mode.lower()=="word"
                 else ("none" if not burn_segments else "no_times")),
        "items": len(parts),
        "burn_mode": mode_burn,
        "style": style_key,
        "subtitle_mode": subtitle_mode,
        "word_mode": word_mode,
        "music": bool(music_path),
        "music_start_at": int(music_delay) if music_path else 0,
        "music_volume": float(music_volume) if music_path else 0.0,
        "strict_preencoded_path": (not burn_segments and style_key=="default"),
    }
    return out_path, debug
