# /opt/fusion-bot/captions.py
# srt_text = JSON complet (avec "words":[...]) OU juste un array words[].
# Génère un .ass avec un mot à la fois, centré, jaune.

from dataclasses import dataclass
import html, json, ast, re

__all__ = ["build_ass_from_srt"]

@dataclass
class CapStyle:
    name: str = "default"
    # garde une police sûre côté serveur ; tu pourras changer le nom si tu as une autre font installée
    font: str = "DejaVu Sans"
    # taille globale (≈ 40% plus petit que ton ancien 64)
    size: int = 40
    outline: int = 4
    shadow: int = 2
    # 5 = centre ; (2 = bas centre que tu avais)
    align: int = 5           # milieu-centre
    margin_v: int = 0        # 0 => vraiment au centre vertical
    primary: str = "&H00000000&"   # noir (texte)
    active:  str = "&H0000FFFF&"   # jaune (non utilisé ici car un seul mot)
    back:    str = "&H80FFFF00&"   # fond jaune semi-transparent

STYLE = CapStyle()

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: {STYLE.name},{STYLE.font},{STYLE.size},{STYLE.primary},&H00000000,{STYLE.back},-1,0,0,0,100,100,0,0,3,{STYLE.outline},{STYLE.shadow},{STYLE.align},60,60,{STYLE.margin_v},1

[Events]
Format: Layer, Start, End, Style, Text
"""

# ------------ utils temps & parsing ------------

def _ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600); t -= 3600 * h
    m = int(t // 60);   t -= 60 * m
    s = int(t)
    cs = int(round((t - s) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def _escape(text: str) -> str:
    text = html.unescape(text or "")
    return text.replace("{", r"\{").replace("}", r"\}")

def _parse_words(payload: str):
    """
    payload = string venant de Make.
    - Peut être le JSON complet: {"task": "...", "words":[...], ...}
    - Ou directement l'array words[] (JSON ou repr Python).
    """
    if not payload:
        return []

    txt = payload.strip()

    # 0) JSON complet
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict) and isinstance(obj.get("words"), list):
            return _clean(obj["words"])
        if isinstance(obj, list):
            return _clean(obj)
    except Exception:
        pass

    # 1) repr Python
    try:
        obj = ast.literal_eval(txt)
        if isinstance(obj, dict) and isinstance(obj.get("words"), list):
            return _clean(obj["words"])
        if isinstance(obj, list):
            return _clean(obj)
    except Exception:
        pass

    # 2) Regex tolérante
    pairs = re.findall(
        r"word['\"]?\s*:\s*['\"]([^'^\"]+)['\"].*?start['\"]?\s*:\s*([0-9.]+).*?end['\"]?\s*:\s*([0-9.]+)",
        txt, flags=re.I | re.S
    )
    return _clean([{"word": w, "start": float(s), "end": float(e)} for (w, s, e) in pairs])

def _clean(arr):
    """
    Normalise + répare les timings pour avoir:
      - ordre correct
      - durée mini par mot
      - pas de recouvrement hardcore
    """
    raw = []
    for w in arr:
        try:
            word = str(w.get("word", "")).strip()
            st = float(w.get("start", 0.0))
            en = float(w.get("end", st))
            raw.append({"word": word, "start": st, "end": en})
        except Exception:
            continue

    # filtre les vides
    raw = [w for w in raw if w["word"]]
    if not raw:
        return []

    # tri par start
    raw.sort(key=lambda x: (x["start"], x["end"]))

    # paramètres de smoothing
    MIN_DUR = 0.16   # durée min "confortable" ~ 160 ms
    MIN_GAP = 0.01   # petit gap entre deux mots

    # 1ère passe : garantir ordre & gap
    for i, w in enumerate(raw):
        if i == 0:
            if w["end"] <= w["start"]:
                w["end"] = w["start"] + MIN_DUR
            continue

        prev = raw[i - 1]

        # start ne doit pas revenir en arrière
        if w["start"] < prev["start"]:
            w["start"] = prev["start"]

        # évite chevauchement violent
        if w["start"] < prev["end"] + MIN_GAP:
            w["start"] = prev["end"] + MIN_GAP

        if w["end"] <= w["start"]:
            w["end"] = w["start"] + MIN_DUR

    # 2ème passe : durée minimale par mot,
    # sans décaler les starts suivants (on borne avec le start du mot suivant)
    n = len(raw)
    for i, w in enumerate(raw):
        desired_end = w["start"] + MIN_DUR
        if w["end"] >= desired_end:
            continue  # déjà assez long

        if i < n - 1:
            next_start = raw[i + 1]["start"]
            # on garde un petit gap avant le mot suivant
            max_end = max(w["start"] + 0.05, next_start - MIN_GAP)
            w["end"] = min(desired_end, max_end)
            # si malgré tout ça reste trop court (phrase super serrée),
            # on laisse au moins quelque chose de visible
            if w["end"] <= w["start"]:
                w["end"] = w["start"] + MIN_DUR * 0.6
        else:
            # dernier mot : on peut l'étendre librement
            w["end"] = desired_end

    return raw

# ------------ génération ASS ------------

def build_ass_from_srt(srt_text: str, preset: str = "default") -> str:
    """
    API attendue par main.py
    - srt_text = JSON complet (avec "words": [...]) OU array words[].
    - Retourne un .ass : un mot à la fois, centré, fond jaune.
    """
    words = _parse_words(srt_text or "")
    if not words:
        return ASS_HEADER

    lines = []
    for w in words:
        st = w["start"]
        en = w["end"]
        tok = _escape(w["word"])

        # style "pancarte" : fond jaune + texte noir, centré
        ass_text = (
            "{\\an" + str(STYLE.align) +
            "\\bord" + str(STYLE.outline) +
            "\\shad" + str(STYLE.shadow) +
            "}" + tok
        )

        line = f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},{STYLE.name},,{ass_text}"
        lines.append(line)

    return ASS_HEADER + "\n".join(lines)
