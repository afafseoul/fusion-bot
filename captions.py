# captions.py — INPUT: srt_text = array "brut" words[] (Make)
# words[] = [{ "word": "as", "start": 3.12, "end": 3.40 }, ...]
from dataclasses import dataclass
import html, json, ast, re

@dataclass
class CapStyle:
    name: str = "default"
    font: str = "DejaVu Sans"
    size: int = 64
    outline: int = 4
    shadow: int = 2
    align: int = 2           # 2 = bas centre
    margin_v: int = 120
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

def _parse_words(payload: str):
    """payload = string de l'array words[] venu de Make (JSON ou repr Python)."""
    if not payload: return []
    txt = payload.strip()
    # 1) JSON strict
    try:
        obj = json.loads(txt)
        if isinstance(obj, list): return _clean(obj)
    except Exception:
        pass
    # 2) literal_eval (repr Python typique de Make)
    try:
        obj = ast.literal_eval(txt)
        if isinstance(obj, list): return _clean(obj)
    except Exception:
        pass
    # 3) Tolérance minimale (sécurité)
    pairs = re.findall(r"word['\"]?\s*:\s*['\"]([^'^\"]+)['\"].*?start['\"]?\s*:\s*([0-9.]+).*?end['\"]?\s*:\s*([0-9.]+)",
                       txt, flags=re.I|re.S)
    return _clean([{"word":w, "start":float(s), "end":float(e)} for (w,s,e) in pairs])

def _clean(arr):
    out = []
    for w in arr:
        try:
            word = str(w.get("word","")).strip()
            st   = float(w.get("start", 0.0))
            en   = float(w.get("end",   st + 0.05))
            if not word: continue
            if en <= st: en = st + 0.05
            out.append({"word": word, "start": st, "end": en})
        except Exception:
            continue
    # tri défensif
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out

def _window_line(words, i, window=5):
    a = max(0, i - (window - 1))
    chunk = []
    for j, w in enumerate(words[a:i+1]):
        tok = _escape(w["word"])
        if j == len(words[a:i+1]) - 1:    # mot courant
            chunk.append(f"{{\\c{STYLE.active}\\b1}}{tok}{{\\c{STYLE.primary}\\b0}}")
        else:
            chunk.append(tok)
    return " ".join(chunk)

def build_ass_from_srt(srt_text: str, preset: str = "default") -> str:
    """
    Signature conservée pour main.py.
    Interprète srt_text comme ARRAY words[] (pas de SRT).
    """
    words = _parse_words(srt_text or "")
    if not words:
        return ASS_HEADER  # pas de sous-titres si vide

    # crée une ligne par mot, sans chevauchement visuel
    lines = []
    n = len(words)
    for i, w in enumerate(words):
        st = w["start"]
        # borne l'affichage à juste avant le mot suivant (évite les empilements)
        if i < n - 1:
            en = min(w["end"], words[i+1]["start"] - 0.01)
            if en <= st: en = st + 0.03
        else:
            en = w["end"]

        text = _window_line(words, i, window=5)
        lines.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},{STYLE.name},{{\\an{STYLE.align}}}{text}")

    return ASS_HEADER + "\n".join(lines)
