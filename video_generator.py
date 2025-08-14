# video_generator.py — télécharge d'abord les médias GIPHY en local (robuste)
import os, re, tempfile, time
from typing import List, Dict, Any, Optional
import requests

from moviepy.editor import (
    VideoFileClip, AudioFileClip, ImageClip,
    CompositeVideoClip, concatenate_videoclips, vfx
)
from PIL import Image, ImageDraw, ImageFont
import numpy as np

# Réglages low-RAM via ENV (déjà posés côté Render)
CONCAT_METHOD = os.getenv("CONCAT_METHOD", "chain")   # 'chain' (léger) | 'compose'
FFMPEG_THREADS = int(os.getenv("FFMPEG_THREADS", "1"))
X264_PRESET = os.getenv("X264_PRESET", "ultrafast")
VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "2500k")

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
    """
    Télécharge l'URL (avec UA/Referer + retries) vers un fichier local.
    Renvoie le chemin du fichier local.
    """
    headers = {
        "User-Agent": UA,
        "Referer": "https://giphy.com/",
        "Accept": "*/*",
        "Connection": "keep-alive",
    }
    tries = 3
    last_err = None
    for k in range(tries):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(5, 30), allow_redirects=True) as r:
                sc = r.status_code
                if sc != 200:
                    raise RuntimeError(f"HTTP {sc}")
                ct = r.headers.get("Content-Type", "")
                ext = _ext_from_ct(ct)
                # force .mp4 si l'URL finit par .mp4
                if url.lower().endswith(".mp4"): ext = ".mp4"
                fname = f"seg_{int(time.time()*1000)}_{k}{ext}"
                fpath = os.path.join(tmpdir, fname)
                with open(fpath, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1_048_576):
                        if chunk:
                            f.write(chunk)
                if logger: logger.info(f"[{req_id}] download ok -> {fpath} ct={ct}")
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

# ---------- Overlays texte (PIL) ----------
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
        clip = clip.set_position(("center", lambda t: y_margin))
    else:
        clip = clip.set_position(("center", "bottom")).margin(bottom=y_margin, opacity=0)
    clip = clip.fadein(0.15).fadeout(0.15)
    return clip
# ------------------------------------------

def _build_segment(seg: Dict[str, Any], W:int, H:int, FPS:int, tmpdir:str, logger=None, req_id:str="?"):
    url_raw = (seg.get("gif_url") or "").strip()
    url = normalize_giphy_url(url_raw)
    dur = max(0.0, _seconds(seg.get("duration")))
    txt = (seg.get("text") or "").strip()
    subs = seg.get("subtitles") or []
    if logger: logger.info(f"[{req_id}] seg url_raw={url_raw} url_norm={url} dur={dur} text={'yes' if txt else 'no'} subs={len(subs)}")

    # 🔽 Nouveau : on télécharge le média avant ouverture MoviePy
    local_path = fetch_media(url, tmpdir, logger, req_id)

    base = VideoFileClip(local_path, audio=False)
    seg_clip = vfx.loop(base, duration=dur) if dur > 0 else base
    seg_clip = fit_cover(seg_clip, W, H).set_fps(FPS)

    overlays = []
    if txt:
        t = make_text_clip(txt, W=W, start=0.0, duration=seg_clip.duration, position="top", fontsize=64, y_margin=80)
        if t: overlays.append(t)

    # Robustesse: n'utiliser que les dicts {"start","end","text"}
    for sub in subs:
        if not isinstance(sub, dict):  # ignore strings/SRT bruts
            continue
        s = max(0.0, _seconds(sub.get("start")))
        e = max(s, _seconds(sub.get("end")))
        txt_sub = (sub.get("text") or "").strip()
        if not txt_sub or e <= s: continue
        sc = make_text_clip(txt_sub, W=W, start=s, duration=(e-s), position="bottom", fontsize=56, y_margin=64)
        if sc: overlays.append(sc)

    if overlays:
        comp = CompositeVideoClip([seg_clip, *overlays], size=(W, H))
        comp = comp.set_duration(seg_clip.duration).set_fps(FPS)
    else:
        comp = seg_clip
    return comp

def generate_video(plan: List[Dict[str, Any]], audio_path: str, output_name: str,
                   temp_dir: Optional[str], width:int, height:int, fps:int,
                   logger=None, req_id:str="?") -> str:
    W,H,FPS = width, height, fps
    tmpdir = temp_dir or tempfile.mkdtemp(prefix="fusionbot_")

    seg_clips = []
    for i, seg in enumerate(plan):
        if logger: logger.info(f"[{req_id}] build seg#{i}")
        seg_clips.append(_build_segment(seg, W,H,FPS, tmpdir, logger, req_id))

    if not seg_clips: raise ValueError("no segments")

    video = concatenate_videoclips(seg_clips, method=CONCAT_METHOD).set_fps(FPS)

    audio = AudioFileClip(audio_path)
    final_duration = min(video.duration, audio.duration)
    video = video.set_duration(final_duration).set_audio(audio.subclip(0, final_duration))

    out_path = os.path.join(tmpdir, output_name)

    video.write_videofile(
        out_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset=X264_PRESET,
        bitrate=VIDEO_BITRATE,
        threads=FFMPEG_THREADS,
        verbose=False,
        logger=None,
        temp_audiofile=os.path.join(tmpdir, "temp-audio.m4a"),
        remove_temp=True,
    )

    try:
        audio.close()
        for c in seg_clips: c.close()
    except Exception:
        pass

    if logger: logger.info(f"[{req_id}] done -> {out_path}")
    return out_path
