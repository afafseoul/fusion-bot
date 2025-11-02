# captions.py — "one-word" captions centered, big, yellow with black outline
from dataclasses import dataclass
import html, json, ast, re

@dataclass
class CapStyle:
    name: str = "default"
    font: str = "DejaVu Sans Bold"   # gras par défaut
    size: int = 140                  # taille bien grande pour 1080x1920
    outline: int = 8                 # gros contour
    shadow: int = 3
    align: int = 5                   # 5 = milieu centre
    margin_v: int = 0                # pas de marge verticale (plein centre)
    primary: str = "&H0000FFFF&"     # JAUNE (B,G,R,00) -> &H00BBGGRR&
    back:    str = "&H00000000&"     # contour noir (via OutlineColour)
    outline_colour: str = "&H00000000&"  # noir

STYLE = CapStyle()

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
; Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: {STYLE.name},{STYLE.font},{STYLE.size},{STYLE.primary},&H00000000,{STYLE.outline_colour},{STYLE.back},-1,0,0,0,100,100,0,0,1,{STYLE.outline},{STYLE.shadow},{STYLE.align},60,60,{STYLE.margin_v},1

[Events]
Format: Layer, Start, End, Style, Text
"""

def _ass_time(t: float) -> str:
    if t < 0: t = 0.0
    h = int(t // 3600); t -= 3600*h
    m = int(t // 60);   t -= 60*m
    s = int(t)
    cs = int(round((t - s) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def _escape(text: str) -> str:
    text = html.unescape(text or "")
    return text.replace("{", r"\{").replace("}", r"\}")

# --- Input parsing ---------------------------------------------------
def _parse_words_payload(payload: str):
    """
    Accepte:
      - l'ARRAY 'words' sérialisé (JSON ou repr Python)
      - ou le JSON complet { task, language, duration, text, words: [...] }
    """
    if not payload: return []
    txt = payload.strip()

    # 1) JSON strict
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict) and isinstance(obj.get("words"), list):
            return _clean(obj["words"])
        if isinstance(obj, list):
            return _clean(obj)
    except Exception:
        pass

    # 2) literal_eval (repr Python Make)
    try:
        obj = ast.literal_eval(txt)
        if isinstance(obj, dict) and isinstance(obj.get("words"), list):
            return _clean(obj["words"])
        if isinstance(obj, list):
            return _clean(obj)
    except Exception:
        pass

    # 3) extraction tolérante
    try:
        # cherche le bloc "words":[ ... ]
        m = re.search(r'"?words"?\s*:\s*(\[[\s\S]*?\])', txt, flags=re.I)
        if m:
            arr = json.loads(m.group(1))
            if isinstance(arr, list):
                return _clean(arr)
    except Exception:
        pass

    # 4) fallback ultra-léger: triplets (word,start,end)
    pairs = re.findall(
        r"word['\"]?\s*:\s*['\"]([^'^\"]+)['\"].*?start['\"]?\s*:\s*([0-9.]+).*?end['\"]?\s*:\s*([0-9.]+)",
        txt, flags=re.I|re.S
    )
    return _clean([{"word": w, "start": float(s), "end": float(e)} for (w, s, e) in pairs])

def _clean(arr):
    out = []
    for w in arr:
        try:
            word = str(w.get("word","")).strip()
            st   = float(w.get("start", 0.0))
            en   = float(w.get("end", st + 0.05))
            if not word: continue
            if en <= st: en = st + 0.05
            out.append({"word": word, "start": st, "end": en})
        except Exception:
            continue
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out
# --------------------------------------------------------------------

def build_ass_from_srt(srt_text: str, preset: str = "default") -> str:
    """
    Utilisée par main.py.
    Ici srt_text contient soit la liste words[], soit le JSON complet (on isole words[]).
    On rend UN mot à la fois, au centre, taille XL, jaune avec contour noir.
    """
    words = _parse_words_payload(srt_text or "")
    if not words:
        return ASS_HEADER  # rien à afficher

    lines = []
    n = len(words)
    for i, w in enumerate(words):
        st = w["start"]
        # couper juste avant le mot suivant pour éviter les superpositions
        if i < n - 1:
            en = min(w["end"], words[i+1]["start"] - 0.01)
            if en <= st: en = st + 0.03
        else:
            en = w["end"]

        text = _escape(w["word"])
        # \an5 est déjà dans le style; on garde le mot seul, en gras via le style.
        lines.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},{STYLE.name},{text}")

    return ASS_HEADER + "\n".join(lines)
