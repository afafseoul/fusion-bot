# styles.py — styles FFmpeg (appelés seulement si style != "default")
from typing import Tuple

def philo(need_dur: float, width: int, height: int, fps: int) -> Tuple[str, str, str]:
    """
    Carré centré (~0.78 * min(W,H)), coins arrondis, effet 'film' léger.
    Aucun input supplémentaire côté API : tout est encapsulé ici.
    """
    inner_px  = int(min(width, height) * 0.78)   # ≈842 pour 1080x1920
    inner_px  = max(200, inner_px)
    radius_px = max(8, inner_px // 18)           # ≈48 pour 842
    r = int(radius_px)

    # Effet "film" léger (peu coûteux) : grain + vignette douce + fines scanlines
    film_chain = (
        ",noise=alls=6:allf=t"
        ",vignette=PI/14"
        ",drawgrid=width=2:height=2:thickness=1:color=black@0.06"
    )

    # 1) base : crop carré -> scale -> fps -> setsar
    base = (
        f"[0:v]"
        f"crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
        f"scale={inner_px}:{inner_px}:flags=bicubic,"
        f"fps={fps},setsar=1[base]"
    )

    # 2) masque arrondi : on crée un patch blanc, on force RGBA, puis on dessine l'alpha via geq (a=...)
    #    (IMPORTANT: format=rgba AVANT geq, et utiliser r/g/b/a plutôt que 'alpha=')
    alpha_expr = (
        f"if(between(X,{r},W-{r})*between(Y,{r},H-{r})"
        f"+lte(hypot(X-{r},Y-{r}),{r})"
        f"+lte(hypot(X-(W-{r}),Y-{r}),{r})"
        f"+lte(hypot(X-{r},Y-(H-{r})),{r})"
        f"+lte(hypot(X-(W-{r}),Y-(H-{r})),{r}),255,0)"
    )

    fc = (
        f"{base};"
        # entrée 1 = patch blanc -> format RGBA -> geq écrit l’ALPHA seulement
        f"[1:v]format=rgba,geq=r=0:g=0:b=0:a='{alpha_expr}'[mask];"
        # base -> RGBA pour l’alpha merge
        f"[base]format=rgba[b_rgba];"
        # fusion alpha
        f"[b_rgba][mask]alphamerge[rounded];"
        # pad sur 1080x1920 puis effet film léger et format final yuv420p
        f"[rounded]pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        f"{film_chain},format=yuv420p[v]"
    )

    # 2e entrée (patch blanc pour le masque)
    extra_inputs = f"-f lavfi -t {need_dur:.3f} -i color=c=white@1.0:s={inner_px}x{inner_px}"
    return extra_inputs, fc, "[v]"

# Registry
_REGISTRY = { "philo": philo }

def build(style_key: str, need_dur: float, width: int, height: int, fps: int) -> Tuple[str, str, str]:
    key = (style_key or "default").lower().strip()
    fn = _REGISTRY.get(key)
    if not fn:
        raise ValueError(f"Unknown style: {key}")
    return fn(need_dur, width, height, fps)
