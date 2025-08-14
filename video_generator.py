import os, re, tempfile
from typing import List, Dict, Any, Optional
from moviepy.editor import VideoFileClip, AudioFileClip, CompositeVideoClip, concatenate_videoclips, vfx
from text_overlay import make_text_clip

CONCAT_METHOD = os.getenv("CONCAT_METHOD", "chain")   # 'chain' (low-mem) | 'compose'
FFMPEG_THREADS = int(os.getenv("FFMPEG_THREADS", "1"))
X264_PRESET = os.getenv("X264_PRESET", "ultrafast")
VIDEO_BITRATE = os.getenv("VIDEO_BITRATE", "2500k")

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

def fit_cover(clip: VideoFileClip, W: int, H: int) -> VideoFileClip:
    if clip.w == 0 or clip.h == 0:
        return clip
    scale = max(W/clip.w, H/clip.h)
    r = clip.resize(scale)
    x1 = max(0, (r.w - W)/2)
    y1 = max(0, (r.h - H)/2)
    return r.crop(x1=x1, y1=y1, x2=x1+W, y2=y1+H)

def _build_segment(seg: Dict[str, Any], W:int, H:int, FPS:int, logger=None, req_id:str="?"):
    url_raw = (seg.get("gif_url") or "").strip()
    url = normalize_giphy_url(url_raw)
    dur = max(0.0, _seconds(seg.get("duration")))
    txt = (seg.get("text") or "").strip()
    subs = seg.get("subtitles") or []

    if logger: logger.info(f"[{req_id}] seg url_raw={url_raw} url_norm={url} dur={dur} text={'yes' if txt else 'no'} subs={len(subs)}")

    base = VideoFileClip(url, audio=False)
    seg_clip = vfx.loop(base, duration=dur) if dur > 0 else base
    seg_clip = fit_cover(seg_clip, W, H).set_fps(FPS)

    overlays = []
    if txt:
        t = make_text_clip(txt, W=W, start=0.0, duration=seg_clip.duration, position="top", fontsize=64, y_margin=80)
        if t: overlays.append(t)

    for sub in subs:
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
    seg_clips = []
    for i, seg in enumerate(plan):
        if logger: logger.info(f"[{req_id}] build seg#{i}")
        seg_clips.append(_build_segment(seg, W,H,FPS, logger, req_id))

    if not seg_clips: raise ValueError("no segments")

    video = concatenate_videoclips(seg_clips, method=CONCAT_METHOD).set_fps(FPS)

    audio = AudioFileClip(audio_path)
    final_duration = min(video.duration, audio.duration)
    video = video.set_duration(final_duration).set_audio(audio.subclip(0, final_duration))

    out_path = os.path.join(temp_dir or tempfile.mkdtemp(prefix="fusionbot_"), output_name)

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
        temp_audiofile=os.path.join(temp_dir or ".", "temp-audio.m4a"),
        remove_temp=True,
    )

    try:
        audio.close()
        for c in seg_clips:
            c.close()
    except Exception:
        pass

    if logger: logger.info(f"[{req_id}] done -> {out_path}")
    return out_path
