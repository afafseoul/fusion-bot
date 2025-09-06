# styles.py
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw


def _ensure_mask(path: Path, w: int, h: int, radius: int) -> None:
    """
    Crée un PNG en niveaux de gris (alpha) avec un grand rectangle à coins arrondis
    (blanc = zone visible, noir = coins masqués). On le garde en cache disque.
    """
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    # rectangle arrondi plein
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)
    img.save(path)


def build(
    temp_dir: str,
    style_key: Optional[str],
    dur: float,
    width: int,
    height: int,
    fps: int,
) -> Tuple[List[str], str, str]:
    """
    Retourne (extra_inputs, filter_complex, map_label).

    extra_inputs: liste d'arguments d'entrée supplémentaires pour ffmpeg (ex: le mask PNG)
    filter_complex: la chaîne filter_complex qui mappe vers l'étiquette finale
    map_label: le label vidéo final (ex: 'v')
    """

    # Par défaut: aucun style => on laisse la pipeline "default" du générateur faire le taf,
    # mais on fournit quand même une chaîne simple si l'appelant veut l'utiliser tel quel.
    if not style_key or style_key.lower() in ("default", "none", "nul", "null"):
        # pipeline simple (optionnelle) si tu veux l’utiliser directement :
        # [0:v] crop->scale->pad->fps->yuv420p
        filter_simple = (
            "[0:v]"
            "crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
            f"scale={width}:{width}:flags=bicubic,"  # carré centré
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps},setsar=1,format=yuv420p[v]"
        )
        return [], filter_simple, "v"

    # === STYLE: PHILO (léger & rapide sur Render Free) =========================
    if style_key.lower() == "philo":
        # Coins arrondis: on prépare un mask PNG (une seule fois)
        mask_path = Path(temp_dir) / f"rounded_mask_{width}x{height}_r48.png"
        _ensure_mask(mask_path, width, height, radius=48)

        # Entrée supplémentaire: on boucle le mask à la durée du segment
        extra_inputs = [
            "-loop", "1",
            "-t", f"{dur:.3f}",
            "-i", str(mask_path)
        ]

        # Filter minimal (pas de filtres gourmands):
        # 1) crop/scale en carré (width x width), 2) pad en 1080x1920,
        # 3) fps/setsar, 4) format rgba, 5) alphamerge avec le mask,
        # 6) sortie yuv420p
        filter_complex = (
            "[0:v]"
            "crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
            f"scale={width}:{width}:flags=bicubic,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps},setsar=1,format=rgba[base];"
            "[1:v]format=gray[mask];"
            "[base][mask]alphamerge,format=yuv420p[v]"
        )

        return extra_inputs, filter_complex, "v"

    # === Autres styles (horror, etc.) à ajouter plus tard ======================
    # Pour l’instant, fallback sur la chaîne simple
    filter_simple = (
        "[0:v]"
        "crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
        f"scale={width}:{width}:flags=bicubic,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps},setsar=1,format=yuv420p[v]"
    )
    return [], filter_simple, "v"