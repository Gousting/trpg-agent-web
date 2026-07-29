"""分析 SSE 事件流，提取关键测试指标。"""
import sys, json

events = []
current_event = None

for line in sys.stdin:
    line = line.strip()
    if line.startswith("event: "):
        current_event = line[7:]
    elif line.startswith("data: ") and current_event:
        try:
            data = json.loads(line[6:])
        except:
            continue
        events.append((current_event, data))
        current_event = None

# 统计
event_types = {}
for evt_type, _ in events:
    event_types[evt_type] = event_types.get(evt_type, 0) + 1

print("=" * 50)
print("事件统计:")
for k, v in sorted(event_types.items()):
    print(f"  {k}: {v}")

# 关键指标
init = next((d for t, d in events if t == "init"), {})
print(f"\n开场: {init.get('opening','')[:100]}...")

item_pickups = [d for t, d in events if t == "item_pickup"]
print(f"\n物品拾取: {len(item_pickups)} 次")
for ip in item_pickups:
    print(f"  {ip.get('speaker','')} 获得: {ip.get('items',[])}")

damages = [d for t, d in events if t == "damage"]
print(f"\nHP伤害: {len(damages)} 次")
for dmg in damages:
    print(f"  {dmg.get('speaker','')} -{dmg.get('amount',0)} HP")

san_losses = [d for t, d in events if t == "san_loss"]
print(f"\nSAN损失: {len(san_losses)} 次")
for sl in san_losses:
    print(f"  {sl.get('speaker','')} -{sl.get('amount',0)} SAN")

dice_rolls = [d for t, d in events if t == "dice_roll"]
print(f"\n骰子检定: {len(dice_rolls)} 次")
for dr in dice_rolls:
    print(f"  {dr.get('speaker','')}: {dr.get('text','')[:80]}")

room_changes = [d for t, d in events if t == "room_change"]
print(f"\n房间移动: {len(room_changes)} 次")
for rc in room_changes:
    print(f"  → {rc.get('room_name','')}")

# 最终状态
states = [d for t, d in events if t == "kp_stream_end"]
if states:
    final = states[-1].get("state", {})
    print(f"\n最终状态:")
    for name, s in final.items():
        print(f"  {name}: HP={s['hp']}/{s['max_hp']} SAN={s['san']}/{s['max_san']} 物品={s['inventory']}")

# 场景图
scenes = [d for t, d in events if t == "kp_stream_end" and d.get("scene")]
print(f"\n场景图切换: {len(scenes)} 次")
for sc in scenes:
    s = sc.get("scene", {})
    if s:
        print(f"  {s.get('image','')[:60]}")

# 总结
has_items = len(item_pickups) > 0
has_dice = len(dice_rolls) > 0
has_hp_or_san = len(damages) + len(san_losses) > 0
has_rooms = len(room_changes) > 0

print(f"\n{'='*50}")
print(f"物品拾取: {'✅' if has_items else '❌'}")
print(f"骰子检定: {'✅' if has_dice else '❌'}")
print(f"HP/SAN变化: {'✅' if has_hp_or_san else '❌'}")
print(f"房间移动: {'✅' if has_rooms else '❌'}")
all_pass = has_items and has_dice and has_hp_or_san and has_rooms
print(f"\n综合: {'✅ 全部通过' if all_pass else '⚠ 部分未触发'}")
