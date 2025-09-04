# styles.py — Définitions des styles vidéo

def vf_for_style(width: int, height: int, fps: int, style_key: str) -> str:
    """
    Retourne la chaîne -vf adaptée au style choisi.
    - default : plein cadre classique (comme aujourd’hui)
    - philo   : crop carré centré + padding vertical + coins arrondis
    """
    sk = (style_key or "default").lower().strip()

    if sk == "philo":
        inner = int(min(width, height) * 0.78)  # carré central (~842px pour 1080x1920)

        # ⚠️ nécessite ffmpeg avec le filtre 'curves' ou 'alphamerge' pour arrondir
        # Ici : on applique crop/pad + masque arrondi
        vf = (
            f"scale={inner}:{inner}:force_original_aspect_ratio=decrease,"
            f"pad={inner}:{inner}:(ow-iw)/2:(oh-ih)/2:black,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps},"
            # coins arrondis (radius=120px ici)
            f"format=rgba,geq='if(gt((X-{width}/2)^2+(Y-{height}/2)^2,{(min(width,height)//2-120)**2}),255,0)':128"
        )
        return vf

    # === Default ===
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={fps}"
    )
