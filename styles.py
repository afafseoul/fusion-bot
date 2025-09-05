# styles.py
# — Répertoire des styles vidéo.
# API attendue par video_generator.py :
#   build(style_name, need_dur, width, height, fps, temp_dir)
# retourne soit None (pas de style), soit un tuple:
#   (extra_inputs, filter_complex, map_out)

from typing import Tuple, Optional
import os
from PIL import Image, ImageDraw

# -------- assets légers générés une fois (PNG) --------

def _ensure_mask_png(temp_dir: str, size: int, radius: int) -> str:
    """
    Crée un PNG (RGBA) de masque avec coins arrondis (blanc opaque sur fond transparent).
    Très rapide côté FFmpeg (simple alphamerge), beaucoup plus léger que geq/hypot.
    """
    path = os.path.join(temp_dir, f"mask_{size}_r{radius}.png")
    if os.path.exists(path):
        return path
    # alpha uniquement (L), puis on le met comme alpha d'un RGBA
    alpha = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(alpha)
    draw.rounded_rectangle((0, 0, size-1, size-1), radius=radius, fill=255)
    rgba = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    rgba.putalpha(alpha)
    rgba.save(path, "PNG")
    return path

def _ensure_stripes_png(temp_dir: str, width: int, height: int) -> str:
    """
    Génère un overlay de fines rayures (horiz/vert) très transparentes.
    Remplace noise/vignette/drawgrid par un simple overlay -> coût CPU minimal.
    """
    path = os.path.join(temp_dir, f"stripes_{width}x{height}.png")
    if os.path.exists(path):
        return path
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    step = 8         # espacement des lignes
    alpha = 18       # ~7% d’opacité
    col = (0, 0, 0, alpha)
    for y in range(0, height, step):
        draw.line([(0, y), (width, y)], fill=col, width=1)
    for x in range(0, width, step):
        draw.line([(x, 0), (x, height)], fill=col, width=1)
    img.save(path, "PNG")
    return path

# --------------------- styles --------------------------

def philo(need_dur: float, width: int, height: int, fps: int, temp_dir: str) -> Tuple[str, str, str]:
    """
    philo = carré centré avec coins arrondis + léger look 'film' via overlay stripes PNG.
    Pas de noise/vignette/drawgrid -> rapide sur Render Free.
    """
    inner = max(200, int(min(width, height) * 0.78))   # ≈842 pour 1080x1920
    radius = max(8, inner // 18)                       # ≈48

    mask_png    = _ensure_mask_png(temp_dir, inner, radius)
    stripes_png = _ensure_stripes_png(temp_dir, width, height)

    # On ajoute 2 inputs (les PNG) bouclés sur la durée du segment
    extra_inputs = (
        f"-loop 1 -t {need_dur:.3f} -i {mask_png} "
        f"-loop 1 -t {need_dur:.3f} -i {stripes_png}"
    )

    # Graphe : crop carré -> scale inner -> fps -> alphamerge(mask) -> pad -> overlay(stripes)
    filter_complex = (
        f"[0:v]"
        f"crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
        f"scale={inner}:{inner}:flags=bicubic,"
        f"fps={fps},setsar=1[base];"
        f"[base]format=rgba[b_rgba];"
        f"[1:v]format=rgba[mask];"
        f"[b_rgba][mask]alphamerge[rounded];"
        f"[rounded]pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[padded];"
        f"[2:v]format=rgba[stripes];"
        f"[padded][stripes]overlay=0:0:format=auto,format=yuv420p[v]"
    )

    # On map la sortie [v]
    return extra_inputs, filter_complex, "[v]"

# ------------------ builder public ---------------------

def build(style_name: Optional[str], need_dur: float, width: int, height: int, fps: int, temp_dir: str):
    """
    Retourne (extra_inputs, filter_complex, map_out) pour un style donné,
    ou None si pas de style (=> chemin 'default' dans video_generator).
    """
    s = (style_name or "").strip().lower()
    if not s or s == "default":
        return None
    if s == "philo":
        return philo(need_dur, width, height, fps, temp_dir)
    # ex: if s == "horror": return horror(...)
    return None
