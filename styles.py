# styles.py — styles externes ultra light
import os
from typing import Tuple

from PIL import Image, ImageDraw  # Pillow est déjà dans requirements


def _rounded_mask_path(temp_dir: str, width: int, height: int, radius_px: int) -> str:
    """
    Crée (si absent) un PNG de masque en niveaux de gris (255 à l'intérieur,
    0 à l'extérieur) avec un grand rectangle à coins arrondis couvrant tout le cadre.
    """
    os.makedirs(temp_dir, exist_ok=True)
    mask_name = f"rounded_mask_{width}x{height}_r{radius_px}.png"
    mask_path = os.path.join(temp_dir, mask_name)
    if os.path.exists(mask_path) and os.path.getsize(mask_path) > 0:
        return mask_path

    # Image grayscale "L": 0 = transparent, 255 = opaque
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)

    # Rectangle avec coins arrondis plein cadre
    # (petite marge de 0.5 pour éviter les escaliers sur l'alpha)
    rect = (0, 0, width, height)
    draw.rounded_rectangle(rect, radius=radius_px, fill=255)

    img.save(mask_path, format="PNG")
    return mask_path


def _build_philo(need_dur: float, width: int, height: int, fps: int, temp_dir: str,
                 radius_px: int = 48) -> Tuple[str, str, str]:
    """
    Style "philo" :
    - crop carré centré
    - scale à min(W,H)
    - pad en WxH (centré)
    - coins arrondis via masque PNG (alphamerge)
    - format final yuv420p (compatible réseaux sociaux)
    Retourne: (extra_inputs, filter_complex, map_label)
    """
    inner = min(width, height)
    mask_path = _rounded_mask_path(temp_dir, width, height, radius_px)

    # Input 0 : source (gif/mp4) — déjà fourni par video_generator
    # Input 1 : masque PNG (on le fige pendant toute la durée du segment)
    extra_inputs = f'-loop 1 -t {need_dur:.3f} -i "{mask_path}"'

    # Chaîne vidéo :
    #  [0:v] -> carré -> scale inner -> pad WxH -> fps -> setsar -> RGBA (pour alphamerge)
    #  [1:v] -> format gray -> scale WxH => [mask]
    #  alphamerge -> yuv420p => [v]
    filter_complex = (
        f"[0:v]"
        f"crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(ih,iw))/2',"
        f"scale={inner}:{inner}:flags=bicubic,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps},setsar=1,format=rgba[base];"
        f"[1:v]format=gray,scale={width}:{height}[mask];"
        f"[base][mask]alphamerge,format=yuv420p[v]"
    )

    map_label = "[v]"
    return extra_inputs, filter_complex, map_label


def build(style_key, need_dur: float, width: int, height: int, fps: int, temp_dir: str):
    """
    Point d’entrée appelé par video_generator._encode_segment_with_style.
    - Si style == "philo" ou "rounded" => coins arrondis (aucun filtre).
    - Sinon => fallback minimal sans arrondi (crop/scale/pad), pour éviter un crash.
    """
    # Sécurise le style_key même si ce n'est pas une string
    s = str(style_key).strip().lower() if style_key is not None else "default"

    if s in ("philo", "rounded"):
        return _build_philo(need_dur, width, height, fps, temp_dir)

    # Fallback "sans style" (équivalent d'un -vf simple)
    inner = min(width, height)
    extra_inputs = ""  # aucun input en plus
    filter_complex = (
        f"[0:v]"
        f"crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(ih,iw))/2',"
        f"scale={inner}:{inner}:flags=bicubic,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps},setsar=1,format=yuv420p[v]"
    )
    map_label = "[v]"
    return extra_inputs, filter_complex, map_label
