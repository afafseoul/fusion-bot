# video_generator.py — download -> encode segment -> concat (low RAM + low /tmp)
import os, re, tempfile, time, subprocess
from typing import List, Dict, Any, Optional
import requests

from moviepy.editor import (
    VideoFileClip, AudioFileClip, ImageClip,
    CompositeVideoClip, vfx
)
from PIL import Image, ImageDraw, ImageFont
import numpy as np

# ENV (Free safe defaults)
CONCAT_METHOD = os.getenv("CONCAT_METHOD", "chain")   # kept for compatibility
FFMPEG_THREADS = int(os.getenv("FFMPEG_THREADS", "1"))
X264_PRESET = os.getenv("X264_PRESET", "ultrafast")
VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "2500k")
MAX_SRC_MB = int(os.getenv("MAX_SRC_MB", "60"))

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
    tries, last_err = 3, None

    # HEAD pour Content-Length (non bloquant si ça échoue)
    try:
        h = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
        cl = int(h.headers.get("Content-Length", "0"))
        if cl and cl > max_bytes:
            raise RuntimeError(f"source too large: {cl}B > {max_bytes}B")
    except Exception as e:
        if logger: logger.info(f"[{req_id}] HEAD warn: {e}")

    for k in range(tries):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(5, 30), allow_redirects=True) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                ct = r.headers.get("Content-Type", "")
                ext = _ext_from_ct(ct)
                if url.lower().endswith(".mp4"): ext = ".mp4"
                fname = f"seg_{int(time.time()*1000)}_{k}{ext}"
                fpath = os.path.join(tmpdir, fname)
                written = 0
                with open(fpath, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1_048_576):
                        if not chunk: continue
                        written += len(chunk)
                        if written > max_bytes:
                            try: os.remove(fpath)
                            except: pass
                            raise RuntimeError(f"source too large streamed: >{MAX_SRC_MB}MB")
                        f.write(chunk)
                if logger: logger.info(f"[{req_id}] download ok -> {fpath} size={written}B ct={ct}")
                return fpath
        except Exception as e:
            last_err = e
            if logger: logger.info(f"[{req_id}] download retry {k+1}/{tries} err={e}")
            time.sleep(0.5 * (k+1))
    raise RuntimeError(f"download failed: {last_err}")

def fit_cover(clip: VideoFileClip, W: int, H: int) -> VideoFileClip:
    if clip.w == 0 or clip.h == 0: return clip
    scale = max(W/clip.w, H/clip.h)
    r = clip.resize(scale)
    x1 = max(0, (r.w - W)/2)
    y1 = max(0, (r.h - H)/2)
    return r.crop(x1=x1, y1=y1, x2=x1+W, y2=y1+H)

# ---------- text overlays ----------
def _pick_font() -> Optional[str]:
    env = os.getenv("FONT_PATH")
    candidates = []
    if env and os.path.isfile(env): candidates.append(env)
    candidates += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for p in candidates:
        if os.path.isfile(p): return p
    return None
_FONT_PATH = _pick_font()

def _load_font(size: int) -> ImageFont.ImageFont:
    try:
        if _FONT_PATH: return ImageFont.truetype(_FONT_PATH, size=size)
    except Exception:
        pass
    return ImageFont.load_default()

def _wrap(text: str, max_w: int, font: ImageFont.ImageFont, stroke_width:int):
    if not text: text = ""
    tmp = Image.new("RGBA", (max_w, 10), (0,0,0,0))
    d = ImageDraw.Draw(tmp)
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = d.textbbox((0,0), test, font=font, stroke_width=stroke_width)
        if bbox[2] > max_w and cur:
            lines.append(cur); cur = w
        else:
            cur = test
    if cur: lines.append(cur)
    if not lines: lines = [""]

    line_heights, max_line_w = [], 0
    for ln in lines:
        bbox = d.textbbox((0,0), ln, font=font, stroke_width=stroke_width)
        max_line_w = max(max_line_w, bbox[2])
        line_heights.append(bbox[3]-bbox[1])
    text_w = max_line_w
    text_h = sum(line_heights) + (len(lines)-1)*int(font.size*0.3)
    return lines, text_w, text_h

