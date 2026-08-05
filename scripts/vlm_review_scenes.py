#!/usr/bin/env python3
"""批量审查场景图：qwen3.8-max 逐张检查（畸形/风格/氛围/构图）
用法: python3 scripts/vlm_review_scenes.py [--dir data/scenes/Sceneimage] [--pattern inf_*]
输出: 每张图的评分与问题，FAIL 标记需要重抽的
"""
import argparse
import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

API = "https://opencode.ai/zen/go/v1/chat/completions"
KEY = "sk-9M41TxqJRKchcuWCSg5KQfk5t2FXc0WYx17lNao8hOIu44B03IT3kmgc3zSMAdEe"
MODEL = "qwen3.8-max"

QUESTION = (
    "这是AI生成的粗犷美漫插画风TRPG恐怖/冒险场景图。请严格检查："
    "1.画面有无明显畸形、结构崩坏、乱码文字、鬼影瑕疵 2.粗犷美漫插画风是否到位"
    "（粗黑描边/排线/重阴影）3.构图是否完整不空洞 4.整体氛围是否符合恐怖/冒险主题。"
    "输出格式：第一行【PASS】或【FAIL】，第二行 分数/10，第三行 简短理由（20字内）。"
    "FAIL条件：明显畸形/崩坏/乱码/画面严重空洞。"
)


def prep_image(path: str) -> str:
    from PIL import Image
    img = Image.open(path)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    w, h = img.size
    if w > 1024 or h > 1024:
        img.thumbnail((1024, 1024), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def review(path: Path) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": QUESTION},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{prep_image(str(path))}"}},
        ]}],
        "max_tokens": 200,
    }
    req = urllib.request.Request(
        API, json.dumps(payload).encode(),
        {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}",
         "User-Agent": "curl/8.0"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/scenes/Sceneimage")
    ap.add_argument("--pattern", default="inf_*_00001_.png")
    args = ap.parse_args()

    files = sorted(Path(args.dir).glob(args.pattern))
    print(f"待审查: {len(files)} 张")
    results = []
    for i, f in enumerate(files, 1):
        try:
            out = review(f)
        except Exception as e:
            out = f"❌ 调用失败: {e}"
        fail = "FAIL" in out.upper()
        results.append((f.name, fail, out))
        mark = "🔴" if fail else "🟢"
        print(f"{mark} [{i}/{len(files)}] {f.name}: {out[:120]}")
        time.sleep(0.5)

    fails = [r for r in results if r[1]]
    print(f"\n=== 完成: {len(results)-len(fails)} 通过, {len(fails)} 需重抽 ===")
    for name, _, out in fails:
        print(f"  🔴 {name}: {out[:150]}")


if __name__ == "__main__":
    main()
