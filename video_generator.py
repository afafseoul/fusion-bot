# Fast montage : télécharge MP4, ajuste la durée en -c copy, concat en -c copy,
# puis un SEUL encodage final pour graver les sous-titres + mux audio.

import os, re, time, tempfile, subprocess
from typing import List, Dict, Any, Optional
import requests

MAX_SRC_MB      = int(os.getenv("MAX_SRC_MB", "120"))
X264_PRESET     = os.getenv("X264_PRESET", "ultrafast")
CRF             = os.getenv("CRF", "28")               # plus grand = plus léger/rapide
FFMPEG_THREADS  = os.getenv("FFMPEG_THREADS", "1")
UA              = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"

# ---------- helpers ----------
def _seconds(x) -> float:
    try: return float(x)
    except: return 0.0

def normalize_giphy_url(u: str) -> str:
    if not u: return u
    if u.endswith(".mp4") and "giphy" in u: return u
    m = re.search(r"giphy\.com/(?:embed|media)/([A-Za-z0-9]+)", u)
    if m: return f"https://media.giphy.com/media/{m.group(1)}/giphy.mp4"
    m = re.search(r"giphy\.com/gifs/[^/]*-([A-Za-z0-9]+)", u)
    if m: return f"https://media.giphy.com/media/{m.group(1)}/giphy.mp4"
    m = re.search(r"/media/([A-Za-z0-9]+)/giphy\.mp4", u)
    if m: return f"https://media.giphy.com/media/{m.group(1)}/giphy.mp4"
    return u

def fetch_media(url: str, tmpdir: str, logger=None, req_id: str = "?") -> str:
    headers = {"User-Agent": UA, "Referer": "https://giphy.com/", "Accept": "*/*", "Connection": "keep-alive"}
    max_bytes = MAX_SRC_MB * 1024 * 1024
    tries, last = 2, None
    for k in range(tries):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(5, 30), allow_redirects=True) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                p = os.path.join(tmpdir, f"src_{int(time.time()*1000)}_{k}.mp4")
                size = 0
                with open(p, "wb") as f:
                    for chunk in r.iter_content(chunk_size=4_194_304):  # 4MB
                        if not chunk: continue
                        size += len(chunk)
                        if size > max_bytes:
                            try: os.remove(p)
                            except: pass
                            raise RuntimeError(f"source too large streamed: >{MAX_SRC_MB}MB")
                        f.write(chunk)
                if logger: logger.info(f"[{req_id}] download ok -> {p} size={size}B")
                return p
        except Exception as e:
            last = e
            if logger: logger.info(f"[{req_id}] download retry {k+1}/{tries} err={e}")
            time.sleep(0.5*(k+1))
    raise RuntimeError(f"download failed: {last}")

def _ffprobe_duration(path: str) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1", path],
            stderr=subprocess.STDOUT
        ).decode("utf-8","ignore").strip()
        return max(0.0, float(out))
    except Exception:
        return 0.0

