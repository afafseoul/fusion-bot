# video_generator.py
import os, math, json, shutil, subprocess, tempfile, re
from urllib.request import urlopen

def _run(cmd, logger, req_id):
    logger.info(f"[{req_id}] CMD: {' '.join(str(c) for c in cmd)}")
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.stderr:
        logger.info(f"[{req_id}] STDERR: {p.stderr[:1200]}")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg/ffprobe failed ({p.returncode})")
    return p

def _probe(path):
    try:
        out = subprocess.check_output([
            "ffprobe","-v","error",
            "-select_streams","v:0",
            "-show_entries","stream=r_frame_rate,width,height:format=duration",
            "-of","json", path
        ], stderr=subprocess.STDOUT).decode("utf-8","ignore")
        j = json.loads(out)
        dur = float(j.get("format", {}).get("duration", 0) or 0)
        st  = (j.get("streams") or [{}])[0]
        fr  = st.get("r_frame_rate") or "0/1"
        try:
            num, den = fr.split("/")
            fps = float(num) / float(den) if float(den) else 0.0
        except Exception:
            fps = 0.0
        return {
            "duration": max(0.0, dur),
            "width": int(st.get("width") or 0),
            "height": int(st.get("height") or 0),
            "fps": fps
        }
    except Exception:
        return {"duration":0.0, "width":0, "height":0, "fps":0.0}

def _download(url, dst, logger, req_id):
    with urlopen(url) as r, open(dst, "wb") as f:
        data = r.read()
        f.write(data)
    size = os.path.getsize(dst)
    logger.info(f"[{req_id}] download ok -> {dst} size={size}B")
    return dst

def _write_filelist(paths, list_path):
    with open(list_path, "w", encoding="utf-8") as f:
        for p in paths:
            # concat demuxer: each path must be quoted
            f.write(f"file '{p}'\n")

def _build_srt(plan, out_path, global_srt=None):
    if global_srt:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(global_srt)
        return

    idx = 1
    with open(out_path, "w", encoding="utf-8") as f:
        for seg in plan:
            text = seg.get("text") or ""
            subs = seg.get("subtitles") or []
            if not text or not subs:
                continue
            # on accepte 1 seule ligne SRT par segment (schema JSON)
            line = subs[0]
            m = re.match(r"\s*(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", line)
            if not m:
                continue
            a, b = m.group(1), m.group(2)
            f.write(f"{idx}\n{a} --> {b}\n{text}\n\n")
            idx += 1

def generate_video(
    plan,
    audio_path,
    output_name,
    temp_dir,
    width=1080,
    height=1920,
    fps=30,
    logger=None,
    req_id="?",
    global_srt=None
):
    """
    Retourne: (out_path, debug_dict)
    """
    logger = logger or DummyLogger()

    tmp = temp_dir
    parts = []
    debug = {"segments": []}

    # 1) préparer chaque segment vidéo (boucle/coupe)
    for i, seg in enumerate(plan):
        url = seg.get("gif_url")
        dur_req = float(seg.get("duration") or 0)
        if not url or dur_req <= 0:
            continue

        logger.info(f"[{req_id}] seg#{i} url={url} dur_req={dur_req:.3f}")

        src_path = os.path.join(tmp, f"src_{int(1000*os.times().elapsed)}_{i}.mp4")
        _download(url, src_path, logger, req_id)
        meta = _probe(src_path)
        src_dur = meta["duration"] or 1.0

        # Combien de répétitions pour couvrir dur_req
        loops = max(1, math.ceil((dur_req + 0.01) / max(0.1, src_dur)))

        # concat des boucles
        seg_list = os.path.join(tmp, f"seg_{i:03d}.txt")
        _write_filelist([src_path] * loops, seg_list)
        looped = os.path.join(tmp, f"loop_{i:03d}.mp4")
        _run([
            "ffmpeg","-y","-f","concat","-safe","0","-i",seg_list,
            "-c","copy", looped
        ], logger, req_id)

        # couper exactement à dur_req
        part_path = os.path.join(tmp, f"part_{i:03d}.mp4")
        _run([
            "ffmpeg","-y","-t", f"{dur_req:.3f}",
            "-i", looped, "-c","copy", "-avoid_negative_ts","make_zero",
            part_path
        ], logger, req_id)

        parts.append(part_path)
        debug["segments"].append({
            "i": i, "url": url, "dur_req": dur_req, "src_dur": src_dur, "loops": loops,
            "part_path": part_path
        })

    # 2) concat + normalisation (⚠️ re-encode ici pour fixer fps/timebase/SAR)
    list_all = os.path.join(tmp, "list_all.txt")
    _write_filelist(parts, list_all)
    concat_path = os.path.join(tmp, "concat.mp4")

    # scale/pad si width/height fournis
    vf = (
        f"fps={fps},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p,setsar=1"
    )
    _run([
        "ffmpeg","-y",
        "-f","concat","-safe","0","-i", list_all,
        "-vf", vf,
        "-r", str(fps),
        "-c:v","libx264","-preset","veryfast","-crf","23",
        "-movflags","+faststart",
        concat_path
    ], logger, req_id)

    # 3) SRT (global ou construit depuis plan)
    subs_path = os.path.join(tmp, "subs.srt")
    _build_srt(plan, subs_path, global_srt=global_srt)

    # 4) export final avec audio + sous-titres
    out_path = os.path.join(tmp, output_name)
    _run([
        "ffmpeg","-y","-threads","1",
        "-i", concat_path,
        "-i", audio_path,
        "-vf", f"subtitles={subs_path}:force_style='Fontsize=36,Outline=2,Shadow=1,Alignment=2,MarginV=60'",
        "-c:v","libx264","-preset","veryfast","-crf","23",
        "-r", str(fps),
        "-c:a","aac",
        "-shortest",
        "-movflags","+faststart",
        out_path
    ], logger, req_id)

    return out_path, debug


class DummyLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
