# styles.py
from __future__ import annotations
import os
from typing import List, Tuple

try:
    from PIL import Image, ImageDraw  # Pillow
except Exception:
    Image = None
    ImageDraw = None


def _safe_str(x) -> str:
    try:
        return str(x).strip()
    except Exception:
        return ""


def build(
    style_key,
    need_dur: float,
    width: int,
    height: int,
    fps: int,
    temp_dir: str,
) -> Tuple[List[str], str | None, str | None]:
    """
    Retourne (extra_inputs, filter_complex, map_label) pour le style demandé.

    - extra_inputs : liste d’arguments ffmpeg (ex: ["-loop","1","-t","2.5","-i","/tmp/mask.png"])
    - filter_complex : la chaîne pour -filter_complex (ou None si style par défaut)
    - map_label : le label vidéo final à mapper (ex: "[v]") ou None

    Si style est "default"/vide -> aucun style (la pipeline par défaut s'applique).
    """

    key = _safe_str(style_key).lower()

    # Pas de style => rien à ajouter
    if not key or key in ("default", "none", "nul", "null"):
        return [], None, None

    if key == "philo":
        # Coins arrondis uniquement (mask PNG + alphamerge), zéro filtre créatif.
        radius = 48  # valeur fixe, comme demandé
        mask_path = _ensure_rounded_mask_png(temp_dir, width, height, radius)

        # On ajoute le masque en input #1 (après la source #0)
        extra_inputs = ["-loop", "1", "-t", f"{need_dur:.3f}", "-i", mask_path]

        # Filtre minimal : crop->scale carré 1080x1080 puis pad 1080x1920,
        # conversion RGBA, alphamerge avec le masque 1080x1920, et sortie YUV420P.
        #
        # NB: On garde crop/scale/pad ici pour garantir le centrage propre même
        # si la pipeline par défaut ne le fait pas quand un style est actif.
        filter_complex = (
            "[0:v]"
            "crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
            f"scale={width}:{width}:flags=bicubic,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            "fps=30,setsar=1,format=rgba[base];"
            "[1:v]format=gray[mask];"
            "[base][mask]alphamerge,format=yuv420p[v]"
        )

        map_label = "[v]"
        return extra_inputs, filter_complex, map_label

    # Styles inconnus => ne rien faire (pas d’échec)
    return [], None, None


def _ensure_rounded_mask_png(temp_dir: str, width: int, height: int, radius: int) -> str:
    """
    Crée (si besoin) un PNG 1080x1920 avec coins arrondis en zone blanche et
    coins transparents (alpha). C'est très rapide et fait une seule fois.
    """
    os.makedirs(temp_dir, exist_ok=True)
    fname = f"rounded_mask_{width}x{height}_r{radius}.png"
    path = os.path.join(temp_dir, fname)

    if os.path.exists(path):
        return path

    if Image is None or ImageDraw is None:
        # Fallback ultra simple : masque blanc plein (pas d'arrondi si Pillow absent)
        # évite de crasher, mais coins non arrondis.
        # Sur Render, Pillow est installé, donc ce fallback ne devrait pas servir.
        with open(path, "wb") as f:
            # petit PNG 1×1 blanc upscalé par ffmpeg si jamais
            f.write(
                bytes.fromhex(
                    "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
                    "0000000A49444154789C6300010000050001A0A25D0000000049454E44AE426082"
                )
            )
        return path

    # Crée une image RGBA transparente
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rectangle arrondi blanc (opaque) centré plein cadre
    # (on ne remet pas d'offsets internes pour rester plein écran et simple)
    rect = (0, 0, width, height)
    try:
        draw.rounded_rectangle(rect, radius=radius, fill=(255, 255, 255, 255))
    except Exception:
        # Pour vieilles versions de Pillow sans rounded_rectangle :
        _rounded_rect_manual(draw, rect, radius, fill=(255, 255, 255, 255))

    img.save(path, "PNG")
    return path


def _rounded_rect_manual(draw: "ImageDraw.ImageDraw", rect, r: int, fill):
    """
    Implémentation de secours si ImageDraw.rounded_rectangle n’existe pas.
    """
    x0, y0, x1, y1 = rect
    w = x1 - x0
    h = y1 - y0
    r = max(0, min(r, min(w, h) // 2))

    # centre + bras horizontaux/verticaux
    draw.rectangle((x0 + r, y0, x1 - r, y1), fill=fill)
    draw.rectangle((x0, y0 + r, x1, y1 - r), fill=fill)

    # 4 quarts de cercle
    draw.pieslice((x0, y0, x0 + 2 * r, y0 + 2 * r), 180, 270, fill=fill)
    draw.pieslice((x1 - 2 * r, y0, x1, y0 + 2 * r), 270, 360, fill=fill)
    draw.pieslice((x0, y1 - 2 * r, x0 + 2 * r, y1), 90, 180, fill=fill)
    draw.pieslice((x1 - 2 * r, y1 - 2 * r, x1, y1), 0, 90, fill=fill)
