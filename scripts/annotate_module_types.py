#!/usr/bin/env python3
"""T1: 无限流 9 个模块标类型 + 补机制数据（combat/rest/trap）"""
import json
from pathlib import Path

MD = Path("data/modules_infinite_flow")


def load(name):
    p = MD / name / "module.json"
    return p, json.loads(p.read_text(encoding="utf-8"))


def save(p, d):
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ── combat 遭遇战：enemies + environment + outcomes + exits 改造 ──
COMBAT = {
    "dungeon_juon_storage": {
        "enemy": {"id": "white_shadow", "name": "白衣怨影", "hp": 14, "armor": 0, "attack_bonus": 3,
                  "damage": "1d4+2", "behavior": "半透明的白衣人形在杂物堆间游走，发出嘶哑的气音"},
        "environment": {"terrain": "塞满旧被褥和纸箱的狭小储物间", "lighting": "dim", "hazards": ["杂物堆——移动时可能绊倒", "悬吊的麻绳——可用来捆住怨影"]},
        "exits": [
            {"id": "victory", "label": "驱散白衣怨影，带着发现返回走廊", "provides_clues": ["jy_storage_done", "jy_storage_clue"], "mood": "relief", "next_location_type": "dungeon_jy"},
            {"id": "defeat", "label": "被怨影击退，撤回主神空间", "provides_clues": ["jy_storage_failed", "jy_retreated"], "mood": "anxiety", "next_location_type": "hub"},
            {"id": "flee", "label": "逃出储物间，撤回主神空间", "provides_clues": ["jy_retreated"], "mood": "anxiety", "next_location_type": "hub"},
        ],
        "victory_text": "怨影在麻绳与咒语的夹击下发出最后一声凄厉的哀嚎，化作灰烬消散。杂物堆深处露出一本浸血的日记——这是关于佐伯家的重要线索。",
    },
    "dungeon_rs_armory": {
        "enemy": {"id": "infected_guard", "name": "感染保安", "hp": 16, "armor": 1, "attack_bonus": 3,
                  "damage": "1d6+2", "behavior": "摇晃着站起来，灰白的皮肤下青筋暴起，嘶吼着扑向你们"},
        "environment": {"terrain": "倒地的武器架与碎玻璃", "lighting": "dim", "hazards": ["地面碎玻璃——闪避检定 -1", "战术步枪——夺下可提升伤害"]},
        "exits": [
            {"id": "victory", "label": "击败感染保安，带着装备返回走廊", "provides_clues": ["rs_armory_done", "rs_armory_clue"], "mood": "relief", "next_location_type": "dungeon_rs"},
            {"id": "defeat", "label": "被感染者压制，撤回主神空间", "provides_clues": ["rs_armory_failed", "rs_retreated"], "mood": "anxiety", "next_location_type": "hub"},
            {"id": "flee", "label": "撤离武器库，撤回主神空间", "provides_clues": ["rs_retreated"], "mood": "anxiety", "next_location_type": "hub"},
        ],
        "victory_text": "感染保安轰然倒地。你们从武器架上取下完好的战术步枪和防弹背心——弹药不多，但聊胜于无。监控室的线索提到「武器库里有重火力」——现在你们明白了。",
    },
    "dungeon_xiuxian_arena": {
        "enemy": {"id": "inner_disciple", "name": "内门剑修·青云", "hp": 20, "armor": 1, "attack_bonus": 4,
                  "damage": "1d8+2", "behavior": "剑法凌厉，每回合寻找破绽发动突刺，剑身缠绕着青色灵气"},
        "environment": {"terrain": "悬空的演武石台，四周是云海", "lighting": "normal", "hazards": ["石台边缘——后退可能坠落", "插地的剑旗——可拔起作为武器"]},
        "exits": [
            {"id": "victory", "label": "击败内门剑修，获得丹房线索", "provides_clues": ["xt_arena_done", "xt_arena_clue"], "mood": "relief", "next_location_type": "dungeon_xt"},
            {"id": "defeat", "label": "不敌剑修，撤回主神空间", "provides_clues": ["xt_arena_failed", "xt_retreated"], "mood": "anxiety", "next_location_type": "hub"},
            {"id": "flee", "label": "跳下石台逃走，撤回主神空间", "provides_clues": ["xt_retreated"], "mood": "anxiety", "next_location_type": "hub"},
        ],
        "victory_text": "剑修收剑而立，眼中闪过一丝赞许。「不错——丹房的老家伙最怕雷法，你们若能找到雷属性的法宝，胜算大增。」他转身离去，留下这句话。",
    },
}

# ── rest 补给：hp/san 恢复 ──
REST = {
    "dungeon_rs_canteen": {"hp_recover": 4, "san_recover": 2},
    "dungeon_rs_dorm": {"hp_recover": 3, "san_recover": 3},
    "dungeon_xiuxian_field": {"hp_recover": 5, "san_recover": 3},
}

# ── trap 陷阱/恐怖事件：进入时检定 ──
TRAP = {
    "dungeon_juon_well": {"check": "spirit", "difficulty": 12, "hp_loss": 1, "san_loss": 2, "success_clue": "jy_well_clue"},
    "dungeon_rs_vent": {"check": "agility", "difficulty": 11, "hp_loss": 3, "san_loss": 0, "success_clue": "rs_vent_clue"},
    "dungeon_xiuxian_forbidden": {"check": "spirit", "difficulty": 14, "hp_loss": 4, "san_loss": 3, "success_clue": "xt_forbidden_clue"},
}


def main():
    for name, cfg in COMBAT.items():
        p, d = load(name)
        d["module_type"] = "combat"
        d["enemies"] = [cfg["enemy"]]
        d["environment"] = cfg["environment"]
        d["outcomes"] = {
            "victory": {"label": "胜利", "consequence": cfg["victory_text"], "reward": "获得战斗线索"},
            "defeat": {"label": "失败", "consequence": "你们被击退，负伤退出。", "reward": "无奖励"},
            "flee": {"label": "撤退", "consequence": "你们选择撤退，撤回主神空间。", "reward": "无奖励"},
        }
        d["exits"] = cfg["exits"]
        save(p, d)
        print(f"✅ {name}: combat (enemy={cfg['enemy']['name']})")

    for name, cfg in REST.items():
        p, d = load(name)
        d["module_type"] = "rest"
        d["rest"] = cfg
        save(p, d)
        print(f"✅ {name}: rest (hp+{cfg['hp_recover']} san+{cfg['san_recover']})")

    for name, cfg in TRAP.items():
        p, d = load(name)
        d["module_type"] = "trap"
        d["trap"] = cfg
        save(p, d)
        print(f"✅ {name}: trap (check={cfg['check']} diff={cfg['difficulty']})")


if __name__ == "__main__":
    main()
