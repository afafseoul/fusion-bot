# captions.py — SRT -> ASS avec "pop" mot-par-mot façon CapCut (sans SRT mots)
# - La phrase entière reste visible (blanc + contour noir)
# - Chaque mot apparaît en surbrillance (jaune) avec un effet d'agrandissement (pop)
# - Timing: durée de la phrase répartie uniformément entre les mots

from dataclasses import dataclass
import re, html

@dataclass
class CapStyle:
    name: str
    font: str = "DejaVu Sans"
    size: int = 92            # gros
    outline: int = 7          # contour épais (pseudo-box)
    shadow: int = 0
    align: int = 5            # 5 = centre (milieu)
    margin_v: int = 0
    primary: str = "&H00FFFFFF&"   # BLANC (phrase de base)
    active_fill: str = "&H0000FFFF&"  # JAUNE (mot actif)
    outline_col: str = "&H00101010&"  # contour sombre
    back:    str = "&H00000000&"      # inutilisé (pas de box opaque)

PRESETS = {
    "default": CapStyle(name="default"),
}

ASS_HEADER_TMPL = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
; Primary = blanc, Outline = sombre. Secondary n'est pas utilisé ici.
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: {name},{font},{size},{primary},&H00FFFFFF&,{outline_col},{back},-1,0,0,0,100,100,0,0,1,{outline},{shadow},{align},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Text
"""

def _ass_time(t: float) -> str:
    h = int(t // 3600); t -= 3600*h
    m = int(t // 60);   t -= 60*m
    s = int(t)
    cs = int(round((t - s)*100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def _srt_blocks(srt_text: str):
    return re.split(r"\n\s*\n", srt_text.strip(), flags=re.MULTILINE)

def _parse_times(block: str):
    m = re.search(
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})",
        block
    )
    if not m:
        return None, None
    hh1,mm1,ss1,ms1, hh2,mm2,ss2,ms2 = m.groups()
    start = int(hh1)*3600 + int(mm1)*60 + int(ss1) + int(ms1)/1000.0
    end   = int(hh2)*3600 + int(mm2)*60 + int(ss2) + int(ms2)/1000.0
    return start, end

def _clean_text_lines(block: str):
    lines = block.strip().splitlines()
    if lines and lines[0].strip().isdigit():
        lines = lines[1:]
    if lines and re.search(r"-->", lines[0]):
        lines = lines[1:]
    text = " ".join(lines).strip()
    text = html.unescape(text).replace("{", r"\{").replace("}", r"\}")
    return text

# --------- Génération des lignes ---------

def _base_sentence_line(text: str, start: float, end: float, st: CapStyle) -> str:
    # Phrase entière en blanc, contour noir — toujours visible
    return (
        f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{st.name},"
        f"{{\\an{st.align}\\1c{st.primary}\\3c{st.outline_col}\\bord{st.outline}\\fs{st.size}}}{text}"
    )

def _overlay_word_line(words, i, start, end, st: CapStyle) -> str:
    """
    Crée une ligne overlay qui affiche SEULEMENT le mot i :
      - les autres mots sont masqués via alpha.
      - le mot i est jaune + 'pop' (zoom in/out) avec \\t.
    """
    n = max(1, len(words))
    dur = max(0.05, end - start)
    w_start = start + (i * dur) / n
    w_end   = start + ((i + 1) * dur) / n

    # timing d'animation en ms (court "pop")
    total_ms = int((w_end - w_start) * 1000)
    in_ms = min(120, total_ms // 3)
    out_ms = min(120, total_ms // 3)
    steady_ms = max(0, total_ms - in_ms - out_ms)

    # construction du texte : mots non-actifs alpha 100%, mot actif alpha 0 + couleur + pop
    parts = []
    for j, w in enumerate(words):
        if j == i:
            # mot visible + surligné, zoom (115%) au début puis retour à 100%
            parts.append(
                "{\\alpha&H00&\\1c" + st.active_fill +
                f"\\3c{st.outline_col}\\bord{max(2, st.outline)}"
                "\\t(0," + str(in_ms) + ",\\fscx115\\fscy115)" +
                ("\\t(" + str(in_ms + steady_ms) + "," + str(in_ms + steady_ms + out_ms) + ",\\fscx100\\fscy100)" if out_ms>0 else "") +
                "}" + w
            )
        else:
            parts.append("{\\alpha&HFF&}" + w)
        if j < n - 1:
            parts.append(" ")

    text = "".join(parts)
    return (
        f"Dialogue: 1,{_ass_time(w_start)},{_ass_time(w_end)},{st.name},"
        f"{{\\an{st.align}\\fs{st.size}}}{text}"
    )

def build_ass_from_srt(srt_text: str, preset: str = "default") -> str:
    st = PRESETS.get((preset or "default").strip().lower(), PRESETS["default"])
    header = ASS_HEADER_TMPL.format(
        name=st.name, font=st.font, size=st.size,
        primary=st.primary, outline_col=st.outline_col, back=st.back,
        outline=st.outline, shadow=st.shadow, align=st.align, margin_v=st.margin_v
    )

    events = []
    for block in _srt_blocks(srt_text):
        start, end = _parse_times(block)
        if start is None:
            continue
        sentence = _clean_text_lines(block)
        if not sentence:
            continue

        words = re.findall(r"\S+", sentence)

        # 1) phrase de base (blanc)
        events.append(_base_sentence_line(sentence, start, end, st))

        # 2) overlays mot par mot (jaune + pop)
        for i in range(len(words)):
            events.append(_overlay_word_line(words, i, start, end, st))

    return header + "\n".join(events) + "\n"
