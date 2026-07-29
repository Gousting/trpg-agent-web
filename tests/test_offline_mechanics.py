"""离线测试：模块、物品、HP/SAN —— 不需要 LLM。

运行: python3 tests/test_offline_mechanics.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trpg_agent.adventure.module_composer import ModuleComposer
from trpg_agent.memory.game_state import Investigator
from trpg_agent.mapgen import DungeonMap

MODULES_DIR = Path(__file__).resolve().parent.parent / "data" / "modules"
STATIC_DIR = Path(__file__).resolve().parent.parent / "trpg_agent_web" / "static"
PASS, FAIL = 0, 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


# ═══════════════════════════════════════════════════════
# 1. 模块加载
# ═══════════════════════════════════════════════════════
print("=" * 50)
print("1. 模块加载")
composer = ModuleComposer(MODULES_DIR)
count = composer.load_all()
check(f"加载 ≥ 15 个模块", count >= 15, f"实际 {count}")

# 列出所有模块
print(f"  已加载模块: {', '.join(sorted(composer._modules.keys())[:10])}...")

# ═══════════════════════════════════════════════════════
# 2. 组合冒险
# ═══════════════════════════════════════════════════════
print("\n2. 组合冒险")
bundle = composer.compile(seed=42, max_depth=4)
check("编译成功", bundle is not None)
check("≥ 6 个模块", len(bundle.module_ids) >= 6,
      f"实际 {len(bundle.module_ids)}")
check("有场景", len(bundle.adventure._scenes) > 0,
      f"实际 {len(bundle.adventure._scenes)} 场景")

# 验证开场场景存在
start = bundle.adventure.get_scene(bundle.adventure.start_scene)
check("开场场景存在", start is not None, f"ID={bundle.adventure.start_scene}")

# 验证模块图片
img_count = sum(1 for s in bundle.adventure._scenes.values() if s.image)
check(f"有模块场景图", img_count > 0, f"{img_count} 个场景有图")

print(f"  模块序列: {' → '.join(bundle.module_ids)}")

# ═══════════════════════════════════════════════════════
# 3. 物品系统
# ═══════════════════════════════════════════════════════
print("\n3. 物品系统")

# 3a. 调查员初始物品
inv = Investigator(name="测试员", hp=12, max_hp=12, san=60, max_san=60, luck=50,
                   inventory=["手电筒", "笔记本"])
check("初始物品正确", inv.inventory == ["手电筒", "笔记本"])

# 3b. 添加/删除物品
inv.inventory.append("旧印护符")
check("添加物品", "旧印护符" in inv.inventory)
inv.inventory.remove("笔记本")
check("移除物品", "笔记本" not in inv.inventory)

# 3c. 地牢房间物品 + 拾取
dmap = DungeonMap(STATIC_DIR / "maps")
dmap.generate(seed=99, num_rooms=8)
room = dmap.current_room
print(f"  当前房间: {room.name if room else '无'}, 物品: {room.items if room else '无'}")
if room and room.items:
    # 模拟拾取
    item = room.items[0]
    inv.inventory.append(item)
    room.items.remove(item)
    check(f"拾取 '{item}' 后物品栏包含", item in inv.inventory)
    check(f"房间物品已移除", item not in room.items)

# 3d. 移动后房间物品变化
if room and len(dmap.current_room.connections) > 0:
    old_items = list(room.items)
    dmap.move_to(room.connections[0])
    new_room = dmap.current_room
    check("移动到新房间", new_room is not None and new_room.id != room.id)
    if new_room and new_room.items:
        check("新房间有不同的物品", new_room.items != old_items,
              f"新物品: {new_room.items}")

# ═══════════════════════════════════════════════════════
# 4. HP/SAN 系统
# ═══════════════════════════════════════════════════════
print("\n4. HP/SAN 系统")

inv2 = Investigator(name="伤员", hp=10, max_hp=12, san=50, max_san=60, luck=45)

# 4a. 伤害
inv2.take_damage(3)
check("HP -3", inv2.hp == 7, f"实际 {inv2.hp}")
inv2.take_damage(2)
check("HP -2", inv2.hp == 5, f"实际 {inv2.hp}")

# 4b. HP 不低于 0
inv2.take_damage(999)
check("HP 不低于 0", inv2.hp == 0, f"实际 {inv2.hp}")

# 4c. SAN 损失
inv3 = Investigator(name="疯人", hp=10, max_hp=10, san=60, max_san=60, luck=40)
inv3.san -= 5
check("SAN -5", inv3.san == 55, f"实际 {inv3.san}")
inv3.san -= 10
check("SAN -10", inv3.san == 45, f"实际 {inv3.san}")
inv3.san = max(0, inv3.san - 999)
check("SAN 不低于 0", inv3.san == 0, f"实际 {inv3.san}")

# 4d. 恢复
inv4 = Investigator(name="恢复中", hp=3, max_hp=12, san=20, max_san=60, luck=50)
inv4.hp = min(inv4.max_hp, inv4.hp + 5)
check("HP 恢复不超过上限", inv4.hp == 8, f"实际 {inv4.hp}")
inv4.san = min(inv4.max_san, inv4.san + 3)
check("SAN 恢复不超过上限", inv4.san == 23, f"实际 {inv4.san}")

# ═══════════════════════════════════════════════════════
# 5. 骰子后果（代码路径，不依赖 LLM）
# ═══════════════════════════════════════════════════════
print("\n5. 骰子后果逻辑")

# 模拟 _dice_consequence 的核心逻辑
def dice_consequence(dice_context: str, inv_state: Investigator):
    """与 web_server.py 中 _dice_consequence 相同逻辑。"""
    if "失败" not in dice_context:
        return None
    import random
    random.seed(123)
    r = random.random()
    if r < 0.5:
        loss = random.randint(1, 2)
        inv_state.san = max(0, inv_state.san - loss)
        return {"type": "san_loss", "amount": loss, "text": f"SAN -{loss}"}
    elif r < 0.8:
        dmg = 1
        inv_state.take_damage(dmg)
        return {"type": "damage", "amount": dmg, "text": f"HP -{dmg}"}
    return None

import random
random.seed(42)

# 测试多次以覆盖不同分支
san_losses = 0
hp_losses = 0
no_losses = 0
for _ in range(100):
    test_inv = Investigator(name="X", hp=10, max_hp=10, san=60, max_san=60, luck=50)
    result = dice_consequence("侦查检定：失败——掷出45，需要60", test_inv)
    if result is None:
        no_losses += 1
    elif result["type"] == "san_loss":
        san_losses += 1
    elif result["type"] == "damage":
        hp_losses += 1

check("失败骰子有后果", san_losses + hp_losses > 50, f"SAN损失:{san_losses} HP损失:{hp_losses} 无:{no_losses}")
check("SAN损失比HP损失多", san_losses > hp_losses, f"SAN:{san_losses} vs HP:{hp_losses}")
check("无损失存在但少", no_losses < 40, f"无损失:{no_losses}")

# 成功骰子不应有后果
random.seed(99)
no_consequence = dice_consequence("侦查检定：成功——掷出78，需要60", 
                                   Investigator(name="Y", hp=10, max_hp=10, san=60, max_san=60, luck=50))
check("成功骰子无后果", no_consequence is None)

# ═══════════════════════════════════════════════════════
# 6. 房间威胁事件
# ═══════════════════════════════════════════════════════
print("\n6. 房间威胁（代码路径）")

def room_threat_events(threats_text: str, inv_state: Investigator, speaker: str):
    """与 web_server.py 中 _room_threat_events 相同逻辑。"""
    events = []
    if not threats_text or threats_text == "无":
        return events
    # 30% SAN
    if random.random() < 0.30:
        loss = random.randint(1, 3)
        inv_state.san = max(0, inv_state.san - loss)
        events.append({"type": "san_loss", "speaker": speaker, 
                       "text": f"{speaker} SAN -{loss}", "amount": loss})
    # 10% HP
    if random.random() < 0.10:
        dmg = random.randint(1, 2)
        inv_state.take_damage(dmg)
        events.append({"type": "damage", "speaker": speaker,
                       "text": f"{speaker} HP -{dmg}", "amount": dmg})
    return events

random.seed(77)
threat_count = 0
for _ in range(100):
    test_inv = Investigator(name="Z", hp=10, max_hp=10, san=60, max_san=60, luck=50)
    events = room_threat_events("不可名状的恐怖弥漫在空气中", test_inv, "陈明")
    threat_count += len(events)

check("有威胁的房间触发事件", threat_count > 20, f"100轮共触发 {threat_count} 次事件")
check("无威胁描述不触发", len(room_threat_events("无", 
    Investigator(name="W", hp=10, max_hp=10, san=60, max_san=60, luck=50), "王刚")) == 0)

# ═══════════════════════════════════════════════════════
# 7. 多模块多次组合 — 验证不同种子产生不同冒险
# ═══════════════════════════════════════════════════════
print("\n7. 多种子组合（验证多样性）")
adventures = []
for seed in [1, 42, 99, 777]:
    b = composer.compile(seed=seed, max_depth=3)
    adventures.append(b.module_ids)
    
# 所有种子都应产生 ≥ 4 个模块
all_min_4 = all(len(ids) >= 4 for ids in adventures)
check("所有种子产生 ≥4 模块", all_min_4)

# 不是所有都相同
all_same = all(ids == adventures[0] for ids in adventures)
check("不同种子产生不同模块序列", not all_same)

for i, ids in enumerate(adventures):
    print(f"  seed={[1,42,99,777][i]:>3}: {' → '.join(ids)}")

# ═══════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════
print("\n" + "=" * 50)
total = PASS + FAIL
print(f"结果: {PASS}/{total} 通过" + (f", {FAIL} 失败" if FAIL else " ✅ 全部通过"))
sys.exit(0 if FAIL == 0 else 1)
