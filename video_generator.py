# Montage rapide: télécharge MP4, ajuste la durée, concat "copy", mux audio, ajoute sous-titres soft.
import os, re, time, tempfile, subprocess
from typing import List, Dict, Any, Optional
import requests

FAST_COPY = True  # toujours actif pour ce backend "simple"
SOFT_SUBS = os.getenv("SOFT_SUBS", "1") == "1"
MAX_SRC_MB = int(os.getenv("MAX_SRC_MB", "120"))
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"

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

def _ext_from_ct(ct: str) -> str:
    if not ct: return ".bin"
    if "mp4" in ct: return ".mp4"
    if "gif" in ct: return ".gif"
    if "webm" in ct: return ".webm"
    return ".bin"

def fetch_media(url: str, tmpdir: str, logger=None, req_id: str = "?") -> str:
    headers = {"User-Agent": UA, "Referer": "https://giphy.com/", "Accept": "*/*", "Connection": "keep-alive"}
    max_bytes = MAX_SRC_MB * 1024 * 1024
    try:
        h = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
        cl = int(h.headers.get("Content-Length","0"))
        if cl and cl > max_bytes:
            raise RuntimeError(f"source too large: {cl}B > {max_bytes}B")
    except Exception as e:
        if logger: logger.info(f"[{req_id}] HEAD warn: {e}")

    tries, last = 2, None
    for k in range(tries):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(5, 30), allow_redirects=True) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                ct = r.headers.get("Content-Type","")
                ext = _ext_from_ct(ct)
                if url.lower().endswith(".mp4"): ext = ".mp4"
                p = os.path.join(tmpdir, f"src_{int(time.time()*1000)}_{k}{ext}")
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
                if logger: logger.info(f"[{req_id}] download ok -> {p} size={size}B ct={ct}")
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
    subprocess.run(
        ["ffmpeg","-y","-t", f"{dur:.3f}", "-i", src, "-c","copy","-avoid_negative_ts","make_zero", dst],
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

def _collect_soft_subs_from_plan(plan: List[Dict[str,Any]], seg_durs: List[float]) -> Optional[str]:
    idx = 1
    lines = []
    offset = 0.0
    for i, seg in enumerate(plan):
        subs = seg.get("subtitles") or []
        # cas 1: chaînes SRT absolues → utiliser seg["text"] comme contenu
        used_abs = False
        for s in subs:
            if isinstance(s, str) and "-->" in s:
                used_abs = True
                txt = (seg.get("text") or "").strip()
                if not txt: continue
                lines += [str(idx), s.strip(), txt, ""]
                idx += 1
        if not used_abs:
            # cas 2: objets relatifs {start,end,text}
            for sub in subs:
                if not isinstance(sub, dict): continue
                st = _seconds(sub.get("start")); en = _seconds(sub.get("end"))
                txt = (sub.get("text") or "").strip()
                if en <= st or not txt: continue
                S = _srt_time(offset + st); E = _srt_time(offset + en)
                lines += [str(idx), f"{S} --> {E}", txt, ""]
                idx += 1
        offset += seg_durs[i] if i < len(seg_durs) else 0.0
    if not lines:
        return None
    return "\n".join(lines)

def generate_video(plan: List[Dict[str, Any]], audio_path: str, output_name: str,
                   temp_dir: Optional[str], width:int, height:int, fps:int,
                   logger=None, req_id:str="?", global_srt: Optional[str]=None) -> str:
    tmpdir = temp_dir or tempfile.mkdtemp(prefix="fusionbot_")

    # 1) construire chaque segment à la bonne durée (copy/trim/repeat)
    part_paths: List[str] = []
    seg_durs: List[float] = []
    for i, seg in enumerate(plan):
        url = normalize_giphy_url((seg.get("gif_url") or "").strip())
        target = _seconds(seg.get("duration")) or 0.0
        if logger: logger.info(f"[{req_id}] seg#{i} url={url} dur_req={target:.3f}")
        src = fetch_media(url, tmpdir, logger, req_id)
        src_dur = _ffprobe_duration(src) or (target if target>0 else 2.0)

        if target <= 0.0 or abs(target - src_dur) < 0.02:
            out = os.path.join(tmpdir, f"part_{i:03d}.mp4")
            subprocess.run(["ffmpeg","-y","-i",src,"-c","copy", out],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            real = src_dur
        elif target < src_dur:
            out = os.path.join(tmpdir, f"part_{i:03d}.mp4")
            _trim_copy(src, out, target)
            real = target
        else:
            R = int(target // max(src_dur, 0.01))
            rem = max(0.0, target - R*src_dur)
            lst = os.path.join(tmpdir, f"seg_{i:03d}.txt")
            with open(lst, "w") as f:
                for _ in range(max(1, R)):
                    f.write(f"file '{src}'\n")
            tmp_outs = []
            if rem > 0.01:
                remf = os.path.join(tmpdir, f"rem_{i:03d}.mp4")
                _trim_copy(src, remf, rem)
                tmp_outs.append(remf)
                with open(lst, "a") as f:
                    f.write(f"file '{remf}'\n")
            out = os.path.join(tmpdir, f"part_{i:03d}.mp4")
            _concat_copy(lst, out)
            try: os.remove(lst)
            except: pass
            for p in tmp_outs:
                try: os.remove(p)
                except: pass
            real = R*src_dur + rem

        try: os.remove(src)
        except: pass

        part_paths.append(out)
        seg_durs.append(real)

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

    # 3) sous-titres soft (SRT texte)
    subs_path = None
    if SOFT_SUBS:
        srt_text = (global_srt or "").strip() if global_srt else None
        if not srt_text:
            srt_text = _collect_soft_subs_from_plan(plan, seg_durs)
        if srt_text:
            subs_path = os.path.join(tmpdir, "subs.srt")
            with open(subs_path, "w", encoding="utf-8") as f:
                f.write(srt_text)

    # 4) mux audio (+ subs) vers sortie (toujours en copy vidéo)
    output_path = os.path.join(tmpdir, output_name)
    if subs_path:
        cmd = [
            "ffmpeg","-y",
            "-i", concat_path, "-i", audio_path, "-i", subs_path,
            "-map","0:v:0","-map","1:a:0","-map","2:0",
            "-c:v","copy","-c:a","aac","-c:s","mov_text",
            "-metadata:s:s:0","language=fr",
            "-shortest","-movflags","+faststart", output_path
        ]
    else:
        cmd = [
            "ffmpeg","-y",
            "-i", concat_path, "-i", audio_path,
            "-map","0:v:0","-map","1:a:0",
            "-c:v","copy","-c:a","aac",
            "-shortest","-movflags","+faststart", output_path
        ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try: os.remove(concat_path)
    except: pass
    if subs_path:
        try: os.remove(subs_path)
        except: pass

    if logger: logger.info(f"[{req_id}] done -> {output_path}")
    return output_path