def _trim_copy(src: str, dst: str, dur: float):
    # Remet les timestamps à zéro pour éviter des discontinuités à la concat.
    subprocess.run(
        ["ffmpeg","-y","-t", f"{dur:.3f}", "-i", src, "-c","copy",
         "-avoid_negative_ts","make_zero", dst],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def _clone_copy(src: str, dst: str):
    subprocess.run(
        ["ffmpeg","-y","-i", src, "-c","copy", dst],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def _concat_copy(list_file: str, dst: str):
    subprocess.run(
        ["ffmpeg","-y","-f","concat","-safe","0","-i",list_file,"-c","copy", dst],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def _srt_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600); t -= h*3600
    m = int(t // 60);   t -= m*60
    s = int(t);         ms = int(round((t - s)*1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _build_srt_from_plan(plan: List[Dict[str,Any]]) -> str:
    """
    Préfère une ligne SRT absolue dans seg['subtitles'] (ex: ["A --> B"]).
    Sinon, reconstruit via start_time + duration en utilisant seg['text'].
    """
    idx, lines = 1, []
    for seg in plan:
        txt = (seg.get("text") or "").strip()
        subs = seg.get("subtitles") or []
        if isinstance(subs, list) and subs and isinstance(subs[0], str) and "-->" in subs[0]:
            if txt:
                lines += [str(idx), subs[0].strip(), txt, ""]
                idx += 1
            continue
        st = _seconds(seg.get("start_time"))
        dur = _seconds(seg.get("duration"))
        if txt and dur > 0:
            S = _srt_time(st); E = _srt_time(st + dur)
            lines += [str(idx), f"{S} --> {E}", txt, ""]
            idx += 1
    return "\n".join(lines).strip()

# ---------- pipeline ----------
def generate_video(plan: List[Dict[str, Any]], audio_path: str, output_name: str,
                   temp_dir: Optional[str], width:int, height:int, fps:int,
                   logger=None, req_id:str="?", global_srt: Optional[str]=None) -> str:

    tmpdir = temp_dir or tempfile.mkdtemp(prefix="fusionbot_")

    # 1) segments à la bonne durée (copy/trim/repeat)
    part_paths: List[str] = []
    real_total = 0.0

    for i, seg in enumerate(plan):
        url = normalize_giphy_url((seg.get("gif_url") or "").strip())
        target = max(0.0, _seconds(seg.get("duration")))
        if logger: logger.info(f"[{req_id}] seg#{i} url={url} dur_req={target:.3f}")
        src = fetch_media(url, tmpdir, logger, req_id)
        src_dur = _ffprobe_duration(src) or (target if target > 0 else 2.0)

        if target <= 0.0:
            out = os.path.join(tmpdir, f"part_{i:03d}.mp4")
            _clone_copy(src, out)
            real = src_dur

        elif target < src_dur - 0.01:
            out = os.path.join(tmpdir, f"part_{i:03d}.mp4")
            _trim_copy(src, out, target)
            real = target

        elif abs(target - src_dur) <= 0.02:
            out = os.path.join(tmpdir, f"part_{i:03d}.mp4")
            _clone_copy(src, out)
            real = src_dur

        else:
            # target > src_dur : duplication + reste tronqué
            R = int(target // max(src_dur, 0.01))
            rem = max(0.0, target - R*src_dur)
            listfile = os.path.join(tmpdir, f"seg_{i:03d}.txt")

            with open(listfile, "w") as f:
                for _ in range(max(1, R)):
                    f.write(f"file '{src}'\n")

            tmp_outs = []
            if rem > 0.01:
                remf = os.path.join(tmpdir, f"rem_{i:03d}.mp4")
                _trim_copy(src, remf, rem)
                tmp_outs.append(remf)
                with open(listfile, "a") as f:
                    f.write(f"file '{remf}'\n")

            out = os.path.join(tmpdir, f"part_{i:03d}.mp4")
            _concat_copy(listfile, out)

            try: os.remove(listfile)
            except: pass
            for p in tmp_outs:
                try: os.remove(p)
                except: pass

            real = R*src_dur + rem

        try: os.remove(src)
        except: pass

        part_paths.append(out)
        real_total += real

    if logger: logger.info(f"[{req_id}] concat {len(part_paths)} parts total≈{real_total:.3f}s")

    # 2) concat finale (copy)
    list_all = os.path.join(tmpdir, "list_all.txt")
    with open(list_all, "w") as f:
        for p in part_paths: f.write(f"file '{p}'\n")
    concat_path = os.path.join(tmpdir, "concat.mp4")
    _concat_copy(list_all, concat_path)
    try: os.remove(list_all)
    except: pass
    for p in part_paths:
        try: os.remove(p)
        except: pass

    # 3) SRT final (global_srt prioritaire)
    srt_text = (global_srt or "").strip()
    if not srt_text:
        srt_text = _build_srt_from_plan(plan)

    subs_path = None
    if srt_text:
        subs_path = os.path.join(tmpdir, "subs.srt")
        with open(subs_path, "w", encoding="utf-8") as f:
            f.write(srt_text)

    # 4) encodage final unique (gravure sous-titres + audio)
    output_path = os.path.join(tmpdir, output_name)
    base_cmd = ["ffmpeg","-y","-threads", FFMPEG_THREADS, "-i", concat_path, "-i", audio_path]

    # Pas de -shortest ici pour ne pas tronquer si l'audio est plus court.
    if subs_path:
        vf = f"subtitles={subs_path}:force_style='Fontsize=36,Outline=2,Shadow=1,Alignment=2,MarginV=60'"
        cmd = base_cmd + [
            "-vf", vf,
            "-c:v","libx264","-preset", X264_PRESET,"-crf", CRF,
            "-c:a","aac",
            "-movflags","+faststart",
            output_path
        ]
    else:
        cmd = base_cmd + [
            "-c:v","libx264","-preset", X264_PRESET,"-crf", CRF,
            "-c:a","aac",
            "-movflags","+faststart",
            output_path
        ]

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try: os.remove(concat_path)
    except: pass
    if subs_path:
        try: os.remove(subs_path)
        except: pass

    if logger: logger.info(f"[{req_id}] done -> {output_path}")
    return output_path
