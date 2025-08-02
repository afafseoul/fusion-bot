from moviepy.editor import TextClip

def generate_text_overlay(text, duration, video_size):
    w, h = video_size
    subtitle = TextClip(
    
        text,
        fontsize=42,
        font="Arial-Bold",
        color="white",
        bg_color="rgba(0,0,0,0.6)",
        method="caption",
        size=(w * 0.9, None),
    ).set_position(("center", h - 100)).set_duration(duration)

    return subtitle
