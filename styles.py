# styles.py — Définition des styles vidéo (léger et stable)

def vf_for_style(width: int, height: int, fps: int, style_key: str) -> str:
    """
    Retourne la chaîne -vf adaptée au style choisi.

    - default : plein cadre classique (scale+pad)
    - philo   : carré centré (crop au centre), resize doux, puis pad vertical.
                → Pas d’arrondis ici pour garder la perf/fiabilité.
    """
    sk = (style_key or "default").lower().strip()

    if sk == "philo":
        # carré interne ~78% du côté le plus petit (≈842 quand 1080x1920)
        inner = int(min(width, height) * 0.78)
        return (
            "crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
            f"scale={inner}:{inner}:flags=bicubic,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps}"
        )

    # default
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps}"
    )
