#!/usr/bin/env python3
"""按剪辑方案（plan.json）出片。先 --review 给人看，确认了再渲染。

用法：
  python3 render_plan.py plan.json --review          # 只打印审核表，不渲染
  python3 render_plan.py plan.json                   # 渲染全部输出
  python3 render_plan.py plan.json --only hl_01      # 只渲染名字里含 hl_01 的
  python3 render_plan.py plan.json --preview         # 480p 快速预览版

plan.json 格式见 templates/plan.example.json。动作：
  keep              原速保留
  speed             加速，配 "speed": 2~8；"audio": "keep"（默认，变速不变调）或 "mute"
  cut               删掉（写出来只为了审核表里看得到删了什么，渲染时跳过）
没写进 segments 的时间段一律不要。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fmt_time, parse_time, probe  # noqa: E402

ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5), "3:4": (3, 4)}


def load(plan_path):
    plan = json.load(open(plan_path))
    base = os.path.dirname(os.path.abspath(plan_path))
    plan["_src"] = os.path.join(base, plan["source"])
    if plan.get("transcript"):
        plan["_transcript"] = os.path.join(base, plan["transcript"])
    plan["_outdir"] = os.path.join(base, plan.get("out_dir", "out"))
    for o in plan["outputs"]:
        for s in o["segments"]:
            s["_s"], s["_e"] = parse_time(s["start"]), parse_time(s["end"])
            s["action"] = s.get("action", "keep")
            s["_sp"] = float(s.get("speed", 1)) if s["action"] == "speed" else 1.0
            s["_dur_out"] = 0 if s["action"] == "cut" else (s["_e"] - s["_s"]) / s["_sp"]
    return plan


def review(plan, info):
    warns = []
    total = info["duration"]
    print(f"源文件：{plan['source']}  时长 {fmt_time(total)}  {info['width']}x{info['height']}\n")
    for o in plan["outputs"]:
        segs = o["segments"]
        src_used = sum(s["_e"] - s["_s"] for s in segs if s["action"] != "cut")
        out_len = sum(s["_dur_out"] for s in segs)
        print(f"## {o['name']}  画幅 {o.get('aspect', '原始')}  成片约 {fmt_time(out_len)}"
              f"（用了源素材 {fmt_time(src_used)}）")
        if o.get("note"):
            print(f"   {o['note']}")
        print("| # | 源时间 | 动作 | 源长→成片 | 备注 |\n|---|---|---|---|---|")
        prev_e = None
        for i, s in enumerate(segs, 1):
            act = {"keep": "保留", "cut": "删", "speed": f"{s['_sp']:g}x 加速"}.get(s["action"], s["action"])
            if s["action"] == "speed" and s.get("audio") == "mute":
                act += " 静音"
            print(f"| {i} | {fmt_time(s['_s'])}-{fmt_time(s['_e'])} | {act} | "
                  f"{s['_e'] - s['_s']:.0f}s→{s['_dur_out']:.0f}s | {s.get('note', '')} |")
            if s["_e"] <= s["_s"]:
                warns.append(f"{o['name']} #{i}：结束时间不晚于开始时间")
            if s["_e"] > total + 0.5:
                warns.append(f"{o['name']} #{i}：结束 {fmt_time(s['_e'])} 超过素材长度")
            if prev_e is not None and s["_s"] < prev_e - 0.05 and not o.get("allow_reorder"):
                warns.append(f"{o['name']} #{i}：和上一段重叠或倒序（要故意倒序就在这个输出里写 allow_reorder: true）")
            if (info["has_audio"] and s["action"] == "speed" and s["_sp"] > 2
                    and s.get("audio", "keep") == "keep"):
                warns.append(f"{o['name']} #{i}：{s['_sp']:g}x 还保留人声，基本听不清，考虑 audio: mute 或配音乐")
            if s["action"] == "speed" and s["_sp"] < 1:
                warns.append(f"{o['name']} #{i}：speed 小于 1 是慢放，确认是故意的")
            prev_e = s["_e"]
        if o.get("aspect") in ASPECTS:
            crop, ow, oh = geometry(o, info, False)
            cw = int(crop.split("=")[1].split(":")[0])
            up = ow / cw
            if up > 2:
                warns.append(f"{o['name']}：裁切后只剩 {cw}px 宽，要放大 {up:.1f} 倍到 {ow}x{oh}，会糊。"
                             "以后拍摄用 4K 或直接竖拍")
        if o.get("aspect") in ("9:16", "4:5", "3:4") and out_len > 90:
            warns.append(f"{o['name']}：竖屏短片 {out_len:.0f} 秒，偏长（单个切片通常 15–60 秒）")
        print()
    if not info["has_audio"]:
        warns.append("源文件没有音轨，成片会补一条静音音轨")
    if warns:
        print("### 需要注意")
        for w in warns:
            print(f"- {w}")
    else:
        print("### 没有发现格式问题（内容好不好还要人看）")
    return warns


def atempo_chain(sp):
    parts = []
    while sp > 2.0:
        parts.append("atempo=2.0")
        sp /= 2.0
    while sp < 0.5:
        parts.append("atempo=0.5")
        sp /= 0.5
    parts.append(f"atempo={sp:.4f}")
    return ",".join(parts)


def geometry(o, info, preview):
    """返回 (裁切滤镜, 输出宽, 输出高)。"""
    W, H = info["width"], info["height"]
    asp = o.get("aspect")
    crop = ""
    if asp and asp in ASPECTS:
        aw, ah = ASPECTS[asp]
        if W / H > aw / ah:  # 源更宽：裁左右
            cw, ch = int(H * aw / ah) // 2 * 2, H
        else:  # 源更高：裁上下
            cw, ch = W, int(W * ah / aw) // 2 * 2
        cx = float(o.get("crop_x", 0.5))
        cy = float(o.get("crop_y", 0.5))
        x = int(max(0, min(W - cw, cx * W - cw / 2)))
        y = int(max(0, min(H - ch, cy * H - ch / 2)))
        crop = f"crop={cw}:{ch}:{x}:{y},"
        # 平台上传标准：短边 1080（9:16 → 1080x1920）
        short = 1080
        ow, oh = (short, int(short * ah / aw)) if aw <= ah else (int(short * aw / ah), short)
    else:
        ow, oh = W, H
    if preview:
        k = 480 / min(ow, oh)
        ow, oh = int(ow * k), int(oh * k)
    return crop, ow // 2 * 2, oh // 2 * 2


def render_output(plan, o, info, preview):
    src = plan["_src"]
    fps = round(info["fps"] or 30, 3)
    crop, ow, oh = geometry(o, info, preview)
    os.makedirs(plan["_outdir"], exist_ok=True)
    suffix = "_preview" if preview else ""
    final = os.path.join(plan["_outdir"], f"{o['name']}{suffix}.mp4")
    crf = "28" if preview else str(plan.get("crf", 20))
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, s in enumerate(x for x in o["segments"] if x["action"] != "cut"):
            dur_in = s["_e"] - s["_s"]
            sp = s["_sp"]
            dur_out = dur_in / sp
            vf = f"{crop}scale={ow}:{oh}:flags=lanczos,setsar=1,setpts=(PTS-STARTPTS)/{sp},fps={fps}"
            fade = 0.03 if dur_out > 0.2 else 0
            af = []
            if sp != 1:
                af.append(atempo_chain(sp))
            if s.get("audio") == "mute":
                af.append("volume=0")
            if fade:
                af.append(f"afade=t=in:d={fade},afade=t=out:st={max(0, dur_out - fade):.3f}:d={fade}")
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                   "-ss", f"{s['_s']:.3f}", "-t", f"{dur_in:.3f}", "-i", src]
            if not info["has_audio"]:
                cmd += ["-f", "lavfi", "-t", f"{dur_out:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            cmd += ["-vf", vf]
            if info["has_audio"]:
                cmd += ["-af", ",".join(af) if af else "anull"]
            else:
                cmd += ["-map", "0:v:0", "-map", "1:a:0"]
            cmd += ["-t", f"{dur_out:.3f}",
                    "-c:v", "libx264", "-preset", "veryfast" if preview else "medium", "-crf", crf,
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                    os.path.join(tmp, f"p{i:04d}.mp4")]
            subprocess.run(cmd, check=True)
            parts.append(os.path.join(tmp, f"p{i:04d}.mp4"))
            print(f"  {o['name']}: 第 {i + 1} 段 {fmt_time(s['_s'])}-{fmt_time(s['_e'])} 完成", flush=True)
        lst = os.path.join(tmp, "list.txt")
        open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", lst, "-c", "copy", "-movflags", "+faststart", final], check=True)
    expect = sum(s["_dur_out"] for s in o["segments"])
    got = probe(final)["duration"]
    flag = "" if abs(got - expect) < 0.5 else "  ⚠ 和预期差超过 0.5 秒，检查一下"
    print(f"→ {final}  {ow}x{oh}  {got:.1f}s（预期 {expect:.1f}s）{flag}")
    return final


def write_srt(plan, o, final):
    """把整片逐字稿的时间换算到这个成片上；加速且静音的段落不出字幕。"""
    rows = json.load(open(plan["_transcript"]))
    subs, off = [], 0.0
    for s in o["segments"]:
        if s["action"] == "cut":
            continue
        if not (s["action"] == "speed" and s.get("audio") == "mute"):
            for r in rows:
                a, b = max(r["start"], s["_s"]), min(r["end"], s["_e"])
                if b - a > 0.2:
                    subs.append((off + (a - s["_s"]) / s["_sp"], off + (b - s["_s"]) / s["_sp"], r["text"]))
        off += s["_dur_out"]
    p = os.path.splitext(final)[0] + ".srt"

    def ts(t):
        return fmt_time(t, True).replace(".", ",")
    with open(p, "w") as f:
        for i, (a, b, t) in enumerate(subs, 1):
            f.write(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n\n")
    print(f"  字幕 {p}（{len(subs)} 条，导入剪映可直接改样式）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    plan = load(a.plan)
    info = probe(plan["_src"])
    warns = review(plan, info)
    if a.review:
        return
    if any("超过素材长度" in w or "不晚于开始" in w for w in warns):
        sys.exit("\n方案里有时间错误，先改 plan.json")
    print("\n开始渲染（一个一个来，不要同时跑两个渲染）")
    for o in plan["outputs"]:
        if a.only and a.only not in o["name"]:
            continue
        final = render_output(plan, o, info, a.preview)
        if plan.get("_transcript") and os.path.exists(plan["_transcript"]):
            write_srt(plan, o, final)
    print("\n渲染完了，下一步：对每个成片跑 ai-video-check 的 render_check.sh，再抽帧看。")


if __name__ == "__main__":
    main()
