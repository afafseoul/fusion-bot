# styles.py — styles vidéo quand on réencode tout (scale + fps + pad)

def vf_for_style_full(width: int, height: int, fps: int, style_key: str) -> str:
    """
    Rend la chaîne -vf complète, y compris le fps=.
    - default : letterbox vers {width}x{height}, puis fps={fps}
    - philo   : crop carré centré -> scale (≈78% du côté min) -> pad {width}x{height} -> fps={fps}
    """
    sk = (style_key or "default").lower().strip()

    if sk == "philo":
        inner = int(min(width, height) * 0.78)  # ~842 pour 1080x1920
        return (
            # carré centré depuis la source
            "crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
            # réduction douce du carré pour bien voir l'effet
            f"scale={inner}:{inner}:flags=bicubic,"
            # placement centré en 1080x1920
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            # cadence constante et ratio pixel propre
            f"fps={fps},setsar=1"
        )

    # default: letterbox propre + fps constant
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps},setsar=1"
    )
