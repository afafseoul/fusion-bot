# captions.py — SRT classique OU JSON { "words": [...] } -> ASS
from dataclasses import dataclass
import re, html, json

@dataclass
class CapStyle:
    name: str
    font: str = "DejaVu Sans"
    size: int = 72           # plus gros
    outline: int = 4
    shadow: int = 2
    align: int = 5           # centre absolu
    margin_v: int = 0
    primary: str = "&H00FFFFFF&"   # blanc (autres mots)
    active:  str = "&H0033CCFF&"   # jaune/orangé (mot courant)
    back:    str = "&H80000000&"   # fond (bordures)

PRESETS = {
    "default": CapStyle(name="default"),
}

ASS_HEADER_TMPL = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: {name},{font},{size},{primary},&H00000000,{back},-1,0,0,0,100,100,0,0,1,{outline},{shadow},{align},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Text
"""

def _ass_time(t: float) -> str:
    t = max(0.0, float(t))
    h = int(t // 3600); t -= 3600*h
    m = int(t // 60);   t -= 60*m
    s = int(t)
    cs = int(round((t - s)*100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def _srt_blocks(srt_text: str):
    return re.split(r"\n\s*\n", srt_text.strip(), flags=re.MULTILINE)

def _escape_ass(s: str) -> str:
    s = html.unescape(str(s))
    return s.replace("{", r"\{").replace("}", r"\}")

# ---------- Mode SRT classique ----------
def srt_to_ass_lines(srt_text: str, style: CapStyle) -> str:
    out = []
    for b in _srt_blocks(srt_text):
        m = re.search(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})", b)
        if not m:
            continue
        hh1,mm1,ss1,ms1, hh2,mm2,ss2,ms2 = m.groups()
        start = int(hh1)*3600+int(mm1)*60+int(ss1)+int(ms1)/1000.0
        end   = int(hh2)*3600+int(mm2)*60+int(ss2)+int(ms2)/1000.0

        lines = [ln for ln in b.strip().splitlines() if ln.strip()]
        # drop index + time line
        if lines and lines[0].strip().isdigit(): lines = lines[1:]
        if lines and "-->" in lines[0]: lines = lines[1:]
        if not lines: continue

        text = _escape_ass(" ".join(lines))
        out.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style.name},{{\\an{style.align}}}{text}")
    return "\n".join(out)

# ---------- Mode JSON "words" (fenêtre glissante + highlight) ----------
def words_json_to_ass(words_json: str, style: CapStyle, window_size: int = 5) -> str:
    try:
        data = json.loads(words_json)
        words = data["words"]
    except Exception:
        # Pas JSON valide -> on retombe en SRT normal
        return srt_to_ass_lines(words_json, style)

    out_lines = []
    n = len(words)
    # normaliser
    seq = []
    for w in words:
        try:
            word = str(w.get("word","")).strip()
            if not word: continue
            start = float(w.get("start"))
            end   = float(w.get("end"))
            if end <= start: end = start + 0.05
            seq.append((word, start, end))
        except Exception:
            continue

    # Pour chaque mot i, on crée un "événement" ASS couvrant [start_i, end_i]
    # Texte = derniers (window_size-1) mots + mot courant, avec le courant en couleur active + gras
    # On évite les retours à la ligne pour limiter les sauts.
    for i, (word, s, e) in enumerate(seq):
        start_i, end_i = s, e
        left = max(0, i - (window_size - 1))
        visible = [w for (w, _, _) in seq[left:i]]   # anciens
        before = " ".join(visible)
        before = _escape_ass(before)

        active = _escape_ass(word)
        # autres (blanc) / actif (jaune, gras)
        txt_parts = []
        if before:
            txt_parts.append(r"{\1c" + style.primary + r"\b0}" + before + " ")
        txt_parts.append(r"{\1c" + style.active + r"\b1}" + active + r"{\b0}")
        # Pas d’anticipation du mot suivant -> moins de jitter

        joined = "".join(txt_parts)
        ass_text = f"{{\\an{style.align}}}{joined}"
        out_lines.append(f"Dialogue: 0,{_ass_time(start_i)},{_ass_time(end_i)},{style.name},{ass_text}")

    return "\n".join(out_lines)

def build_ass_from_srt(srt_or_json: str, preset: str = "default") -> str:
    st = PRESETS.get((preset or "default").strip().lower(), PRESETS["default"])
    header = ASS_HEADER_TMPL.format(
        name=st.name, font=st.font, size=st.size,
        primary=st.primary, back=st.back, outline=st.outline, shadow=st.shadow,
        align=st.align, margin_v=st.margin_v
    )

    # Détection JSON { "words": [...] }
    is_json = False
    try:
        obj = json.loads(srt_or_json)
        is_json = isinstance(obj, dict) and "words" in obj
    except Exception:
        is_json = False

    if is_json:
        body = words_json_to_ass(srt_or_json, st, window_size=5)
    else:
        body = srt_to_ass_lines(srt_or_json, st)

    return header + body
