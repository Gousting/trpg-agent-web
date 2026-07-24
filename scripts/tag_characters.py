#!/usr/bin/env python3
"""用 Qwen3.7 Plus 逐张标注 COC TRPG 角色卡。"""

import base64, io, json, time, requests
from pathlib import Path
from PIL import Image

API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
API_KEY = "sk-ee06LCJ2weQcOOMlap1x0PMsEa7xCuPzj9Rrw7qGHb51JMEu64JC8GfgfjJ6vTAs"
MODEL = "qwen3.7-plus"

CHARS_DIR = Path("/home/shrine/trpg_agent/data/characters/Userimage")
OUTPUT_PATH = Path("/home/shrine/trpg_agent/data/character_tags.json")
MAX_SIZE = (1024, 1024)

SYSTEM_PROMPT = """你是 COC TRPG 角色分析专家。对给定的角色图输出结构化 JSON 标注。只返回 JSON 对象，放在 ```json ``` 代码块中。格式：

{
  "role": "角色定位（如：调查员/私家侦探/教授/记者/医生/邪教徒/警探/古董商/神秘学家等）",
  "gender": "性别",
  "age_range": "年龄段（如：20-30/30-40/40-50/50+）",
  "appearance": "外貌特征（20字内）",
  "clothing": "服装风格（如：风衣+软呢帽/西装三件套/实验白大褂/教士袍/军装等）",
  "era": "时代背景",
  "personality": ["性格标签1", "性格标签2", "性格标签3"],
  "expression": "表情/神态",
  "art_style": "艺术风格",
  "color_palette": "主色调",
  "props": ["手持/身边道具1", "道具2", "道具3"],
  "narrative_hook": "叙事钩子——这个角色在故事中的定位（20字内）",
  "coc_themes": ["关联克苏鲁主题"],
  "sanity_hint": "理智状态暗示（stable/shaken/unstable/lost）",
  "combat_readiness": "战斗倾向（passive/cautious/capable/aggressive）"
}

标签具体、可检索。中文。"""


def encode_image(path: Path) -> str:
    img = Image.open(path)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    w, h = img.size
    if w > MAX_SIZE[0] or h > MAX_SIZE[1]:
        img.thumbnail(MAX_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def tag_one(image_path: Path) -> dict | None:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "标注这张 COC TRPG 角色卡。"},
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
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2)
    return None


images = sorted([p for p in CHARS_DIR.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")])
total = len(images)
print(f"共 {total} 张角色卡，开始标注...")

output = {"version": "1.0", "total": total, "characters": {}}
success = 0

for i, img in enumerate(images, 1):
    tags = tag_one(img)
    if tags:
        output["characters"][img.name] = tags
        success += 1
        print(f"[{i}/{total}] {img.name[:25]:<25s} {tags.get('role', '?')}")
    else:
        print(f"[{i}/{total}] {img.name[:25]:<25s} FAILED")
    if i < total:
        time.sleep(1)

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\nDONE: {success}/{total}")
