#!/usr/bin/env python3
"""恢复下载：从 ComfyUI history 拉取所有 inf_* 输出到 data/scenes/Sceneimage/
（batch_generate_scene_images.py 提交成功但轮询崩溃后的兜底脚本）
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

HOST = "http://192.168.0.106:8188"
PROJECT = Path(__file__).resolve().parent.parent
DEST = PROJECT / "data" / "scenes" / "Sceneimage"


def get(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
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
    DEST.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for _ in range(120):
        try:
            h = get(f"{HOST}/history?max_items=60")
        except Exception:
            time.sleep(10)
            continue
        done = 0
        for pid, v in h.items():
            st = v.get("status", {})
            if not (st.get("completed") or st.get("status_str") == "success"):
                continue
            for nid, out in v.get("outputs", {}).items():
                for img in out.get("images", []):
                    fname = img["filename"]
                    if not fname.startswith("inf_"):
                        continue
                    if fname in mapping:
                        continue
                    dest = download(fname, img.get("subfolder", ""), DEST)
                    mapping[fname] = dest
                    mod = fname.split("_00001_")[0]
                    print(f"✅ {mod} -> {fname} ({dest.stat().st_size//1024}KB)")
                    done += 1
        if done == 0 and len(mapping) >= 28:
            break
        if len(mapping) >= 28:
            break
        time.sleep(15)
    print(f"\n=== 已下载 {len(mapping)} 张 ===")
    for fname in sorted(mapping):
        print(f"  {fname}")


if __name__ == "__main__":
    main()
