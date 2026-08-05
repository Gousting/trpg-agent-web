#!/usr/bin/env python3
"""v1.15 模块类型扩展——批量改造无限流模块 JSON。

- 3 个 story → puzzle（解谜）：juon_deed / rs_monitor / xiuxian_library
- 3 个 story → social（社交）：juon_neighbor / rs_autopsy / xiuxian_mentor
- 3 个 story → choice（抉择）：juon_bathroom / rs_lab / xiuxian_court
- 3 个 BOSS 加 phase_thresholds（阶段机制）
- 3 个 story 加 interaction（互动叙事）：juon_diary / rs_dorm / xiuxian_danfang

幂等：已有对应字段则跳过。
"""
import json
import os
from pathlib import Path

BASE = Path("data/modules_infinite_flow")

def load(mid):
    p = BASE / mid / "module.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    return d, p

def save(mid, d):
    p = BASE / mid / "module.json"
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  ✓ {mid}")

# ── puzzle ─────────────────────────────────────────────
PUZZLE = {
    "dungeon_juon_deed": {
        "description": "房契背面有一组被涂掉的数字，旁边是伽椰子生前留下的旧照片。你们需要从线索中推出正确的顺序才能打开铁盒里的暗格。",
        "options": [
            {"text": "按照片拍摄日期排序——老照片在先", "correct": True, "clue": "jy_deed_puzzle", "penalty": 0,
             "result_text": "暗格弹开，里面是伽椰子的结婚戒指——上面刻着另一个名字。"},
            {"text": "按房契上的金额从大到小排序", "correct": False, "penalty": 2,
             "result_text": "机关发出咔哒声，一道暗针弹出——好在闪避及时。"},
            {"text": "把纸片撕成的小林名字拼回去", "correct": False, "penalty": 1,
             "result_text": "拼图缺了一块，铁盒纹丝不动。"},
        ],
    },
    "dungeon_rs_monitor": {
        "description": "监控室的控制台需要输入四位数密码。墙上贴着的便签写着「B.O.W. 泄漏日 + 疫苗批号后四位」，桌上一本翻开的日志记录了 3 月 14 日的泄漏事故和疫苗批号 0027。",
        "options": [
            {"text": "输入 03140027", "correct": True, "clue": "rs_monitor_puzzle", "penalty": 0,
             "result_text": "屏幕亮起，监控录像自动回放——你们看到了实验体逃脱的完整过程。"},
            {"text": "输入 14030027", "correct": False, "penalty": 2,
             "result_text": "警报响起，走廊传来脚步声——你们被迫躲进柜子。"},
            {"text": "输入 00271403", "correct": False, "penalty": 1,
             "result_text": "密码错误，控制台发出刺耳的提示音。"},
        ],
    },
    "dungeon_xiuxian_library": {
        "description": "藏经阁三楼的禁书区，一本无字天书悬浮在光幕中。旁边的石刻提示：「以灵田之青，试炼之白，丹房之火，禁地之墨，按五行相生排列。」",
        "options": [
            {"text": "青→火→白→墨（木生火，火克金？不对）", "correct": False, "penalty": 2,
             "result_text": "光幕闪烁，一道剑气擦过——你们狼狈避开。"},
            {"text": "墨→白→青→火（水生木，木生火）", "correct": True, "clue": "xt_library_puzzle", "penalty": 0,
             "result_text": "光幕消散，天书缓缓展开——上面记载着「入魔」的真相。"},
            {"text": "白→墨→火→青", "correct": False, "penalty": 1,
             "result_text": "光幕剧烈震动，书架开始摇晃。"},
        ],
    },
}

# ── social ─────────────────────────────────────────────
SOCIAL = {
    "dungeon_juon_neighbor": {
        "npc": "邻居老妇人",
        "npc_desc": "三十年前佐伯家惨案的唯一知情者，守着秘密过了大半辈子。警惕、疲惫、恐惧。",
        "responses": [
            {"text": "「我们只是想知道真相。您一个人守着这些秘密，很累吧？」", "effect_type": "clue",
             "success_clue": "jy_neighbor_trust", "effect_value": 0,
             "fail_text": "老妇人沉默了。"},
            {"text": "「这房子现在归我们管了，你最好把知道的全说出来。」", "effect_type": "none",
             "effect_value": 0, "fail_text": "老妇人脸色一沉，关上了门。"},
            {"text": "拿出房契：「这个人——小林——您认识吗？」", "effect_type": "clue",
             "success_clue": "jy_neighbor_kobayashi", "effect_value": 0,
             "fail_text": "她盯着房契，手在发抖。"},
        ],
    },
    "dungeon_rs_autopsy": {
        "npc": "幸存研究员",
        "npc_desc": "解剖室里唯一幸存的 T 病毒研究员，精神濒临崩溃，手里攥着一支针剂。",
        "responses": [
            {"text": "「你还好吗？我们不是来伤害你的。」", "effect_type": "clue",
             "success_clue": "rs_autopsy_trust", "effect_value": 0,
             "fail_text": "研究员缩在角落，没有回应。"},
            {"text": "「把针剂交出来！」", "effect_type": "none", "effect_value": 0,
             "fail_text": "研究员猛地站起来，针剂对准了自己。"},
            {"text": "「疫苗——你的研究资料在哪里？」", "effect_type": "clue",
             "success_clue": "rs_autopsy_data", "effect_value": 0,
             "fail_text": "研究员眼神闪烁：「疫苗……早就没了……」"},
        ],
    },
    "dungeon_xiuxian_mentor": {
        "npc": "传功长老",
        "npc_desc": "青云宗传功长老，温和正直，对三十年前轮回者的失踪耿耿于怀。",
        "responses": [
            {"text": "「长老，三十年前那位轮回者——他到底怎么了？」", "effect_type": "clue",
             "success_clue": "xt_mentor_truth", "effect_value": 0,
             "fail_text": "长老叹了口气：「那是个不该提起的名字。」"},
            {"text": "「弟子愿拜入长老门下，求长老指点。」", "effect_type": "hp", "effect_value": 4,
             "fail_text": "长老微微颔首，但没有立即答应。"},
            {"text": "「长老可知后山禁地藏着什么？」", "effect_type": "clue",
             "success_clue": "xt_mentor_forbidden", "effect_value": 0,
             "fail_text": "长老脸色一变：「那不是你们该去的地方。」"},
        ],
    },
}

