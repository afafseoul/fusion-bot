# captions.py — SRT -> ASS
# - style "default": gros sous-titres type TikTok (blanc, outline épais), centrés au milieu
# - karaoké mot-par-mot: durée de la phrase répartie uniformément entre les mots

from dataclasses import dataclass
import re, html

@dataclass
class CapStyle:
    name: str
    font: str = "DejaVu Sans"
    size: int = 80            # ++ plus gros
    outline: int = 6          # bordure épaisse
    shadow: int = 0
    align: int = 5            # 5 = centre (milieu de l’écran)
    margin_v: int = 0
    primary: str = "&H00FFFFFF&"   # blanc (texte normal)
    secondary: str = "&H0000FFFF&" # jaune (highlight)
    back:    str = "&H80000000&"   # fond semi-transparent (utile si tu veux un box effect plus tard)

PRESETS = {
    # Tu peux créer d'autres styles ici (ex. "tiktok_bold", "white_box", etc.)
    "default": CapStyle(name="default"),
}

# IMPORTANT: on met bien SecondaryColour pour le karaoké
ASS_HEADER_TMPL = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: {name},{font},{size},{primary},{secondary},&H00000000,{back},-1,0,0,0,100,100,0,0,1,{outline},{shadow},{align},60,60,{margin_v},1

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
    # blocs SRT séparés par des lignes vides
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
    # 1ère ligne = index ?
    if lines and lines[0].strip().isdigit():
        lines = lines[1:]
    # 2e ligne = timecode ?
    if lines and re.search(r"-->", lines[0]):
        lines = lines[1:]
    text = " ".join(lines).strip()
    text = html.unescape(text).replace("{", r"\{").replace("}", r"\}")
    return text

def _karaoke_line(text: str, start: float, end: float, align: int) -> str:
    """
    Construit une ligne ASS avec highlight mot-par-mot.
    Stratégie simple: on répartit uniformément la durée sur le nombre de mots.
    - On utilise les tags {\k<cs>} (centi-secondes) pour séquencer les mots.
    - Le style SecondaryColour sera utilisé par le moteur karaoké pendant la progression.
    Remarque: libass anime le "remplissage" gauche->droite; visuellement on obtient
    un highlight progressif mot-à-mot sur la durée indiquée.
    """
    words = re.findall(r"\S+", text)
    dur = max(0.01, end - start)
    n = max(1, len(words))
    per_cs = max(1, int(round((dur / n) * 100)))  # centi-secondes par mot

    seq = []
    # Au début on force \k0 pour bien initialiser, puis on enchaîne {\kX}mot
    seq.append(r"{\k0}")
    for i, w in enumerate(words):
        seq.append(rf"{{\k{per_cs}}}{w}")
        if i < n - 1:
            seq.append(" ")  # conserver les espaces visuels

    # \an<align> positionne au centre (5 = milieu)
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},default,{{\\an{align}}}{''.join(seq)}"

def srt_to_ass_lines_karaoke(srt_text: str, style: CapStyle) -> str:
    out = []
    for b in _srt_blocks(srt_text):
        start, end = _parse_times(b)
        if start is None:
            continue
        text = _clean_text_lines(b)
        if not text:
            continue
        out.append(_karaoke_line(text, start, end, style.align))
    return "\n".join(out)

def build_ass_from_srt(srt_text: str, preset: str = "default") -> str:
    st = PRESETS.get((preset or "default").strip().lower(), PRESETS["default"])
    header = ASS_HEADER_TMPL.format(
        name=st.name, font=st.font, size=st.size,
        primary=st.primary, secondary=st.secondary, back=st.back,
        outline=st.outline, shadow=st.shadow, align=st.align, margin_v=st.margin_v
    )
    # on génère la version karaoké (mot-par-mot, répartition simple)
    return header + srt_to_ass_lines_karaoke(srt_text, st)
