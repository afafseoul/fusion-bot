# styles.py (extrait)
import os
from typing import Tuple
from PIL import Image, ImageDraw

def _ensure_mask_png(temp_dir: str, size: int, radius: int) -> str:
    path = os.path.join(temp_dir, f"mask_philo_{size}_r{radius}.png")
    if os.path.exists(path): return path
    img = Image.new("RGBA", (size, size), (0,0,0,0))
    ImageDraw.Draw(img).rounded_rectangle((0,0,size,size), radius=radius, fill=(255,255,255,255))
    img.save(path, "PNG"); return path

def _ensure_stripes_png(temp_dir: str, width: int, height: int) -> str:
    # fines lignes horizontales/verticales semi-transparentes (effet vinyle light)
    path = os.path.join(temp_dir, f"stripes_{width}x{height}.png")
    if os.path.exists(path): return path
    img = Image.new("RGBA", (width, height), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    step = 8
    alpha = 18  # ~7% d’opacité
    col = (0,0,0,alpha)
    for y in range(0, height, step):
        draw.line([(0,y),(width,y)], fill=col, width=1)
    for x in range(0, width, step):
        draw.line([(x,0),(x,height)], fill=col, width=1)
    img.save(path, "PNG"); return path

def philo(need_dur: float, width: int, height: int, fps: int, temp_dir: str) -> Tuple[str, str, str]:
    inner = max(200, int(min(width, height)*0.78))   # ≈842 pour 1080x1920
    radius = max(8, inner//18)                       # ≈48
    mask_png    = _ensure_mask_png(temp_dir, inner, radius)

    # --- Choisis l’un des deux blocs ci-dessous ---

    USE_STRIPES = True  # True => “philo_stripes”; False => “philo_core” (super rapide)

    if USE_STRIPES:
        stripes_png = _ensure_stripes_png(temp_dir, width, height)
        extra = f"-loop 1 -t {need_dur:.3f} -i {mask_png} -loop 1 -t {need_dur:.3f} -i {stripes_png}"
        fc = (
            f"[0:v]crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
            f"scale={inner}:{inner}:flags=bicubic,fps={fps},setsar=1[base];"
            f"[base]format=rgba[b_rgba];[1:v]format=rgba[mask];"
            f"[b_rgba][mask]alphamerge[rounded];"
            f"[rounded]pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[padded];"
            f"[2:v]format=rgba[stripes];"
            f"[padded][stripes]overlay=0:0:format=auto,format=yuv420p[v]"
        )
    else:
        # philo_core : pas de film du tout
        extra = f"-loop 1 -t {need_dur:.3f} -i {mask_png}"
        fc = (
            f"[0:v]crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
            f"scale={inner}:{inner}:flags=bicubic,fps={fps},setsar=1[base];"
            f"[base]format=rgba[b_rgba];[1:v]format=rgba[mask];"
            f"[b_rgba][mask]alphamerge, pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"format=yuv420p[v]"
        )

    return extra, fc, "[v]"