# ── choice ─────────────────────────────────────────────
CHOICE = {
    "dungeon_juon_bathroom": {
        "description": "浴室的门半开着，里面传来断断续续的呜咽声。浴帘后似乎有一个人影——是活人，还是……",
        "options": [
            {"text": "掀开浴帘救人——不管是什么都要看一眼", "clue": "jy_bathroom_rescue", "hp_cost": 1, "san_cost": 2,
             "reward_text": "浴帘后是伽椰子的幻影——你们看清了她临死前的样子，但也付出了代价。"},
            {"text": "关上门离开——这不是你们该管的", "clue": "jy_bathroom_ignore", "hp_cost": 0, "san_cost": 1,
             "reward_text": "门在身后缓缓合上，呜咽声消失了——但你们知道，有些东西看不见不代表不在。"},
            {"text": "用相机拍下浴帘后的景象", "clue": "jy_bathroom_photo", "hp_cost": 0, "san_cost": 3,
             "reward_text": "照片里没有浴帘——只有一个模糊的白衣身影，站在你们身后。"},
        ],
    },
    "dungeon_rs_lab": {
        "description": "实验室中央的培养槽里，一个实验体正在逐渐苏醒。旁边的紧急按钮标注着「销毁样本」。你们发现实验体身上有一份完整的研究日志——记录着病毒的全部真相。",
        "options": [
            {"text": "按下销毁按钮——彻底消灭实验体", "clue": "rs_lab_destroy", "hp_cost": 0, "san_cost": 2,
             "reward_text": "培养槽瞬间被高温灼烧，实验体在惨叫中消失。日志被火焰吞没了一半——但你们记住了关键部分。"},
            {"text": "救出实验体——它也许还保留着人性", "clue": "rs_lab_save", "hp_cost": 3, "san_cost": 1,
             "reward_text": "你们砸开培养槽，实验体踉跄着站起来，用浑浊的眼睛看了你们一眼，然后逃进了黑暗。它会不会回来？"},
            {"text": "研究日志带走，不管实验体", "clue": "rs_lab_log", "hp_cost": 0, "san_cost": 0,
             "reward_text": "你们带着完整的研究日志撤离——这是解开真相的钥匙。"},
        ],
    },
    "dungeon_xiuxian_court": {
        "description": "执法堂首座盯着你们：「三十年前轮回者的失踪，本座追查至今。你们——是来作证的，还是来遮掩的？」堂下跪着瑟瑟发抖的丹房小厮。",
        "options": [
            {"text": "为小厮作证——你们亲眼看到长老在丹房动过手脚", "clue": "xt_court_testify", "hp_cost": 0, "san_cost": 1,
             "reward_text": "首座眼神锐利：「好，本座记下了。」小厮感激地看了你们一眼。"},
            {"text": "保持沉默——宗门内部的事，外人少管", "clue": "xt_court_silence", "hp_cost": 0, "san_cost": 0,
             "reward_text": "首座失望地摇头：「也罢。」但你们能感觉到，他记住了你们的怯懦。"},
            {"text": "反咬一口——说小厮才是内鬼", "clue": "xt_court_accuse", "hp_cost": 2, "san_cost": 3,
             "reward_text": "小厮被拖了下去。首座深深看了你们一眼——那眼神让人后背发凉。"},
        ],
    },
}

