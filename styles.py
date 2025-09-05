# styles.py — construit la chaîne de filtres ffmpeg pour un style donné
import os, shlex
from typing import Tuple
from PIL import Image, ImageDraw

def build(style_key: str, need_dur: float, width: int, height: int, fps: int, temp_dir: str) -> Tuple[str, str, str]:
    """
    Retourne (extra_inputs, filter_complex, map_label)
      - extra_inputs: options/inputs additionnels à ajouter à la ligne ffmpeg (ex: masque PNG)
      - filter_complex: la chaîne de filtres complete
      - map_label: la sortie vidéo à mapper (ex: "[v]")
    """
    k = (style_key or "default").lower().strip()
    inner = min(width, height)

    if k in ("rounded", "round", "coins", "coin"):
        # Coins arrondis simples
        mask_path = _ensure_rounded_mask(width, height, radius=48, temp_dir=temp_dir)
        extra_inputs = f'-loop 1 -t {need_dur:.3f} -i {shlex.quote(mask_path)}'
        filter_complex = (
            # pipeline base -> WxH
            f"[0:v]"
            f"crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
            f"scale={inner}:{inner}:flags=bicubic,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps},setsar=1,format=rgba[base];"
            # masque en N&B
            f"[1:v]format=gray,scale={width}:{height}[mask];"
            # fusion alpha + sortie en yuv420p (coins transparents -> noir)
            f"[base][mask]alphamerge,format=yuv420p[v]"
        )
        return extra_inputs, filter_complex, "[v]"

    if k == "philo":
        # Style léger: contraste/saturation + coins arrondis (pas de grain)
        mask_path = _ensure_rounded_mask(width, height, radius=48, temp_dir=temp_dir)
        extra_inputs = f'-loop 1 -t {need_dur:.3f} -i {shlex.quote(mask_path)}'
        filter_complex = (
            f"[0:v]"
            f"crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
            f"scale={inner}:{inner}:flags=bicubic,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps},setsar=1,"
            # léger grade d'image
            f"eq=contrast=1.06:brightness=0.02:saturation=1.08,"
            f"unsharp=5:5:0.5:5:5:0.0,"
            f"format=rgba[base];"
            f"[1:v]format=gray,scale={width}:{height}[mask];"
            f"[base][mask]alphamerge,format=yuv420p[v]"
        )
        return extra_inputs, filter_complex, "[v]"

    # Fallback: pipeline défaut mais via style (au cas où)
    extra_inputs = ""
    filter_complex = (
        f"[0:v]"
        f"crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
        f"scale={inner}:{inner}:flags=bicubic,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps},setsar=1,format=yuv420p[v]"
    )
    return extra_inputs, filter_complex, "[v]"

def _ensure_rounded_mask(width: int, height: int, radius: int, temp_dir: str) -> str:
    os.makedirs(temp_dir, exist_ok=True)
    path = os.path.join(temp_dir, f"rounded_mask_{width}x{height}_r{radius}.png")
    if not os.path.exists(path):
        img = Image.new("L", (width, height), 0)  # L = 8-bit (grayscale)
        draw = ImageDraw.Draw(img)
        # rectangle aux coins arrondis plein (blanc = alpha 1.0)
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
        img.save(path, "PNG")
    return path