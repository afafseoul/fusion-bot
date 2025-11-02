# captions.py — One word at a time, HUGE, dead-center, yellow with black outline.

from dataclasses import dataclass
import html, json, ast, re

@dataclass
class CapStyle:
    name: str = "default"
    font: str = "DejaVu Sans Bold"
    size: int = 220              # très grand pour 1080x1920
    outline: int = 10            # contour épais
    shadow: int = 0              # pas d'ombre
    align: int = 5               # centre (ASS)
    margin_v: int = 0
    primary: str = "&H0000FFFF&"         # jaune  (AA BB GG RR -> 00 00 FF FF)
    outline_colour: str = "&H00000000&"  # noir
    back: str = "&H00000000&"            # non utilisé (BorderStyle 1)

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

# --- Parse srt_text venant de Make (array words[] OU JSON complet) ---
def _parse_words_payload(payload: str):
    if not payload: return []
    txt = (payload or "").strip()

    # JSON strict
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict) and isinstance(obj.get("words"), list):
            return _clean(obj["words"])
        if isinstance(obj, list):
            return _clean(obj)
    except Exception:
        pass

    # literal_eval (repr Python)
    try:
        obj = ast.literal_eval(txt)
        if isinstance(obj, dict) and isinstance(obj.get("words"), list):
            return _clean(obj["words"])
        if isinstance(obj, list):
            return _clean(obj)
    except Exception:
        pass

    # extraction tolérante du bloc "words":[...]
    try:
        m = re.search(r'"?words"?\s*:\s*(\[[\s\S]*?\])', txt, flags=re.I)
        if m:
            arr = json.loads(m.group(1))
            if isinstance(arr, list):
                return _clean(arr)
    except Exception:
        pass

    # fallback: triplets word/start/end
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
    Utilisé par main.py. Rend **un seul mot** au centre, énorme.
    srt_text = array words[] ou JSON complet contenant 'words'.
    """
    words = _parse_words_payload(srt_text or "")
    if not words:
        return ASS_HEADER

    lines = []
    n = len(words)
    for i, w in enumerate(words):
        st = w["start"]
        en = w["end"]
        if i < n - 1:
            en = min(en, words[i+1]["start"] - 0.01)
            if en <= st: en = st + 0.03

        text = _escape(w["word"])

        # On force position et style au runtime pour être SÛR du centre et de la taille
        # \pos(540,960) = centre exact pour 1080x1920
        # \fs = taille police, \bord = contour, \shad = ombre (0)
        override = f"{{\\an5\\pos(540,960)\\fs{STYLE.size}\\bord{STYLE.outline}\\shad{STYLE.shadow}}}"
        lines.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},{STYLE.name},{override}{text}")

    return ASS_HEADER + "\n".join(lines)
