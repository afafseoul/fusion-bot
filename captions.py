# /opt/fusion-bot/captions.py
# srt_text = JSON complet (avec "words":[...]) OU directement un array words[].

from dataclasses import dataclass
import html, json, ast, re

__all__ = ["build_ass_from_srt"]

@dataclass
class CapStyle:
    name: str = "default"
    # Police type "CapCut"
    font: str = "Arial Black"
    # Avant on était à ~240 -> on réduit d’environ 40 %
    size: int = 144
    outline: int = 10
    shadow: int = 0
    align: int = 5           # centre
    margin_v: int = 0
    primary: str = "&H0000FFFF&"        # jaune (BGR)
    back:    str = "&H00000000&"

STYLE = CapStyle()

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
; Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: {STYLE.name},{STYLE.font},{STYLE.size},{STYLE.primary},&H00000000,{STYLE.back},-1,0,0,0,100,100,0,0,1,{STYLE.outline},{STYLE.shadow},{STYLE.align},60,60,{STYLE.margin_v},1

[Events]
Format: Layer, Start, End, Style, Text
"""

def _ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
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
    for w in arr:
        try:
            word = str(w.get("word", "")).strip()
            st   = float(w.get("start", 0.0))
            en   = float(w.get("end",   st + 0.05))
            if not word:
                continue
            if en <= st:
                en = st + 0.05
            out.append({"word": word, "start": st, "end": en})
        except Exception:
            continue
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out

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

    # 2) regexp tolérante
    pairs = re.findall(
        r"word['\"]?\s*:\s*['\"]([^'^\"]+)['\"].*?start['\"]?\s*:\s*([0-9.]+).*?end['\"]?\s*:\s*([0-9.]+)",
        txt, flags=re.I | re.S
    )
    return _clean([{"word": w, "start": float(s), "end": float(e)} for (w, s, e) in pairs])

def build_ass_from_srt(srt_text: str, preset: str = "default") -> str:
    """
    - srt_text = JSON complet (avec "words": [...]) OU array words[].
    - 1 mot à la fois, centré, jaune, anim pop courte.
    """
    words = _parse_words(srt_text or "")
    if not words:
        return ASS_HEADER

    lines = []
    n = len(words)
    pos = r"\pos(540,960)"  # centre 1080x1920

    for i, w in enumerate(words):
        st = w["start"]
        en = w["end"]
        if i < n - 1:
            en = min(en, words[i + 1]["start"] - 0.01)
            if en <= st:
                en = st + 0.03

        text = _escape(w["word"])

        # scale constant pour tous les mots (plus petit qu’avant)
        start_scale = 90    # 0 ms
        peak_scale  = 105   # 0–70 ms
        final_scale = 100   # 70–160 ms

        override = (
            "{\\an5" + pos +
            f"\\fs{STYLE.size}\\bord{STYLE.outline}\\shad{STYLE.shadow}" +
            "\\1c&H00FFFF&\\3c&H000000&" +          # jaune + contour noir
            f"\\fscx{start_scale}\\fscy{start_scale}" +
            f"\\t(0,70,\\fscx{peak_scale}\\fscy{peak_scale})" +
            f"\\t(70,160,\\fscx{final_scale}\\fscy{final_scale})" +
            "}"
        )

        lines.append(
            f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},{STYLE.name},{override}{text}"
        )

    return ASS_HEADER + "\n".join(lines)
