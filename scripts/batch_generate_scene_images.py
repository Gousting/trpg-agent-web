#!/usr/bin/env python3
"""TRPG 无限流场景图批量生成（粗犷美漫插画风）

读 data/modules_infinite_flow/*/module.json 的全部场景 image_prompt，
追加风格后缀 → ComfyUI 排队生成 → 下载到 data/scenes/Sceneimage/。
输出映射表（模块名 → 文件名），供后续加 image 字段。

用法: python3 scripts/batch_generate_scene_images.py [--dry-run] [--limit N]
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

HOST = "http://192.168.0.106:8188"
PROJECT = Path(__file__).resolve().parent.parent
MD = PROJECT / "data" / "modules_infinite_flow"
DEST = PROJECT / "data" / "scenes" / "Sceneimage"

STYLE_SUFFIX = (
    ", gritty dark comic book illustration style, bold inked outlines, "
    "dramatic cross-hatching and heavy shadows, textured brushwork, "
    "dark manga-horror art, cinematic horror comic panel, high contrast"
)


def collect_scenes() -> list[dict]:
    """收集 (module_name, scene_id, image_prompt) 清单，跳过 boss 和无 prompt 的"""
    out = []
    for sub in sorted(MD.iterdir()):
        if not sub.is_dir():
            continue
        mf = sub / "module.json"
        if not mf.is_file():
            continue
        name = sub.name
        if "boss" in name:
            continue
        d = json.loads(mf.read_text(encoding="utf-8"))
        for sc in d.get("scenes", []):
            prompt = (sc.get("image_prompt") or "").strip()
            if not prompt:
                print(f"  ⚠️ {name}::{sc.get('id')} 无 image_prompt，跳过")
                continue
            out.append({"module": name, "scene": sc.get("id", "?"), "prompt": prompt})
    return out


def workflow(prompt: str, seed: int, prefix: str) -> dict:
    return {
        "1": {"inputs": {"width": 1344, "height": 768, "batch_size": 1}, "class_type": "EmptyLatentImage"},
        "2": {"inputs": {"unet_name": "z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors", "weight_dtype": "default"}, "class_type": "UNETLoader"},
        "3": {"inputs": {"clip_name": "qwen3_4b_fp8_scaled.safetensors", "type": "qwen_image"}, "class_type": "CLIPLoader"},
        "4": {"inputs": {"clip": ["3", 0], "prompt": prompt, "auto_resize_images": False}, "class_type": "TextEncodeZImageOmni"},
        "5": {"inputs": {"clip": ["3", 0], "text": ""}, "class_type": "CLIPTextEncode"},
        "6": {"inputs": {
            "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "simple", "denoise": 1.0,
            "model": ["2", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["1", 0],
        }, "class_type": "KSampler"},
        "7": {"inputs": {"vae_name": "ae.safetensors"}, "class_type": "VAELoader"},
        "8": {"inputs": {"samples": ["6", 0], "vae": ["7", 0]}, "class_type": "VAEDecode"},
        "9": {"inputs": {"filename_prefix": prefix, "images": ["8", 0]}, "class_type": "SaveImage"},
    }


def post(url: str, data=None, method="POST"):
    if method == "GET":
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    req = urllib.request.Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def download(filename: str, subfolder: str, dest: Path) -> Path:
    url = f"{HOST}/view?filename={filename}&type=output"
    if subfolder:
        url += f"&subfolder={subfolder}"
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    out = dest / filename
    out.write_bytes(data)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    scenes = collect_scenes()
    if args.limit:
        scenes = scenes[: args.limit]
    print(f"待生成: {len(scenes)} 张")
    for s in scenes:
        print(f"  {s['module']}::{s['scene']}  prompt_len={len(s['prompt'])}")

    if args.dry_run:
        return

    DEST.mkdir(parents=True, exist_ok=True)
    mapping = {}
    pids = []
    for i, s in enumerate(scenes):
        prompt = s["prompt"] + STYLE_SUFFIX
        prefix = f"inf_{s['module']}"
        r = post(f"{HOST}/prompt", {"prompt": workflow(prompt, 1000 + i, prefix), "client_id": "vm_batch"})
        pids.append((s, r["prompt_id"]))
        print(f"提交[{i+1}/{len(scenes)}] {s['module']}: {r['prompt_id'][:12]}")

    pending: list[tuple[dict, str]] = pids
    while pending:
        time.sleep(10)
        for s, pid in list(pending):
            try:
                h = post(f"{HOST}/history/{pid}", method="GET")
            except Exception:
                continue
            if pid not in h:
                continue
            st = h[pid].get("status", {})
            if st.get("completed") or st.get("status_str") == "success":
                for nid, out in h[pid].get("outputs", {}).items():
                    for img in out.get("images", []):
                        fname = img["filename"]
                        dest = download(fname, img.get("subfolder", ""), DEST)
                        mapping[s["module"]] = fname
                        print(f"✅ {s['module']} -> {fname} ({dest.stat().st_size//1024}KB)")
                pending.remove((s, pid))
            elif st.get("status_str") == "error":
                print(f"❌ {s['module']}: {json.dumps(st, ensure_ascii=False)[:300]}")
                pending.remove((s, pid))
    if pending:
        print("⏰ 超时剩余:", [s["module"] for s in pending])

    print(f"\n=== 完成 {len(mapping)}/{len(scenes)} ===")
    for mod, fname in mapping.items():
        print(f"  {mod} → {fname}")


if __name__ == "__main__":
    main()
