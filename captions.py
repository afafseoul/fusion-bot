# captions.py — build-up par groupes de 5 mots, centré au milieu, texte large.
from dataclasses import dataclass
import html, json, ast, re

@dataclass
class CapStyle:
    name: str = "default"
    font: str = "DejaVu Sans"
    size: int = 96           # plus gros
    outline: int = 6         # contour plus épais
    shadow: int = 2
    align: int = 5           # 5 = milieu centre
    margin_v: int = 40
    primary: str = "&H00FFFFFF&"   # blanc
    active:  str = "&H0000FFFF&"   # jaune
    back:    str = "&H80000000&"

STYLE = CapStyle()

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: {STYLE.name},{STYLE.font},{STYLE.size},{STYLE.primary},&H00000000,{STYLE.back},-1,0,0,0,100,100,0,0,1,{STYLE.outline},{STYLE.shadow},{STYLE.align},60,60,{STYLE.margin_v},1

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

def _clean(arr):
    out = []
    for w in arr or []:
        try:
            word = str(w.get("word","")).strip()
            st   = float(w.get("start", 0.0))
            en   = float(w.get("end",   st + 0.05))
            if not word: continue
            if en <= st: en = st + 0.05
            out.append({"word": word, "start": st, "end": en})
        except Exception:
            continue
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out

def _parse_payload(payload: str):
    """
    Accepte :
      - JSON complet { task, language, text, words:[...] }
      - juste words[] (JSON)
      - repr Python (Make) de words[]
      - tolérance via regex
    Retourne: liste propre de dicts {word,start,end}
    """
    if not payload: return []
    txt = str(payload).strip()

    # 1) JSON strict
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict) and "words" in obj:
            return _clean(obj.get("words"))
        if isinstance(obj, list):
            return _clean(obj)
    except Exception:
        pass

    # 2) literal_eval pour repr Python
    try:
        obj = ast.literal_eval(txt)
        if isinstance(obj, dict) and "words" in obj:
            return _clean(obj.get("words"))
        if isinstance(obj, list):
            return _clean(obj)
    except Exception:
        pass

    # 3) Regex tolérante
    pairs = re.findall(
        r"word['\"]?\s*:\s*['\"]([^'^\"]+)['\"].*?start['\"]?\s*:\s*([0-9.]+).*?end['\"]?\s*:\s*([0-9.]+)",
        txt, flags=re.I|re.S
    )
    return _clean([{"word":w, "start":float(s), "end":float(e)} for (w,s,e) in pairs])

def _build_up_line(words, i, group_size=5):
    """
    Build-up dans un groupe de `group_size` mots.
    i = index global du mot courant.
    Exemple group_size=5:
      0: [w0]
      1: [w0 w1]
      2: [w0 w1 w2]
      3: [w0 w1 w2 w3]
      4: [w0 w1 w2 w3 w4]  -> reset au mot suivant
      5: [w5]
      ...
    Le mot courant est surligné.
    """
    g_idx   = i % group_size
    g_start = i - g_idx
    span    = words[g_start:i+1]

    parts = []
    for k, w in enumerate(span):
        tok = _escape(w["word"])
        if k == len(span) - 1:
            parts.append(f"{{\\c{STYLE.active}\\b1}}{tok}{{\\c{STYLE.primary}\\b0}}")
        else:
            parts.append(tok)
    return " ".join(parts)

def build_ass_from_srt(srt_text: str, preset: str = "default") -> str:
    """
    Même signature que l'ancien code. Ici srt_text peut contenir:
      - JSON complet avec 'words'
      - ou juste l'array words[].
    """
    words = _parse_payload(srt_text or "")
    if not words:
        return ASS_HEADER

    lines = []
    n = len(words)
    for i, w in enumerate(words):
        st = w["start"]
        # borne l'affichage jusqu'au début du mot suivant, pour éviter l'empilement
        if i < n - 1:
            en = min(w["end"], words[i+1]["start"] - 0.01)
            if en <= st: en = st + 0.03
        else:
            en = w["end"]

        text = _build_up_line(words, i, group_size=5)
        # l'alignement principal est dans le style ; on laisse l'override par sécurité
        lines.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},{STYLE.name},{{\\an{STYLE.align}}}{text}")

    return ASS_HEADER + "\n".join(lines)
