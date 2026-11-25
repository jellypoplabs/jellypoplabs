"""剪辑脚本共用的小工具：时间解析、ffprobe。"""
import json
import subprocess


def parse_time(v):
    """接受 秒数(数字) / "ss.f" / "mm:ss(.f)" / "hh:mm:ss(.f)"，返回秒。"""
    if isinstance(v, (int, float)):
        return float(v)
    parts = str(v).strip().split(":")
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    return sec


def fmt_time(t, ms=False):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if ms:
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"
    if h >= 1:
        return f"{int(h)}:{int(m):02d}:{int(s):02d}"
    return f"{int(m):02d}:{int(s):02d}"


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fps = None
    if v:
        num, den = v.get("avg_frame_rate", "0/1").split("/")
        fps = float(num) / float(den) if float(den) else None
        if not fps:
            num, den = v.get("r_frame_rate", "30/1").split("/")
            fps = float(num) / float(den)
    rot = 0
    if v:
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        rot = int(v.get("tags", {}).get("rotate", rot))
    w, h = (v["width"], v["height"]) if v else (0, 0)
    if abs(rot) in (90, 270):
        w, h = h, w
    return {
        "duration": float(info["format"]["duration"]),
        "width": w, "height": h, "fps": fps,
        "has_video": v is not None, "has_audio": a is not None,
        "size_mb": int(info["format"].get("size", 0)) / 1e6,
    }
