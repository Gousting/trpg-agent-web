#!/usr/bin/env python3
"""模块场景图片 prompt 提取脚本。

遍历 data/modules/ 下所有 module.json，提取每个场景的 image_prompt，
输出为可直接喂给 ComfyUI 的生图清单。

用法:
    python scripts/extract_module_prompts.py              # 打印清单
    python scripts/extract_module_prompts.py --json       # 输出 JSON
    python scripts/extract_module_prompts.py --missing    # 只列缺图的场景
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = PROJECT_ROOT / "data" / "modules"
IMAGES_DIR = PROJECT_ROOT / "data" / "scenes" / "modules"

# ── 默认 ComfyUI prompt 增强后缀 ──
_DEFAULT_SUFFIX = (
    "dark atmospheric horror, cinematic lighting, volumetric fog, "
    "photorealistic, 8K, high detail, film grain"
)


def extract(modules_dir: Path, images_dir: Path) -> list[dict]:
    """遍历所有模块，提取场景 prompt 清单。"""
    results: list[dict] = []
    if not modules_dir.is_dir():
        return results

    for mod_dir in sorted(modules_dir.iterdir()):
        if not mod_dir.is_dir():
            continue
        mod_json = mod_dir / "module.json"
        if not mod_json.is_file():
            continue

        data = json.loads(mod_json.read_text(encoding="utf-8"))
        module_id = data.get("id", mod_dir.name)
        module_title = data.get("title", module_id)
        scenes = data.get("scenes", []) or []

        for scene in scenes:
            sid = scene.get("id", "")
            image_prompt = str(scene.get("image_prompt", "") or "").strip()
            if not image_prompt:
                continue

            # 拼接后缀
            full_prompt = f"{image_prompt}, {_DEFAULT_SUFFIX}"

            # 检查图片是否已存在
            image_path = images_dir / module_id / f"{sid}.png"
            exists = image_path.is_file()

            results.append({
                "module_id": module_id,
                "module_title": module_title,
                "scene_id": sid,
                "scene_title": scene.get("title", sid),
                "image_prompt": image_prompt,
                "full_prompt": full_prompt,
                "image_exists": exists,
                "output_path": str(image_path),
                "comfyui_workflow_note": "Z-Image Turbo, 1024x1024, steps=4, cfg=1",
            })

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="提取模块场景生图 prompt")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--missing", action="store_true", help="只列缺图的场景")
    parser.add_argument("--comfyui", action="store_true", help="输出 ComfyUI 批量工作流 JSON")
    args = parser.parse_args()

    items = extract(MODULES_DIR, IMAGES_DIR)

    if args.missing:
        items = [it for it in items if not it["image_exists"]]

    if args.comfyui:
        # 输出可导入 ComfyUI 的节点 JSON（基础 Z-Image 工作流）
        workflow = _build_comfyui_workflow(items)
        json.dump(workflow, sys.stdout, ensure_ascii=False, indent=2)
        return

    if args.json:
        json.dump(items, sys.stdout, ensure_ascii=False, indent=2)
        return

    # 人类可读输出
    if not items:
        print("没有找到包含 image_prompt 的场景。")
        return

    total = len(items)
    existing = sum(1 for it in items if it["image_exists"])
    print(f"模块场景 prompt 清单  ({total} 个场景, {existing} 已有图, {total - existing} 待生成)")
    print("=" * 72)

    for it in items:
        status = "✅" if it["image_exists"] else "⬜"
        print(f"\n{status} [{it['module_id']}] {it['scene_title']}")
        print(f"   Scene: {it['scene_id']}")
        print(f"   Output: {it['output_path']}")
        print(f"   Prompt: {it['image_prompt']}")


def _build_comfyui_workflow(items: list[dict]) -> dict:
    """构建简单的 Z-Image Turbo 批处理工作流（占位，需在 Windows 端替换为实际节点）。"""
    prompts = [it["full_prompt"] for it in items]
    return {
        "_note": "将此 JSON 导入 ComfyUI 后，逐条替换 prompt 节点文本并执行。",
        "_prompt_count": len(prompts),
        "prompts": [
            {
                "index": i,
                "module_id": items[i]["module_id"],
                "scene_id": items[i]["scene_id"],
                "output_path": items[i]["output_path"],
                "prompt": p,
            }
            for i, p in enumerate(prompts)
        ],
    }


if __name__ == "__main__":
    main()
