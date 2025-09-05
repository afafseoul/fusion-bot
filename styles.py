# styles.py — styles FFmpeg (seulement appelés si style != "default")
from typing import Tuple

def philo(need_dur: float, width: int, height: int, fps: int) -> Tuple[str, str, str]:
    """
    Carré centré ~0.78 * min(1080,1920), coins arrondis, et effet 'film' très léger.
    Tout est interne (aucun input API supplémentaire).
    """
    inner_px  = int(min(width, height) * 0.78)   # ~842 pour 1080x1920
    inner_px  = max(200, inner_px)
    radius_px = max(8, inner_px // 18)           # ~48 pour 842
    r = int(radius_px)

    # Effet "film" très léger (cheap): grain + vignette douce + fines scanlines
    film_chain = (
        ",noise=alls=6:allf=t"
        ",vignette=PI/14"
        ",drawgrid=width=2:height=2:thickness=1:color=black@0.06"
    )

    # Étapes:
    # 1) crop carré -> scale -> fps -> setsar
    base = (
        f"[0:v]"
        f"crop='min(iw,ih)':'min(iw,ih)':'(iw-min(iw,ih))/2':'(ih-min(iw,ih))/2',"
        f"scale={inner_px}:{inner_px}:flags=bicubic,"
        f"fps={fps},setsar=1[base]"
    )

    # 2) fabriquer un masque alpha arrondi (sur un patch blanc taille inner_px)
    alpha_expr = (
        f"if(between(X,{r},W-{r})*between(Y,{r},H-{r})"
        f"+lte(hypot(X-{r},Y-{r}),{r})"
        f"+lte(hypot(X-(W-{r}),Y-{r}),{r})"
        f"+lte(hypot(X-{r},Y-(H-{r})),{r})"
        f"+lte(hypot(X-(W-{r}),Y-(H-{r})),{r}),255,0)"
    )

    fc = (
        f"{base};"
        f"[1:v]geq=lum=0:cb=0:cr=0:alpha='{alpha_expr}',format=rgba[mask];"
        f"[base]format=rgba[b_rgba];[b_rgba][mask]alphamerge[rounded];"
        f"[rounded]pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black{film_chain},"
        f"format=yuv420p[v]"
    )

    # 2e entrée: patch blanc pour le masque (peu coûteux)
    extra_inputs = f"-f lavfi -t {need_dur:.3f} -i color=c=white@1.0:s={inner_px}x{inner_px}"
    return extra_inputs, fc, "[v]"

# Registry / factory
_REGISTRY = { "philo": philo }

def build(style_key: str, need_dur: float, width: int, height: int, fps: int) -> Tuple[str, str, str]:
    key = (style_key or "default").lower().strip()
    fn = _REGISTRY.get(key)
    if not fn:
        # Fallback : si style inconnu, on laissera l'appelant gérer (ne devrait pas arriver).
        raise ValueError(f"Unknown style: {key}")
    return fn(need_dur, width, height, fps)