def make_text_clip(
    text: str,
    W: int,
    max_w_ratio: float = 0.92,
    fontsize: int = 56,
    start: float = 0.0,
    duration: float = None,
    position: str = "bottom",  # "bottom" | "top"
    y_margin: int = 64,
    stroke_width: int = 4,
):
    if not text: return None
    max_w = int(W*max_w_ratio)
    font = _load_font(fontsize)
    lines, text_w, text_h = _wrap(text, max_w, font, stroke_width)
    pad_x, pad_y = 24, 16
    box_w = text_w + 2*pad_x
    box_h = text_h + 2*pad_y
    img = Image.new("RGBA", (box_w, box_h), (0,0,0,0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0,0,box_w-1,box_h-1), 28, fill=(0,0,0,128))
    y = pad_y
    for ln in lines:
        bbox = d.textbbox((0,0), ln, font=font, stroke_width=stroke_width)
        w = bbox[2]; h = bbox[3]-bbox[1]
        x = (box_w - w)//2
        d.text((x,y), ln, font=font, fill=(255,255,255,255),
               stroke_width=stroke_width, stroke_fill=(0,0,0,255))
        y += h + int(font.size*0.3)
    arr = np.array(img)
    clip = ImageClip(arr).set_start(start)
    if duration is not None: clip = clip.set_duration(duration)
    if position == "top":
        clip = clip.set_position(("center", y_margin))   # y fixe en pixels
    else:
        clip = clip.set_position(("center", "bottom")).margin(bottom=y_margin, opacity=0)
    clip = clip.fadein(0.15).fadeout(0.15)
    return clip
# ------------------------------------------

def generate_video(plan: List[Dict[str, Any]], audio_path: str, output_name: str,
                   temp_dir: Optional[str], width:int, height:int, fps:int,
                   logger=None, req_id:str="?") -> str:
    W,H,FPS = width, height, fps
    tmpdir = temp_dir or tempfile.mkdtemp(prefix="fusionbot_")

    seg_paths: List[str] = []

    for i, seg in enumerate(plan):
        if logger: logger.info(f"[{req_id}] build seg#{i}")

        url_raw = (seg.get("gif_url") or "").strip()
        url = normalize_giphy_url(url_raw)
        dur = max(0.0, _seconds(seg.get("duration")))
        txt = (seg.get("text") or "").strip()
        subs = seg.get("subtitles") or []

        local = fetch_media(url, tmpdir, logger, req_id)

        base = VideoFileClip(local, audio=False)
        seg_clip = vfx.loop(base, duration=dur) if dur > 0 else base
        seg_clip = fit_cover(seg_clip, W, H).set_fps(FPS)

        overlays = []
        if txt:
            t = make_text_clip(txt, W=W, start=0.0, duration=seg_clip.duration, position="top", fontsize=64, y_margin=80)
            if t: overlays.append(t)

        for sub in subs:
            if not isinstance(sub, dict):  # ignore SRT strings
                continue
            s = max(0.0, _seconds(sub.get("start")))
            e = max(s, _seconds(sub.get("end")))
            txt_sub = (sub.get("text") or "").strip()
            if not txt_sub or e <= s: continue
            sc = make_text_clip(txt_sub, W=W, start=s, duration=(e-s), position="bottom", fontsize=56, y_margin=64)
            if sc: overlays.append(sc)

        comp = CompositeVideoClip([seg_clip, *overlays], size=(W, H)).set_duration(seg_clip.duration).set_fps(FPS) if overlays else seg_clip

        out_seg = os.path.join(tmpdir, f"part_{i:03d}.mp4")
        comp.write_videofile(
            out_seg, fps=FPS, codec="libx264", audio=False,
            preset=X264_PRESET, bitrate=VIDEO_BITRATE, threads=FFMPEG_THREADS,
            verbose=False, logger=None
        )

        # libère RAM + supprime le média source téléchargé
        try:
            if comp is not seg_clip: comp.close()
            seg_clip.close()
            base.close()
        except Exception:
            pass
        try:
            os.remove(local)
        except Exception:
            pass

        seg_paths.append(out_seg)

    # concat (copy) via ffmpeg
    list_file = os.path.join(tmpdir, "list.txt")
    with open(list_file, "w") as f:
        for p in seg_paths:
            f.write(f"file '{p}'\n")

    concat_path = os.path.join(tmpdir, "concat.mp4")
    subprocess.run(
        ["ffmpeg","-y","-f","concat","-safe","0","-i",list_file,"-c","copy",concat_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # dès que concat existe, nettoie segments + list
    for p in seg_paths:
        try: os.remove(p)
        except Exception: pass
    try: os.remove(list_file)
    except Exception: pass

    # mux audio (shortest) vers fichier final
    output_path = os.path.join(tmpdir, output_name)
    subprocess.run(
        ["ffmpeg","-y",
         "-i", concat_path, "-i", audio_path,
         "-map","0:v:0","-map","1:a:0",
         "-c:v","copy","-c:a","aac",
         "-shortest","-movflags","+faststart", output_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # supprime concat intermédiaire
    try: os.remove(concat_path)
    except Exception: pass

    if logger: logger.info(f"[{req_id}] done -> {output_path}")
    return output_path
