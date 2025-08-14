import os, math
from typing import Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from moviepy.editor import ImageClip

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
    lines = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = d.textbbox((0,0), test, font=font, stroke_width=stroke_width)
        if bbox[2] > max_w and cur:
            lines.append(cur); cur = w
        else:
            cur = test
    if cur: lines.append(cur)
    if not lines: lines = [""]

    line_heights = []
    max_line_w = 0
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
    position: str = "bottom",  # "bottom" or "top"
    y_margin: int = 64,
    text_rgb: Tuple[int,int,int] = (255,255,255),
    stroke_rgb: Tuple[int,int,int] = (0,0,0),
    stroke_width: int = 4,
    bg_alpha: int = 128,
    radius: int = 28,
):
    if not text:
        return None
    max_w = int(W*max_w_ratio)
    font = _load_font(fontsize)
    lines, text_w, text_h = _wrap(text, max_w, font, stroke_width)

    pad_x, pad_y = 24, 16
    box_w = text_w + 2*pad_x
    box_h = text_h + 2*pad_y

    img = Image.new("RGBA", (box_w, box_h), (0,0,0,0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0,0,box_w-1,box_h-1), radius, fill=(0,0,0,bg_alpha))

    y = pad_y
    for ln in lines:
        bbox = d.textbbox((0,0), ln, font=font, stroke_width=stroke_width)
        w = bbox[2]
        h = bbox[3]-bbox[1]
        x = (box_w - w)//2
        d.text((x,y), ln, font=font, fill=text_rgb+(255,), stroke_width=stroke_width, stroke_fill=stroke_rgb+(255,))
        y += h + int(font.size*0.3)

    arr = np.array(img)
    clip = ImageClip(arr).set_start(start)
    if duration is not None:
        clip = clip.set_duration(duration)
    if position == "top":
        clip = clip.set_position(("center", lambda t: y_margin))
    else:
        clip = clip.set_position(("center", "bottom")).margin(bottom=y_margin, opacity=0)
    clip = clip.fadein(0.15).fadeout(0.15)
    return clip
