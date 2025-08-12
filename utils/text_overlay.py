import os
import math
import textwrap
from typing import Tuple, Optional

from PIL import Image, ImageDraw, ImageFont
import numpy as np
from moviepy.editor import ImageClip


def _load_font(font_path: Optional[str], fontsize: int) -> ImageFont.FreeTypeFont:
    """
    Essaie d'utiliser la police fournie, sinon retombe sur une police par défaut.
    """
    # 1) chemin explicite (env ou arg)
    if font_path and os.path.exists(font_path):
        try:
            return ImageFont.truetype(font_path, fontsize)
        except Exception:
            pass

    # 2) quelques polices communes si dispo
    for candidate in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]:
        if os.path.exists(candidate):
            try:
                return ImageFont.truetype(candidate, fontsize)
            except Exception:
                continue

    # 3) fallback bitmap
    return ImageFont.load_default()


def _wrap_text(txt: str, font: ImageFont.FreeTypeFont, max_width_px: int, draw: ImageDraw.ImageDraw) -> str:
    """
    Coupe le texte en lignes pour ne pas dépasser max_width_px (approx via mesure réelle).
    """
    words = txt.strip().split()
    if not words:
        return ""

    lines, line = [], []
    for w in words:
        test = " ".join(line + [w])
        bbox = draw.textbbox((0, 0), test, font=font, stroke_width=0)
        if (bbox[2] - bbox[0]) <= max_width_px:
            line.append(w)
        else:
            if line:
                lines.append(" ".join(line))
            line = [w]
    if line:
        lines.append(" ".join(line))
    return "\n".join(lines)


def _rounded_box(img: Image.Image, box: Tuple[int, int, int, int], radius: int, fill: Tuple[int, int, int, int]):
    """
    Dessine un rectangle arrondi semi-transparent derrière le texte.
    """
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1

    corner = Image.new("L", (radius * 2, radius * 2), 0)
    draw_c = ImageDraw.Draw(corner)
    draw_c.pieslice((0, 0, radius * 2, radius * 2), 180, 270, fill=255)

    mask = Image.new("L", (w, h), 255)
    mask.paste(corner.crop((0, 0, radius, radius)), (0, 0))
    mask.paste(corner.rotate(90), (w - radius, 0))
    mask.paste(corner.rotate(180), (w - radius, h - radius))
    mask.paste(corner.rotate(270), (0, h - radius))

    overlay = Image.new("RGBA", (w, h), fill)
    img.alpha_composite(overlay, (x1, y1), mask=mask)


def generate_text_overlay(
    text: str,
    duration: float,
    frame_size: Tuple[int, int],
    *,
    fontsize: int = 56,
    font_path: Optional[str] = None,
    text_color: Tuple[int, int, int] = (255, 255, 255),
    stroke_color: Tuple[int, int, int] = (16, 16, 16),
    stroke_width: int = 3,
    box_opacity: int = 120,          # 0-255
    box_margin_h: int = 48,          # marge latérale
    box_margin_v: int = 24,          # marge verticale interne
    margin_bottom: int = 110,        # distance du bas
) -> ImageClip:
    """
    Retourne un ImageClip MoviePy (avec alpha) contenant le texte mis en page,
    prêt à être superposé sur la vidéo.

    - Pas besoin d'ImageMagick (utilise Pillow).
    - Position: centrée, au bas de l'écran.
    - Léger fade-in/out pour un rendu propre.
    """
    W, H = frame_size
    if not text or duration <= 0:
        # clip vide et transparent
        return ImageClip(np.zeros((H, W, 4), dtype=np.uint8)).set_duration(max(duration, 0.01))

    # Canvas transparent
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Police
    font = _load_font(font_path or os.getenv("OVERLAY_FONT_PATH"), fontsize)

    # Largeur max du texte (laisser des bords)
    max_text_width = int(W * 0.86)

    # Wrap réaliste
    wrapped = _wrap_text(text, font, max_text_width, draw)

    # Mesure du bloc de texte
    lines = wrapped.split("\n")
    line_heights = []
    max_line_w = 0
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln or " ", font=font, stroke_width=stroke_width)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        max_line_w = max(max_line_w, w)
        line_heights.append(h)

    text_block_w = min(max_text_width, max_line_w)
    text_block_h = sum(line_heights) + (len(lines) - 1) * math.ceil(fontsize * 0.35)

    # Zone (boîte) derrière le texte
    box_w = text_block_w + 2 * box_margin_h
    box_h = text_block_h + 2 * box_margin_v
    box_x = (W - box_w) // 2
    box_y = H - margin_bottom - box_h

    if box_opacity > 0:
        _rounded_box(
            img,
            (box_x, box_y, box_x + box_w, box_y + box_h),
            radius=18,
            fill=(0, 0, 0, int(box_opacity)),
        )

    # Dessin du texte (centré)
    y = box_y + box_margin_v
    for i, ln in enumerate(lines):
        bbox = draw.textbbox((0, 0), ln or " ", font=font, stroke_width=stroke_width)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = (W - w) // 2
        draw.text(
            (x, y),
            ln,
            font=font,
            fill=text_color + (255,),
            stroke_width=stroke_width,
            stroke_fill=stroke_color + (255,),
        )
        y += h + math.ceil(fontsize * 0.35)

    # Vers ImageClip (alpha géré par le canal A)
    arr = np.array(img)
    clip = ImageClip(arr).set_duration(duration)
    # Placement bas-centre + petite animation
    clip = clip.set_position(("center", "bottom")).margin(bottom=margin_bottom, opacity=0)
    clip = clip.fadein(0.18).fadeout(0.18)
    return clip
