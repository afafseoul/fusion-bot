import os, re, time, tempfile, subprocess
from typing import List, Dict, Any, Optional
import requests

FAST_COPY = True
MAX_SRC_MB = int(os.getenv("MAX_SRC_MB", "120"))
UA = "Mozilla/5.0"

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
    headers = {"User-Agent": UA, "Referer": "https://giphy.com/", "Accept": "*/*"}
    max_bytes = MAX_SRC_MB * 1024 * 1024
    tries, last = 2, None
    for k in range(tries):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(5, 30), allow_redirects=True) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                ext = ".mp4"
                p = os.path.join(tmpdir, f"src_{int(time.time()*1000)}_{k}{ext}")
                size = 0
                with open(p, "wb") as f:
                    for chunk in r.iter_content(chunk_size=4_194_304):
                        if not chunk: continue
                        size += len(chunk)
                        if size > max_bytes:
                            os.remove(p)
                            raise RuntimeError(f"source too large streamed: >{MAX_SRC_MB}MB")
                        f.write(chunk)
                if logger: logger.info(f"[{req_id}] download ok -> {p} size={size}B")
                return p
        except Exception as e:
            last = e
            if logger: logger.info(f"[{req_id}] retry {k+1}/{tries} err={e}")
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
    subprocess.run(
        ["ffmpeg","-y","-t", f"{dur:.3f}", "-i", src, "-c","copy", dst],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def _concat_copy(list_file: str, dst: str):
    subprocess.run(
        ["ffmpeg","-y","-f","concat","-safe","0","-i",list_file,"-c","copy", dst],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def _srt_time(t: float) -> str:
    h = int(t // 3600); t -= h*3600
    m = int(t // 60);   t -= m*60
    s = int(t);         ms = int(round((t - s)*1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _collect_srt(plan: List[Dict[str,Any]]) -> str:
    idx = 1
    lines = []
    for seg in plan:
        subs = seg.get("subtitles")
        txt = seg.get("text") or ""
        if subs and isinstance(subs, list) and "-->" in subs[0]:
            lines += [str(idx), subs[0], txt, ""]
            idx += 1
    return "\n".join(lines)

def generate_video(plan: List[Dict[str, Any]], audio_path: str, output_name: str,
                   temp_dir: Optional[str], width:int, height:int, fps:int,
                   logger=None, req_id:str="?", global_srt: Optional[str]=None) -> str:

    tmpdir = temp_dir or tempfile.mkdtemp(prefix="fusionbot_")

    part_paths = []
    for i, seg in enumerate(plan):
        url = normalize_giphy_url(seg.get("gif_url", ""))
        target = _seconds(seg.get("duration"))
        if logger: logger.info(f"[{req_id}] seg#{i} {url}")
        src = fetch_media(url, tmpdir, logger, req_id)
        if target > 0:
            out = os.path.join(tmpdir, f"part_{i:03d}.mp4")
            _trim_copy(src, out, target)
        else:
            out = src
        part_paths.append(out)

    list_all = os.path.join(tmpdir, "list_all.txt")
    with open(list_all, "w") as f:
        for p in part_paths: f.write(f"file '{p}'\n")
    concat_path = os.path.join(tmpdir, "concat.mp4")
    _concat_copy(list_all, concat_path)

    # SRT → gravage
    subs_path = os.path.join(tmpdir, "subs.srt")
    with open(subs_path, "w", encoding="utf-8") as f:
        f.write(global_srt.strip() if global_srt else _collect_srt(plan))

    output_path = os.path.join(tmpdir, output_name)
    cmd = [
        "ffmpeg","-y",
        "-i", concat_path, "-i", audio_path,
        "-vf", f"subtitles={subs_path}:force_style='FontSize=28,Outline=1,Shadow=1'",
        "-c:a","aac","-shortest","-movflags","+faststart", output_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if logger: logger.info(f"[{req_id}] done -> {output_path}")
    return output_path
