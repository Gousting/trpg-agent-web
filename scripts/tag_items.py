#!/usr/bin/env python3
"""用 Qwen3.7 Plus (via OpenCode Zen) 逐张标注 COC TRPG 物品卡。"""

import base64, io, json, time, requests
from pathlib import Path
from PIL import Image

API_URL = "https://opencode.ai/zen/go/v1/chat/completions"
API_KEY = "sk-ee06LCJ2weQcOOMlap1x0PMsEa7xCuPzj9Rrw7qGHb51JMEu64JC8GfgfjJ6vTAs"
MODEL = "qwen3.7-plus"

ITEMS_DIR = Path("/home/shrine/trpg_agent/data/items/Itemimage")
OUTPUT_PATH = Path("/home/shrine/trpg_agent/data/item_tags.json")
MAX_SIZE = (1024, 1024)

SYSTEM_PROMPT = """你是 COC TRPG 道具分析专家。对给定的物品图输出结构化 JSON 标注。只返回 JSON 对象，放在 ```json ``` 代码块中。格式：

{
  "item_type": "物品类型",
  "name_suggestion": "建议名称（10字内）",
  "material": "材质",
  "condition": "新旧状态",
  "era": "时代风格",
  "mystical_aura": "神秘感等级（none/subtle/strong/overwhelming）",
  "color_palette": "主色调",
  "art_style": "艺术风格",
  "key_features": ["特征1", "特征2", "特征3"],
  "narrative_hook": "叙事钩子（20字内）",
  "coc_themes": ["关联主题"],
  "danger_level": "危险性（safe/suspicious/dangerous/deadly）",
  "rarity": "稀有度（common/uncommon/rare/legendary）",
  "function_hint": "功能暗示（20字内）"
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
                {"type": "text", "text": "标注这张 COC TRPG 物品卡。"},
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
        except Exception as e:
            if attempt == 2:
                print(f"  FAIL: {e}")
                return None
            time.sleep(2)
    return None


images = sorted([p for p in ITEMS_DIR.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")])
total = len(images)
print(f"共 {total} 件物品，开始标注...")

output = {"version": "1.0", "total": total, "items": {}}
success = 0

for i, img in enumerate(images, 1):
    tags = tag_one(img)
    if tags:
        output["items"][img.name] = tags
        success += 1
        print(f"[{i}/{total}] {img.name[:25]:<25s} {tags.get('item_type', '?')}")
    else:
        print(f"[{i}/{total}] {img.name[:25]:<25s} FAILED")
    if i < total:
        time.sleep(1)

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\nDONE: {success}/{total}")
