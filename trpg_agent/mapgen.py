"""Roguelike 地图生成器 — 每次跑团生成不同的疗养院地图。

架构：节点图（非网格）。每个房间有类型、物品、威胁、线索。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════
# 房间模板
# ═══════════════════════════════════════════════════════

ROOM_TEMPLATES = {
    "entrance": {
        "name_templates": ["门厅", "接待大厅", "玄关"],
        "desc_templates": [
            "破败的接待台后挂着一幅褪色的疗养院平面图，地上散落着发黄的病历。",
            "大门在身后吱呀关上，空气中弥漫着消毒水与霉味。墙上的挂钟停在午夜。",
        ],
        "items_pool": [["手电筒电池"], ["地图碎片"], ["旧报纸"]],
        "clues": ["疗养院于1923年因「患者集体失踪」事件关闭。"],
        "connections": 2,
    },
    "corridor": {
        "name_templates": ["走廊", "过道", "通道"],
        "desc_templates": [
            "狭长的走廊两侧是剥落的墙皮，几盏壁灯忽明忽暗。尽头的黑暗中似乎有什么在移动。",
            "地板在脚下吱嘎作响，墙上挂着面目模糊的肖像画，每走一步都像被画中的眼睛盯着。",
        ],
        "items_pool": [["绷带"], ["火柴"], []],
        "connections": 3,
    },
    "ward": {
        "name_templates": ["病房A", "病房B", "隔离病房", "重症监护室"],
        "desc_templates": [
            "铁架病床锈迹斑斑，床单上有暗褐色的旧渍。床头柜上放着一本日记，最后一页被撕掉了。",
            "约束带还绑在床栏上，但它们的扣环是从内侧被扯断的。窗户用木板钉死。",
        ],
        "items_pool": [
            ["镇静剂", "日记残页"],
            ["急救包", "破旧的圣经"],
            ["吗啡", "患者名牌"],
        ],
        "threats": [
            ("阴影中的低语", "SAN损失: 1d3"),
            ("墙上浮现的人脸", "SAN损失: 1d2"),
        ],
        "connections": 2,
    },
    "lab": {
        "name_templates": ["实验室", "化验室", "药剂室"],
        "desc_templates": [
            "实验台上摆满了烧瓶和试管，一些不明液体还在冒着气泡。黑板上写满了看不懂的公式。",
            "福尔马林罐里浸泡着畸形的标本——它们看起来不像是地球上的生物。",
        ],
        "items_pool": [
            ["化学试剂", "实验笔记"],
            ["解毒剂", "显微镜切片"],
            ["奇怪的血清", "研究员日记"],
        ],
        "clues": [
            "笔记中提到「哈斯塔的恩赐」和「门即将打开」。",
            "化验报告显示：患者血液中含有未知金属元素。",
        ],
        "threats": [
            ("实验体逃脱", "格斗检定"),
            ("毒气泄漏", "急救或闪避检定"),
        ],
        "connections": 2,
    },
    "morgue": {
        "name_templates": ["停尸房", "太平间", "解剖室"],
        "desc_templates": [
            "冷藏柜的门半开着，里面空无一物。但解剖台上躺着一具覆着白布的尸体——它的手指刚刚动了一下。",
            "一股浓郁的福尔马林味扑面而来。墙角堆着几个尸袋，其中一个在微微颤动。",
        ],
        "items_pool": [
            ["解剖刀", "尸检报告"],
            ["防腐液", "身份牌"],
            ["骨锯", "失踪者名单"],
        ],
        "clues": [
            "尸检报告显示：死因为「心脏被外力从胸腔中移除」，但体表无任何伤口。",
            "失踪者名单上有你们三个人的名字。",
        ],
        "threats": [
            ("死者起身", "SAN检定 重大损失"),
            ("尸袋中的东西", "格斗或闪避检定"),
        ],
        "connections": 1,
    },
    "office": {
        "name_templates": ["院长办公室", "档案室", "护士站"],
        "desc_templates": [
            "红木办公桌上积着厚灰，抽屉半开。墙上挂着一张疗养院全体员工合影——所有人的眼睛都被涂黑了。",
            "文件柜里塞满了患者档案。最后一页被人撕走了，但你能看到撕痕下的压痕：一个名字。",
        ],
        "items_pool": [
            ["疗养院钥匙", "院长信件"],
            ["患者档案", "保险柜密码"],
            [".38弹药", "地图"],
        ],
        "clues": [
            "院长信件：「委员会要求立即停止实验。我说过这东西是可控的——它只吃掉了三个护工。」",
        ],
        "connections": 2,
    },
    "basement": {
        "name_templates": ["地下室", "锅炉房", "地下通道"],
        "desc_templates": [
            "楼梯通向一片黑暗。墙壁上覆盖着一层黏滑的苔藓，空气中弥漫着硫磺与腐肉的味道。深处传来节奏性的重击声。",
            "地下空间比建筑本身大得多——这不可能。墙壁上有巨大的爪痕，地板中央画着一个发光的圆圈。",
        ],
        "items_pool": [
            ["仪式匕首", "旧印护符"],
            ["古书残页", "黑色蜡烛"],
        ],
        "clues": [
            "圆圈中央的符号是「黄衣之王」的印记。召唤已经开始了——你们必须在下一个满月前阻止它。",
        ],
        "threats": [
            ("Boss: 星之眷族", "战斗检定 ×3"),
            ("Boss: 黄衣之王的化身", "SAN检定 永久疯狂风险"),
        ],
        "connections": 1,
    },
    "supply": {
        "name_templates": ["储藏室", "杂物间", "工具房"],
        "desc_templates": [
            "货架上满是积灰的医疗用品。角落里有一台老旧的发电机，旁边放着几桶汽油。",
            "翻倒的工具箱旁边躺着一具穿着护工服的骷髅——它手里攥着一把消防斧。",
        ],
        "items_pool": [
            ["急救包×2", "消防斧"],
            ["绷带×3", "汽油桶", "手电筒"],
            ["霰弹枪", "子弹×6"],
        ],
        "connections": 1,
    },
}


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class Room:
    id: str
    name: str
    room_type: str
    description: str
    items: list[str] = field(default_factory=list)
    clues: list[str] = field(default_factory=list)
    threats: list[tuple[str, str]] = field(default_factory=list)
    connections: list[str] = field(default_factory=list)  # → room_id
    visited: bool = False
    cleared: bool = False
    x: float = 0.0
    y: float = 0.0


@dataclass
class GameMap:
    rooms: dict[str, Room] = field(default_factory=dict)
    current_room_id: str = ""
    start_room_id: str = ""
    boss_room_id: str = ""


# ═══════════════════════════════════════════════════════
# 生成器
# ═══════════════════════════════════════════════════════

def _pick(items):
    return random.choice(items) if items else ""


def _roll_chance(pct: float) -> bool:
    return random.random() < pct


def generate_map(seed: int | None = None, num_rooms: int = 10) -> GameMap:
    """生成一个疗养院地图。每次调用产生不同布局。

    Args:
        seed: 随机种子（None=每次不同）
        num_rooms: 房间数量（含入口和Boss房）
    """
    if seed is not None:
        random.seed(seed)

    gmap = GameMap()
    room_id = 0

    # 1. 入口
    entry = _make_room("entrance", str(room_id))
    gmap.rooms[str(room_id)] = entry
    gmap.start_room_id = str(room_id)
    gmap.current_room_id = str(room_id)
    entry.visited = True
    room_id += 1

    # 2. Boss 房（最后生成）
    boss = _make_room("basement", "_boss")
    gmap.boss_room_id = "_boss"

    # 3. 中间房间
    room_types_pool = ["corridor", "ward", "lab", "office", "supply", "ward", "corridor", "corridor"]
    random.shuffle(room_types_pool)

    for i in range(num_rooms - 2):
        rtype = room_types_pool[i % len(room_types_pool)]
        room = _make_room(rtype, str(room_id))
        gmap.rooms[str(room_id)] = room
        room_id += 1

    # Boss 房间
    gmap.rooms["_boss"] = boss

    # 4. 生成连接（保证连通性：每个房间至少连一个）
    all_ids = list(gmap.rooms.keys())
    connected: set[str] = {gmap.start_room_id}

    for rid in all_ids:
        if rid == gmap.start_room_id:
            continue
        # 随机连到一个已连接的房间
        candidate = random.choice(list(connected))
        _connect(gmap.rooms[candidate], gmap.rooms[rid])
        connected.add(rid)

    # 额外随机连接（30% 概率增加回路）
    for rid in all_ids:
        room = gmap.rooms[rid]
        if len(room.connections) < 3 and _roll_chance(0.3):
            other = random.choice([r for r in all_ids if r != rid and r not in room.connections])
            _connect(room, gmap.rooms[other])

    # Boss 房只能从特定房间进入（模拟深度探索）
    mid_rooms = [r for r in all_ids if r != gmap.start_room_id and r != gmap.boss_room_id]
    if mid_rooms:
        # 从最深（连接最少）的房间连到 Boss
        mid_rooms.sort(key=lambda r: len(gmap.rooms[r].connections))
        _connect(gmap.rooms[mid_rooms[-1]], boss)

    # 5. 布局坐标（简单的辐射布局）
    _layout(gmap)

    return gmap


def _make_room(rtype: str, rid: str) -> Room:
    t = ROOM_TEMPLATES[rtype]
    name = _pick(t["name_templates"])
    desc = _pick(t["desc_templates"])
    items = _pick(t.get("items_pool", [[]]))
    clues = list(t.get("clues", []))
    threats = list(t.get("threats", []))

    # 30% 概率不放物品
    if items and _roll_chance(0.3):
        items = []

    return Room(
        id=rid,
        name=f"{name}({rid})" if rid not in ("_boss",) else f"{name}",
        room_type=rtype,
        description=desc,
        items=items,
        clues=clues,
        threats=threats,
    )


def _connect(a: Room, b: Room):
    if b.id not in a.connections:
        a.connections.append(b.id)
    if a.id not in b.connections:
        b.connections.append(a.id)


def _layout(gmap: GameMap):
    """简单辐射布局——入口在中心，其他房间围绕。"""
    import math
    rooms = list(gmap.rooms.values())
    start = gmap.rooms[gmap.start_room_id]
    start.x, start.y = 250, 200

    # BFS 分层放置
    placed = {start.id}
    queue = [(start, 0, 0)]
    layer = 0
    radius = 120

    while queue:
        room, layer_idx, angle_offset = queue.pop(0)
        layer_idx += 1
        children = [gmap.rooms[c] for c in room.connections if c not in placed]
        n = len(children)
        for i, child in enumerate(children):
            angle = angle_offset + (2 * math.pi * i / max(n, 1))
            child.x = room.x + radius * math.cos(angle)
            child.y = room.y + radius * math.sin(angle)
            placed.add(child.id)
            queue.append((child, layer_idx, angle))
        radius = max(80, radius - 8)


# ═══════════════════════════════════════════════════════
# 序列化（给前端）
# ═══════════════════════════════════════════════════════

def map_to_dict(gmap: GameMap) -> dict:
    return {
        "current_room_id": gmap.current_room_id,
        "start_room_id": gmap.start_room_id,
        "boss_room_id": gmap.boss_room_id,
        "rooms": {
            rid: {
                "id": r.id,
                "name": r.name,
                "type": r.room_type,
                "x": r.x,
                "y": r.y,
                "visited": r.visited,
                "cleared": r.cleared,
                "connections": r.connections,
                "items": r.items,
                "threats": [t[0] for t in r.threats],
            }
            for rid, r in gmap.rooms.items()
        },
    }
