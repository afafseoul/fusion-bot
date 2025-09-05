# styles.py
# Construire les filtres en fonction d'un style choisi.
# Retourne (extra_inputs: list[str], filter_complex: str, map_label: str)

from typing import List, Tuple

def _base_chain(label_in: str, out_w: int, out_h: int, fps: int) -> str:
    # Chaîne "par défaut" ultra simple et rapide : crop carré centré, scale,
    # fps, pad au format final, yuv420p.
    return (
        f"[{label_in}]"
        f"crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
        f"scale={min(out_w, out_h)}:{min(out_w, out_h)}:flags=bicubic,"
        f"fps={fps},setsar=1,"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"format=yuv420p[v]"
    )

def _philo_chain(label_vid: str, label_mask: str, inner: int, out_w: int, out_h: int, fps: int) -> str:
    # Philo léger et rapide :
    # - on prépare la vidéo en RGBA
    # - on merge alpha avec un masque à coins arrondis
    # - on pad, + légère vignette + légère correction (contraste/sat) -> look old-school sans coût lourd
    return (
        f"[{label_vid}]"
        f"crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
        f"scale={inner}:{inner}:flags=bicubic,fps={fps},setsar=1,format=rgba[base];"
        f"[{label_mask}]format=rgba[mask];"
        f"[base][mask]alphamerge[rounded];"
        f"[rounded]"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"vignette=PI/16,"
        f"eq=contrast=1.05:saturation=0.95,"
        f"format=yuv420p[v]"
    )

def build(style_key: str, dur: float, out_w: int, out_h: int, fps: int, temp_dir: str) -> Tuple[List[str], str, str]:
    """
    style_key: "default" | "philo" | ...
    dur: durée du segment (s)
    out_w/out_h: dimensions de sortie (ex: 1080x1920)
    fps: fps de sortie
    temp_dir: présent pour compat avec l'appelant (non utilisé ici)
    """
    # Normaliser style_key
    key = (style_key or "default").strip().lower()

    if key in ("", "default", "none", "defaut", "défaut"):
        # Aucun style : chaîne rapide
        fc = _base_chain("0:v", out_w, out_h, fps)
        return ([], fc, "v")

    if key == "philo":
        # Tailles par défaut rapides et jolies (comme demandé)
        inner = int(min(out_w, out_h) * 0.78)  # ≈ 842 pour 1080x1920
        radius = max(12, inner // 18)          # ≈ 46 pour 842

        # On génère un masque lavfi (très rapide) : un rectangle arrondi rempli en blanc sur fond transparent
        # color=black@0 -> transparent ; drawbox avec r=<radius> remplit en blanc la zone arrondie.
        mask_lavfi = (
            f"color=c=black@0.0:s={inner}x{inner},"
            f"format=rgba,"
            f"drawbox=x=0:y=0:w={inner}:h={inner}:color=white@1.0:t=fill:r={radius}"
        )

        extra_inputs = [
            "-f", "lavfi", "-t", f"{dur:.3f}", "-i", mask_lavfi
        ]
        # [0:v] = vidéo source, [1:v] = masque
        fc = _philo_chain("0:v", "1:v", inner, out_w, out_h, fps)
        return (extra_inputs, fc, "v")

    # Fallback : si style inconnu, on tombe sur default rapide
    fc = _base_chain("0:v", out_w, out_h, fps)
    return ([], fc, "v")
