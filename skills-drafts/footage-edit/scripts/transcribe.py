#!/usr/bin/env python3
"""转写：用 faster-whisper 出带时间的逐字稿。

用法：python3 transcribe.py 素材.mp4 [--model small] [--lang zh] [--out 目录]
输出：transcript.json（每句 start/end/text）、transcript.srt、transcript.md（[mm:ss] 一行一句，给 AI 读）
需要：pip install faster-whisper（第一次运行会下载模型）
中文推荐 small 或 medium；large-v3 最准但慢。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fmt_time  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("没装 faster-whisper：pip install faster-whisper")

    out = a.out or os.path.splitext(a.video)[0] + "_scan"
    os.makedirs(out, exist_ok=True)
    model = WhisperModel(a.model, device="auto", compute_type="int8")
    # 中文提示让模型输出简体和标点
    segs, info = model.transcribe(a.video, language=a.lang, vad_filter=True,
                                  initial_prompt="以下是普通话的句子，使用简体中文和标点。")
    rows = []
    for s in segs:
        rows.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        print(f"[{fmt_time(s.start)}] {s.text.strip()}", flush=True)

    json.dump(rows, open(os.path.join(out, "transcript.json"), "w"), ensure_ascii=False, indent=1)
    with open(os.path.join(out, "transcript.srt"), "w") as f:
        for i, r in enumerate(rows, 1):
            f.write(f"{i}\n{fmt_time(r['start'], True).replace('.', ',')} --> "
                    f"{fmt_time(r['end'], True).replace('.', ',')}\n{r['text']}\n\n")
    with open(os.path.join(out, "transcript.md"), "w") as f:
        for r in rows:
            f.write(f"[{fmt_time(r['start'])}-{fmt_time(r['end'])}] {r['text']}\n")
    print(f"\n{len(rows)} 句，写到 {out}/transcript.*")


if __name__ == "__main__":
    main()
