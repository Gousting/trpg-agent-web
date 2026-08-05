"""批量给无限流非 BOSS 模块添加"撤退回主神空间"出口（设计文档 §3.3-4）。

- 模块级 exits 追加 flee 出口（next_location_type: hub, provides_clues: <world>_retreated）
- 场景级 exit_labels 追加 hub_plaza 映射
- 已有撤退口的模块（attic）跳过，幂等可重跑
"""
import json
from pathlib import Path

MD = Path("data/modules_infinite_flow")

WORLD_PREFIX = {
    "juon": "jy",
    "rs": "rs",
    "xiuxian": "xt",
}

RETREAT_EXIT = {
    "id": "flee",
    "label": "撤退回主神空间（无奖励）",
    "provides_clues": [],  # 运行时填 <world>_retreated
    "mood": "anxiety",
    "next_location_type": "hub",
}

RETREAT_LABEL = "咒怨太深了——撤退回主神空间"  # 会被覆盖为世界观文案


def retreat_label(world: str) -> str:
    return {
        "juon": "咒怨太深了——撤退回主神空间",
        "rs": "这次生化围剿到此为止——撤退回主神空间",
        "xiuxian": "修仙之路不可强求——撤退回主神空间",
    }[world]


def main() -> None:
    changed = 0
    skipped = 0
    for sub in sorted(MD.iterdir()):
        if not sub.is_dir():
            continue
        mf = sub / "module.json"
        if not mf.is_file():
            continue
        d = json.loads(mf.read_text(encoding="utf-8"))
        name = sub.name
        if name == "hub_plaza" or "boss" in name:
            skipped += 1
            continue

        # 世界观前缀：从模块名取 dungeon_<world>_*
        parts = name.split("_")
        world = parts[1] if len(parts) > 1 else ""
        if world not in WORLD_PREFIX:
            print(f"  ⚠️ 无法识别世界观: {name}")
            skipped += 1
            continue
        clue = f"{WORLD_PREFIX[world]}_retreated"

        exits = d.get("exits", [])
        if any(e.get("next_location_type") == "hub" for e in exits):
            skipped += 1
            continue

        # 1) 模块级撤退出口
        exit_entry = json.loads(json.dumps(RETREAT_EXIT))
        exit_entry["provides_clues"] = [clue]
        exits.append(exit_entry)

        # 2) 场景级 exit_labels 映射
        for sc in d.get("scenes", []):
            labels = sc.setdefault("exit_labels", {})
            if "hub_plaza" not in labels:
                labels["hub_plaza"] = retreat_label(world)

        mf.write_text(
            json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        changed += 1
        print(f"  ✓ {name}: 加撤退口 ({clue})")

    print(f"\n完成: 修改 {changed} 个模块, 跳过 {skipped} 个")


if __name__ == "__main__":
    main()
