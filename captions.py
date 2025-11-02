# captions.py — SRT -> ASS (style 'default')
from dataclasses import dataclass
import re, html

@dataclass
class CapStyle:
    name: str
    font: str = "DejaVu Sans"
    size: int = 54
    outline: int = 3
    shadow: int = 1
    align: int = 5              # 5 = plein centre (au milieu)
    margin_v: int = 0
    primary: str = "&H00FFFFFF&"   # blanc
    active:  str = "&H0000FFFF&"   # jaune (mot courant)
    back:    str = "&H80000000&"

PRESETS = {
    "default": CapStyle(name="default", align=5, margin_v=0),
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
    h = int(t // 3600); t -= 3600*h
    m = int(t // 60);   t -= 60*m
    s = int(t)
    cs = int(round((t - s)*100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def _srt_blocks(srt_text: str):
    return re.split(r"\n\s*\n", srt_text.strip(), flags=re.MULTILINE)

def srt_to_ass_lines(srt_text: str, style: CapStyle) -> str:
    out = []
    for b in _srt_blocks(srt_text):
        m = re.search(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})", b)
        if not m:
            continue
        hh1,mm1,ss1,ms1, hh2,mm2,ss2,ms2 = m.groups()
        start = int(hh1)*3600+int(mm1)*60+int(ss1)+int(ms1)/1000.0
        end   = int(hh2)*3600+int(mm2)*60+int(ss2)+int(ms2)/1000.0

        lines = b.strip().splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if lines and re.search(r"-->", lines[0]):
            lines = lines[1:]
        if not lines:
            continue
        text = " ".join(lines)
        text = html.unescape(text).replace("{", r"\{").replace("}", r"\}")

        out.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style.name},{{\\an{style.align}}}{text}")
    return "\n".join(out)

def build_ass_from_srt(srt_text: str, preset: str = "default") -> str:
    st = PRESETS.get((preset or "default").strip().lower(), PRESETS["default"])
    header = ASS_HEADER_TMPL.format(
        name=st.name, font=st.font, size=st.size,
        primary=st.primary, back=st.back, outline=st.outline, shadow=st.shadow,
        align=st.align, margin_v=st.margin_v
    )
    return header + srt_to_ass_lines(srt_text, st)