# ── BOSS 阶段 ──────────────────────────────────────────
BOSS_PHASES = {
    "dungeon_juon_boss": [
        {"threshold": 0.6, "name": "怨念爆发", "attack_bonus": 2,
         "behavior": "伽椰子的头发无风自动，整栋宅子的怨念在向她汇聚，她的攻击变得疯狂而凌厉。"},
        {"threshold": 0.3, "name": "临死反扑", "attack_bonus": 3,
         "behavior": "伽椰子发出凄厉的尖叫，楼梯间的阴影中爬出无数半透明的人影——它们全都扑向你们。"},
    ],
    "dungeon_rs_boss": [
        {"threshold": 0.6, "name": "暴君觉醒", "attack_bonus": 2,
         "behavior": "暴君融合体的皮肤裂开，露出下面蠕动的血肉——它的速度骤然加快。"},
        {"threshold": 0.3, "name": "病毒狂暴", "attack_bonus": 3,
         "behavior": "融合体仰天咆哮，全身的触手疯狂舞动，每一次挥击都带着致命的腐蚀液。"},
    ],
    "dungeon_xiuxian_boss": [
        {"threshold": 0.6, "name": "入魔加深", "attack_bonus": 2,
         "behavior": "入魔长老的双眼赤红如血，周围的灵气被疯狂吞噬，他的剑招不再留有余地。"},
        {"threshold": 0.3, "name": "魔化爆发", "attack_bonus": 3,
         "behavior": "长老周身黑气冲天，眉心的魔纹彻底绽放——他正在燃烧自己的生命来换取力量。"},
    ],
}

# ── story interaction ──────────────────────────────────
INTERACTION = {
    "dungeon_juon_diary": {
        "options": [
            {"text": "合上日记，把它放回原处", "clue": "jy_diary_putback", "hp_cost": 0, "san_cost": 0,
             "result_text": "日记合上的瞬间，二楼似乎安静了一些。"},
            {"text": "撕下其中一页带走", "clue": "jy_diary_page", "hp_cost": 0, "san_cost": 1,
             "result_text": "那一页记载着伽椰子最后的心理活动——但撕下的瞬间，你们感觉有什么东西注意到了你们。"},
            {"text": "对着日记说出「佐伯伽椰子」的名字", "clue": "jy_diary_name", "hp_cost": 1, "san_cost": 2,
             "result_text": "日记无风自动，翻到了最后一页——上面用鲜血写着「你们也会死」。"},
        ],
    },
    "dungeon_rs_dorm": {
        "options": [
            {"text": "搜索床铺和储物柜", "clue": "rs_dorm_search", "hp_cost": 0, "san_cost": 0,
             "result_text": "在储物柜深处找到了一本私人日记和几张照片——是员工的日常生活。"},
            {"text": "打开那扇传来音乐的门", "clue": "rs_dorm_music", "hp_cost": 1, "san_cost": 1,
             "result_text": "门后是一台还在播放的留声机，旁边的床上躺着一具白骨化的尸体——他死前还戴着耳机。"},
            {"text": "检查墙上的员工值日表", "clue": "rs_dorm_roster", "hp_cost": 0, "san_cost": 0,
             "result_text": "值日表上，大部分名字都被红笔划掉了——只有「理查德」的名字前画着一个问号。"},
        ],
    },
    "dungeon_xiuxian_danfang": {
        "options": [
            {"text": "检查丹炉底部——看看有没有暗格", "clue": "xt_danfang_secret", "hp_cost": 0, "san_cost": 0,
             "result_text": "丹炉底部刻着一行小字：「丹药三分毒，人心七分险。」"},
            {"text": "拿走一瓶现成的丹药", "clue": "xt_danfang_pill", "hp_cost": 1, "san_cost": 0,
             "result_text": "丹药入手温热，散发着异香——但你们不知道它的效果，贸然服用恐怕不妥。"},
            {"text": "翻看墙上的炼丹记录", "clue": "xt_danfang_records", "hp_cost": 0, "san_cost": 1,
             "result_text": "记录显示，长老每月都在偷偷炼制一种没有名字的丹药——服用者都是外门弟子。"},
        ],
    },
}

def main():
    changed = 0
    # puzzle
    for mid, data in PUZZLE.items():
        d, p = load(mid)
        if d.get("puzzle"):
            print(f"  - {mid} 已有 puzzle，跳过")
            continue
        d["module_type"] = "puzzle"
        d["puzzle"] = data
        save(mid, d)
        changed += 1
    # social
    for mid, data in SOCIAL.items():
        d, p = load(mid)
        if d.get("social"):
            print(f"  - {mid} 已有 social，跳过")
            continue
        d["module_type"] = "social"
        d["social"] = data
        save(mid, d)
        changed += 1
    # choice
    for mid, data in CHOICE.items():
        d, p = load(mid)
        if d.get("choice"):
            print(f"  - {mid} 已有 choice，跳过")
            continue
        d["module_type"] = "choice"
        d["choice"] = data
        save(mid, d)
        changed += 1
    # boss phases
    for mid, phases in BOSS_PHASES.items():
        d, p = load(mid)
        if d.get("phase_thresholds"):
            print(f"  - {mid} 已有 phase_thresholds，跳过")
            continue
        d["phase_thresholds"] = phases
        save(mid, d)
        changed += 1
    # interaction
    for mid, data in INTERACTION.items():
        d, p = load(mid)
        if d.get("interaction"):
            print(f"  - {mid} 已有 interaction，跳过")
            continue
        d["interaction"] = data
        save(mid, d)
        changed += 1
    print(f"\n共改造 {changed} 个模块")

if __name__ == "__main__":
    main()
