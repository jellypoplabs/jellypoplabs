#!/usr/bin/env python3
"""素材体检：时长/分辨率/音轨、静音段、（可选）画面静止段、带时间的抽帧拼图。

用法：python3 scan.py 素材.mp4 [--every 秒] [--motion] [--out 目录]
输出：<out>/summary.md  silences.json  freezes.json  sheet_01.jpg ...
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fmt_time, probe  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def detect(path, filt, pattern_start, pattern_end, vf=False):
    flag = "-vf" if vf else "-af"
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path, flag, filt]
    cmd += ["-an"] if vf else ["-vn"]
    cmd += ["-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(pattern_start, err)]
    ends = [float(x) for x in re.findall(pattern_end, err)]
    return starts, ends


def pair(starts, ends, total):
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else total
        out.append({"start": round(s, 2), "end": round(e, 2), "dur": round(e - s, 2)})
    return out


def font(size):
    for f in ["/System/Library/Fonts/PingFang.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def sheets(path, every, out, total, cols=6, rows=5):
    tmp = os.path.join(out, "_frames")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
                    "-vf", f"fps=1/{every},scale=320:-2", "-q:v", "4",
                    os.path.join(tmp, "f_%05d.jpg")], check=True)
    frames = sorted(os.listdir(tmp))
    if not frames:
        return []
    fw, fh = Image.open(os.path.join(tmp, frames[0])).size
    lab = 28
    f = font(20)
    per = cols * rows
    made = []
    for si in range(0, len(frames), per):
        chunk = frames[si:si + per]
        sheet = Image.new("RGB", (cols * fw, rows * (fh + lab)), (24, 24, 24))
        d = ImageDraw.Draw(sheet)
        for i, name in enumerate(chunk):
            # fps=1/N 的第 k 帧大约在 (k-0.5)*N 秒；取中点更接近真实位置
            t = (si + i + 0.5) * every
            if t > total:
                t = total
            x, y = (i % cols) * fw, (i // cols) * (fh + lab)
            sheet.paste(Image.open(os.path.join(tmp, name)), (x, y + lab))
            d.text((x + 6, y + 3), fmt_time(t), fill=(255, 220, 80), font=f)
        p = os.path.join(out, f"sheet_{si // per + 1:02d}.jpg")
        sheet.save(p, quality=85)
        made.append(p)
    shutil.rmtree(tmp)
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--every", type=float, default=0, help="每隔几秒抽一帧，默认按时长自动")
    ap.add_argument("--silence-db", type=float, default=-35)
    ap.add_argument("--silence-min", type=float, default=1.5)
    ap.add_argument("--motion", action="store_true", help="同时检测画面静止段（长视频较慢）")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    info = probe(a.video)
    total = info["duration"]
    out = a.out or os.path.splitext(a.video)[0] + "_scan"
    os.makedirs(out, exist_ok=True)
    every = a.every or max(2.0, round(total / 120))  # 约 120 张

    silences, freezes = [], []
    if info["has_audio"]:
        s, e = detect(a.video, f"silencedetect=noise={a.silence_db}dB:d={a.silence_min}",
                      r"silence_start: ([\d.]+)", r"silence_end: ([\d.]+)")
        silences = pair(s, e, total)
    if a.motion:
        s, e = detect(a.video, "scale=320:-2,freezedetect=n=0.003:d=3",
                      r"freeze_start: ([\d.]+)", r"freeze_end: ([\d.]+)", vf=True)
        freezes = pair(s, e, total)
    made = sheets(a.video, every, out, total)

    json.dump(silences, open(os.path.join(out, "silences.json"), "w"), indent=1)
    json.dump(freezes, open(os.path.join(out, "freezes.json"), "w"), indent=1)

    sil_total = sum(x["dur"] for x in silences)
    L = [f"# 素材体检：{os.path.basename(a.video)}", "",
         f"- 时长 {fmt_time(total)}（{total:.1f} 秒），{info['width']}x{info['height']}，"
         f"{info['fps'] or 0:.2f} fps，{'有' if info['has_audio'] else '**没有**'}音轨，{info['size_mb']:.0f} MB",
         f"- 抽帧：每 {every:g} 秒一张，共 {len(made)} 张拼图（sheet_XX.jpg，左上角是时间）"]
    if info["has_audio"]:
        L.append(f"- 静音（低于 {a.silence_db:g} dB 且超过 {a.silence_min:g} 秒）：{len(silences)} 段，"
                 f"合计 {sil_total:.0f} 秒，占 {100 * sil_total / total:.0f}%")
    if a.motion:
        L.append(f"- 画面静止（超过 3 秒）：{len(freezes)} 段，合计 {sum(x['dur'] for x in freezes):.0f} 秒")
    if silences and silences[0]["start"] < 0.5:
        L.append(f"- 开头 {silences[0]['dur']:.0f} 秒没声音：通常是开机等待，可直接删")
    L += ["", "## 最长的静音段（前 20）", "", "| 开始 | 结束 | 秒 |", "|---|---|---|"]
    for x in sorted(silences, key=lambda x: -x["dur"])[:20]:
        L.append(f"| {fmt_time(x['start'])} | {fmt_time(x['end'])} | {x['dur']:.1f} |")
    if freezes:
        L += ["", "## 最长的画面静止段（前 20）", "", "| 开始 | 结束 | 秒 |", "|---|---|---|"]
        for x in sorted(freezes, key=lambda x: -x["dur"])[:20]:
            L.append(f"| {fmt_time(x['start'])} | {fmt_time(x['end'])} | {x['dur']:.1f} |")
    open(os.path.join(out, "summary.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L[:8]))
    print(f"\n报告：{os.path.join(out, 'summary.md')}")


if __name__ == "__main__":
    main()
