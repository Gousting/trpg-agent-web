#!/usr/bin/env python3
"""用 Qwen3.7 Plus (via OpenCode Zen) 逐张标注 COC TRPG 场景卡。

输出 data/scene_tags.json，包含每个场景的15维结构化标签。
"""

import base64
import json
import time
from pathlib import Path

import requests

API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
API_KEY = "sk-ee06LCJ2weQcOOMlap1x0PMsEa7xCuPzj9Rrw7qGHb51JMEu64JC8GfgfjJ6vTAs"
MODEL = "qwen3.7-plus"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCENES_DIR = PROJECT_ROOT / "data" / "scenes" / "Sceneimage"
OUTPUT_PATH = PROJECT_ROOT / "data" / "scene_tags.json"

SYSTEM_PROMPT = """你是 COC TRPG 场景分析专家。对给定的场景图输出结构化 JSON 标注。只返回 JSON 对象，放在 ```json ``` 代码块中。格式：

{
  "scene_type": "场景类型",
  "location": "具体地点描述（15字以内）",
  "lighting": "光线特征",
  "mood": ["氛围1", "氛围2", "氛围3"],
  "architecture": "建筑风格",
  "era": "时代背景",
  "weather": "天气",
  "color_palette": "主色调",
  "art_style": "艺术风格",
  "key_objects": ["物件1", "物件2", "物件3", "物件4"],
  "narrative_hook": "叙事钩子（20字内）",
  "coc_themes": ["克苏鲁主题1", "主题2"],
  "composition": "构图特征",
  "density": "sparse/moderate/dense",
  "reusability": "high/medium/low"
}

标签要具体、可检索。用中文。"""


def encode_image(path: Path) -> str:
    ext = path.suffix.lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def tag_single(image_path: Path, idx: int, total: int) -> dict | None:
    """Send one image to Qwen3.7 Plus and return parsed tags."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "标注这张 COC TRPG 场景卡。"},
                {"type": "image_url", "image_url": {"url": encode_image(image_path)}},
            ]},
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    for attempt in range(3):
        try:
            resp = requests.post(API_URL, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            # Extract JSON from code block
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            tags = json.loads(content.strip())
            return tags

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            if attempt < 2:
                time.sleep(2)
            else:
                print(f"  [{idx}/{total}] JSON解析失败: {e}", flush=True)
                return None
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                time.sleep(3)
            else:
                print(f"  [{idx}/{total}] 请求失败: {e}", flush=True)
                return None
    return None


def main():
    images = sorted(SCENES_DIR.glob("*"))
    images = [p for p in images if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    total = len(images)
    print(f"共 {total} 张场景图，逐张标注...", flush=True)

    output = {
        "version": "2.0",
        "description": "COC TRPG 场景卡标签映射，Qwen3.7 Plus (OpenCode Zen) 标注，15维结构化标签",
        "total": total,
        "images": {},
    }

    success = 0
    for i, img in enumerate(images, 1):
        tags = tag_single(img, i, total)
        if tags:
            output["images"][img.name] = tags
            success += 1
            print(f"  [{i}/{total}] ✓ {img.name[:20]}... {tags.get('scene_type', '?')}", flush=True)
        else:
            print(f"  [{i}/{total}] ✗ {img.name}", flush=True)

        if i < total:
            time.sleep(1.5)  # rate limit

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 完成: {success}/{total} 张", flush=True)
    print(f"  输出: {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
