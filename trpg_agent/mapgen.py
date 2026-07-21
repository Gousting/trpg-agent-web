"""Roguelike 地图生成器 — 每次跑团生成不同的疗养院地图。

架构：节点图 + 瓦片网格。房间矩形区域 + 走廊连接。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# 瓦片网格
GRID_W, GRID_H = 48, 36
TILE_FLOOR = 0
TILE_WALL = 1
TILE_CORRIDOR = 2
TILE_DOOR = 3


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
    connections: list[str] = field(default_factory=list)
    visited: bool = False
    cleared: bool = False
    # 布局
    x: float = 0.0
    y: float = 0.0
    # 瓦片坐标
    grid_x: int = 0
    grid_y: int = 0
    grid_w: int = 5
    grid_h: int = 4


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
# 瓦片地图生成
# ═══════════════════════════════════════════════════════

ROOM_SIZES = {
    "entrance": (6, 5), "corridor": (5, 2), "ward": (5, 4),
    "lab": (5, 4), "morgue": (5, 4), "office": (4, 4),
    "basement": (7, 6), "supply": (4, 4),
}

TILE_COLORS = {
    "entrance": "#3a3020", "corridor": "#1a1815", "ward": "#1a2a1a",
    "lab": "#1a1a2a", "morgue": "#151515", "office": "#252015",
    "basement": "#2a0a0a", "supply": "#252015",
}


def generate_tile_map(seed: int | None = None, num_rooms: int = 10) -> tuple[GameMap, list[list[int]]]:
    """生成瓦片网格地图。返回 (GameMap, grid[GRID_H][GRID_W])。

    grid 值: 0=地板, 1=墙, 2=走廊, 3=门
    """
    if seed is not None:
        random.seed(seed)

    gmap = GameMap()
    grid = [[TILE_WALL for _ in range(GRID_W)] for _ in range(GRID_H)]

    # 1. 生成房间（不重叠放置）
    room_id = 0
    placed: list[tuple[int, int, int, int, str]] = []  # (gx, gy, gw, gh, rid)

    # 入口在中心偏左
    ew, eh = ROOM_SIZES["entrance"]
    entry_gx = GRID_W // 2 - ew // 2
    entry_gy = GRID_H // 2 - eh // 2
    _carve_room(grid, entry_gx, entry_gy, ew, eh)
    placed.append((entry_gx, entry_gy, ew, eh, "0"))

    entry = _make_room("entrance", "0")
    entry.grid_x, entry.grid_y = entry_gx, entry_gy
    entry.grid_w, entry.grid_h = ew, eh
    entry.visited = True
    gmap.rooms["0"] = entry
    gmap.start_room_id = "0"
    gmap.current_room_id = "0"
    room_id += 1

    # 2. 从已放置房间向外生成新房间
    room_types_pool = ["corridor", "ward", "lab", "office", "supply", "ward", "corridor"]
    random.shuffle(room_types_pool)

    for i in range(num_rooms - 2):
        rtype = room_types_pool[i % len(room_types_pool)]
        rw, rh = ROOM_SIZES.get(rtype, (4, 4))

        # 选一个已放置房间，从它的一侧生成
        for attempt in range(20):
            parent = random.choice(placed)
            px, py, pw, ph, _ = parent
            side = random.choice(["north", "south", "east", "west"])
            gap = random.randint(2, 4)

            if side == "north":
                gx = px + random.randint(0, max(0, pw - rw))
                gy = py - rh - gap
            elif side == "south":
                gx = px + random.randint(0, max(0, pw - rw))
                gy = py + ph + gap
            elif side == "east":
                gx = px + pw + gap
                gy = py + random.randint(0, max(0, ph - rh))
            else:  # west
                gx = px - rw - gap
                gy = py + random.randint(0, max(0, ph - rh))

            gx = max(1, min(GRID_W - rw - 1, gx))
            gy = max(1, min(GRID_H - rh - 1, gy))

            if _can_place(grid, gx, gy, rw, rh):
                _carve_room(grid, gx, gy, rw, rh)
                rid_str = str(room_id)
                placed.append((gx, gy, rw, rh, rid_str))

                room = _make_room(rtype, rid_str)
                room.grid_x, room.grid_y = gx, gy
                room.grid_w, room.grid_h = rw, rh
                gmap.rooms[rid_str] = room

                # 连接走廊
                _carve_corridor(grid, px + pw // 2, py + ph // 2, gx + rw // 2, gy + rh // 2)
                _connect(gmap.rooms[parent[4]], room)
                room_id += 1
                break

    # Boss 房（最远的角落）
    rw, rh = ROOM_SIZES["basement"]
    farthest = max(placed, key=lambda p: abs(p[0] - entry_gx) + abs(p[1] - entry_gy))
    fx, fy, fw, fh, fid = farthest

    # 放在 farthest 的反方向
    dx = fx - entry_gx
    dy = fy - entry_gy
    boss_gx = entry_gx - dx // 2
    boss_gy = entry_gy - dy // 2
    boss_gx = max(2, min(GRID_W - rw - 2, boss_gx))
    boss_gy = max(2, min(GRID_H - rh - 2, boss_gy))

    if not _can_place(grid, boss_gx, boss_gy, rw, rh):
        # 备选：放在边缘
        for ex, ey in [(2, 2), (GRID_W - rw - 2, 2), (2, GRID_H - rh - 2), (GRID_W - rw - 2, GRID_H - rh - 2)]:
            if _can_place(grid, ex, ey, rw, rh):
                boss_gx, boss_gy = ex, ey
                break

    _carve_room(grid, boss_gx, boss_gy, rw, rh)
    boss = _make_room("basement", "_boss")
    boss.grid_x, boss.grid_y = boss_gx, boss_gy
    boss.grid_w, boss.grid_h = rw, rh
    gmap.rooms["_boss"] = boss
    gmap.boss_room_id = "_boss"
    _carve_corridor(grid, fx + fw // 2, fy + fh // 2, boss_gx + rw // 2, boss_gy + rh // 2)
    _connect(gmap.rooms[fid], boss)

    return gmap, grid


def _can_place(grid: list[list[int]], gx: int, gy: int, w: int, h: int) -> bool:
    if gx < 1 or gy < 1 or gx + w >= GRID_W - 1 or gy + h >= GRID_H - 1:
        return False
    for y in range(gy - 1, gy + h + 1):
        for x in range(gx - 1, gx + w + 1):
            if 0 <= y < GRID_H and 0 <= x < GRID_W:
                if grid[y][x] != TILE_WALL:
                    return False
    return True


def _carve_room(grid: list[list[int]], gx: int, gy: int, w: int, h: int):
    for y in range(gy, gy + h):
        for x in range(gx, gx + w):
            grid[y][x] = TILE_FLOOR


def _carve_corridor(grid: list[list[int]], x1: int, y1: int, x2: int, y2: int):
    """L 形走廊。"""
    cx, cy = x1, y1
    # 水平再垂直
    while cx != x2:
        cx += 1 if x2 > cx else -1
        if grid[cy][cx] == TILE_WALL:
            grid[cy][cx] = TILE_CORRIDOR
    while cy != y2:
        cy += 1 if y2 > cy else -1
        if grid[cy][cx] == TILE_WALL:
            grid[cy][cx] = TILE_CORRIDOR


# ═══════════════════════════════════════════════════════
# 序列化
# ═══════════════════════════════════════════════════════

def map_to_dict(gmap: GameMap) -> dict:
    return {
        "current_room_id": gmap.current_room_id,
        "start_room_id": gmap.start_room_id,
        "boss_room_id": gmap.boss_room_id,
        "rooms": {
            rid: {
                "id": r.id, "name": r.name, "type": r.room_type,
                "x": r.x, "y": r.y,
                "grid_x": r.grid_x, "grid_y": r.grid_y,
                "grid_w": r.grid_w, "grid_h": r.grid_h,
                "visited": r.visited, "cleared": r.cleared,
                "connections": r.connections,
                "items": r.items,
                "threats": [t[0] for t in r.threats],
            }
            for rid, r in gmap.rooms.items()
        },
    }


# ═══════════════════════════════════════════════════════
# PNG 地图渲染（Pillow）
# ═══════════════════════════════════════════════════════

from pathlib import Path

_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
_TILE_PX = 20  # 每格像素

_RGB = {
    TILE_WALL: (8, 8, 8),
    TILE_CORRIDOR: (20, 18, 16),
    TILE_DOOR: (55, 40, 20),
    "entrance": (70, 52, 35),
    "corridor": (25, 22, 20),
    "ward": (25, 42, 25),
    "lab": (25, 25, 45),
    "morgue": (20, 20, 20),
    "office": (42, 35, 20),
    "basement": (42, 10, 10),
    "supply": (42, 40, 25),
}

_ROOM_ICONS = {
    "entrance": "🚪", "ward": "🛏", "lab": "🔬",
    "morgue": "⚰", "office": "📁", "basement": "💀", "supply": "📦",
}


def render_map_png(gmap: GameMap, grid: list[list[int]], output_path: str) -> str:
    """渲染 48×36 瓦片网格为高清 PNG 地图。

    每格 20px → 960×720，带纹理噪点、房间标签、迷雾、当前房间高亮。
    返回 output_path。
    """
    from PIL import Image, ImageDraw, ImageFont

    gh, gw = len(grid), len(grid[0])
    w, h = gw * _TILE_PX, gh * _TILE_PX

    img = Image.new("RGB", (w, h), (4, 4, 4))
    draw = ImageDraw.Draw(img)

    # 瓦片→房间映射
    tile_room: dict[tuple[int, int], Room] = {}
    for rid, room in gmap.rooms.items():
        for dy in range(room.grid_h):
            for dx in range(room.grid_w):
                tile_room[(room.grid_x + dx, room.grid_y + dy)] = room

    # ── 逐格绘制 ──
    for gy in range(gh):
        for gx in range(gw):
            tile = grid[gy][gx]
            room = tile_room.get((gx, gy))
            px, py = gx * _TILE_PX, gy * _TILE_PX

            if tile == TILE_WALL:
                color = _RGB[TILE_WALL]
            elif room:
                if room.visited:
                    color = _RGB.get(room.room_type, (30, 30, 30))
                else:
                    # 迷雾
                    edge = (gx in (room.grid_x, room.grid_x + room.grid_w - 1) or
                            gy in (room.grid_y, room.grid_y + room.grid_h - 1))
                    color = (15, 15, 15) if edge else (8, 8, 8)
            elif tile == TILE_CORRIDOR:
                color = _RGB[TILE_CORRIDOR]
            elif tile == TILE_DOOR:
                color = _RGB[TILE_DOOR]
            else:
                color = (20, 18, 16)

            draw.rectangle([px, py, px + _TILE_PX - 1, py + _TILE_PX - 1], fill=color)

            # 纹理噪点
            if not room or room.visited:
                noise = ((gx * 7 + gy * 13) % 12) - 6
                r = max(0, min(255, color[0] + noise))
                g = max(0, min(255, color[1] + noise))
                b = max(0, min(255, color[2] + noise))
                draw.rectangle([px, py, px + _TILE_PX - 1, py + _TILE_PX - 1],
                               fill=(r, g, b), outline=None)

    # 网格线
    for gy in range(gh + 1):
        draw.line([0, gy * _TILE_PX, w, gy * _TILE_PX], fill=(12, 12, 12))
    for gx in range(gw + 1):
        draw.line([gx * _TILE_PX, 0, gx * _TILE_PX, h], fill=(12, 12, 12))

    # ── 走廊连线 ──
    drawn = set()
    for room in gmap.rooms.values():
        if not room.visited:
            continue
        for cid in room.connections:
            other = gmap.rooms.get(cid)
            if not other or not other.visited:
                continue
            key = tuple(sorted([room.id, cid]))
            if key in drawn:
                continue
            drawn.add(key)
            cx1 = (room.grid_x + room.grid_w / 2) * _TILE_PX
            cy1 = (room.grid_y + room.grid_h / 2) * _TILE_PX
            cx2 = (other.grid_x + other.grid_w / 2) * _TILE_PX
            cy2 = (other.grid_y + other.grid_h / 2) * _TILE_PX
            draw.line([cx1, cy1, cx2, cy2], fill=(80, 60, 40), width=max(2, _TILE_PX // 3))

    # ── 房间标签 ──
    try:
        font = ImageFont.truetype(_FONT_PATH, 16)
        font_sm = ImageFont.truetype(_FONT_PATH, 12)
    except OSError:
        font = ImageFont.load_default()
        font_sm = font

    for room in gmap.rooms.values():
        if not room.visited:
            continue
        rx = room.grid_x * _TILE_PX
        ry = room.grid_y * _TILE_PX
        rw = room.grid_w * _TILE_PX
        rh = room.grid_h * _TILE_PX
        is_current = room.id == gmap.current_room_id

        # 边框
        border_color = (212, 160, 64) if is_current else (50, 50, 50)
        draw.rectangle([rx, ry, rx + rw - 1, ry + rh - 1], outline=border_color, width=2)

        # 当前房间光晕
        if is_current:
            for i in range(4, 0, -1):
                alpha = max(0, 80 - i * 20)
                glow_rect = [rx - i, ry - i, rx + rw + i - 1, ry + rh + i - 1]
                draw.rectangle(glow_rect, outline=(255, 180, 50, alpha), width=1)

        # Boss 红光
        if room.id == gmap.boss_room_id:
            for i in range(3, 0, -1):
                draw.rectangle(
                    [rx - i, ry - i, rx + rw + i - 1, ry + rh + i - 1],
                    outline=(180, 20, 20), width=1
                )

        # 标签
        label = room.name.split("(")[0] if "(" in room.name else room.name
        icon = _ROOM_ICONS.get(room.room_type, "")

        if rw > 80 and rh > 60:
            tw = draw.textlength(label, font=font)
            draw.text((rx + (rw - tw) / 2, ry + rh / 2 + 4), label, fill=(170, 170, 170), font=font)
            if icon:
                iw = draw.textlength(icon, font=font_sm)
                draw.text((rx + (rw - iw) / 2, ry + rh / 2 - 18), icon, fill=(200, 200, 200), font=font_sm)
        elif rw > 60:
            tw = draw.textlength(label, font=font_sm)
            draw.text((rx + (rw - tw) / 2, ry + rh / 2 + 2), label, fill=(170, 170, 170), font=font_sm)

    # 保存
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG")
    return output_path


# ═══════════════════════════════════════════════════════
# dungeongen 桥接渲染
# ═══════════════════════════════════════════════════════

def _render_dungeongen_map(gmap: GameMap, output_path: str) -> str:
    """用 dungeongen 渲染 OPD 手绘风格地图 + 走廊 + 游戏状态叠加。
    
    dungeongen 内部使用 64px/格 的固定缩放（与画布尺寸无关）。
    我们在 post-process 中画走廊、迷雾、高亮。
    
    Args:
        gmap: 已生成的 GameMap
        output_path: 输出 PNG 路径
    
    Returns:
        output_path
    """
    import dungeongen as dg
    from PIL import Image, ImageDraw

    CELL = 64  # dungeongen 的固定像素/格比例
    BORDER = 3

    canvas_w = (GRID_W + BORDER * 2) * CELL
    canvas_h = (GRID_H + BORDER * 2) * CELL

    opts = dg.Options(
        canvas_width=canvas_w,
        canvas_height=canvas_h,
        grid_style="DOTS",
    )
    dg_map = dg.Map(opts)

    # ── 创建房间 ──────────────────────────────────
    dg_rooms: dict[str, dg.Room] = {}
    for rid, room in gmap.rooms.items():
        gx = float(room.grid_x + BORDER)
        gy = float(room.grid_y + BORDER)
        dg_room = dg_map.create_rectangular_room(
            gx, gy, float(room.grid_w), float(room.grid_h),
        )
        dg_rooms[rid] = dg_room

    # ── 注册连接 ──────────────────────────────────
    drawn_pairs = set()
    for rid, room in gmap.rooms.items():
        dg_room = dg_rooms[rid]
        for conn_id in room.connections:
            pair = tuple(sorted([rid, conn_id]))
            if pair in drawn_pairs:
                continue
            drawn_pairs.add(pair)
            if conn_id in dg_rooms:
                dg_room.connect_to(dg_rooms[conn_id])

    # ── dungeongen 渲染 ──────────────────────────
    dg_map.render_to_png(output_path, width=canvas_w, height=canvas_h)

    # ── 叠加层：走廊 + 迷雾 + 高亮 ────────────────
    img = Image.open(output_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def _room_rect(room) -> tuple[int, int, int, int]:
        rx = (room.grid_x + BORDER) * CELL
        ry = (room.grid_y + BORDER) * CELL
        return rx, ry, rx + room.grid_w * CELL, ry + room.grid_h * CELL

    def _room_center(room) -> tuple[int, int]:
        rx, ry, rxx, ryy = _room_rect(room)
        return (rx + rxx) // 2, (ry + ryy) // 2

    # 走廊连线（细线，避免喧宾夺主）
    for rid, room in gmap.rooms.items():
        for conn_id in room.connections:
            if conn_id < rid:
                continue
            other = gmap.rooms.get(conn_id)
            if not other:
                continue
            c1 = _room_center(room)
            c2 = _room_center(other)
            draw.line([c1, c2], fill=(100, 75, 50, 160), width=max(3, CELL // 6))

    # 迷雾和高亮（加 2px 内缩避免溢出到网格线）
    INSET = 3
    for rid, room in gmap.rooms.items():
        rx, ry, rxx, ryy = _room_rect(room)
        if not room.visited:
            draw.rectangle(
                [rx + INSET, ry + INSET, rxx - INSET, ryy - INSET],
                fill=(0, 0, 0, 65),
            )
        elif rid == gmap.current_room_id:
            for i in range(6, 0, -1):
                a = max(0, 70 - i * 12)
                draw.rectangle(
                    [rx - i * 3, ry - i * 3, rxx + i * 3, ryy + i * 3],
                    outline=(255, 180, 50, a), width=2,
                )
        if rid == gmap.boss_room_id and room.visited:
            for i in range(4, 0, -1):
                draw.rectangle(
                    [rx - i * 3, ry - i * 3, rxx + i * 3, ryy + i * 3],
                    outline=(180, 20, 20), width=2,
                )

    img = Image.alpha_composite(img, overlay)
    img = img.convert("RGB")
    img.save(output_path, "PNG")
    return output_path

class DungeonMap:
    """地牢地图组件 — 封装地图的生成、渲染和状态管理。

    组件化之后，外部代码不再直接操作 GameMap/Room 内部字段，
    而是通过 DungeonMap 的 API 完成所有地图操作。

    用法::

        dmap = DungeonMap(Path("static/maps"))
        dmap.generate(seed=42)
        dmap.render()
        # ... 游戏循环中 ...
        dmap.move_to("3")   # 自动重渲染、标记 visited
        frontend_data = dmap.to_frontend()
    """

    def __init__(self, output_dir: str | Path, base_name: str = "current"):
        import uuid
        self.output_dir = Path(output_dir)
        self.base_name = base_name
        self._session_id = uuid.uuid4().hex[:8]
        self.gmap: GameMap | None = None
        self.grid: list[list[int]] | None = None
        self._render_version: int = 0

    # ── 属性 ──────────────────────────────────────

    @property
    def image_path(self) -> Path:
        return self.output_dir / f"{self.base_name}_{self._session_id}.png"

    @property
    def relative_path(self) -> str:
        """返回带会话 ID 的唯一路径，彻底避免缓存。"""
        return f"/static/maps/{self.base_name}_{self._session_id}.png?v={self._render_version}"

    @property
    def current_room(self) -> Room | None:
        if self.gmap and self.gmap.current_room_id in self.gmap.rooms:
            return self.gmap.rooms[self.gmap.current_room_id]
        return None

    @property
    def room_count(self) -> int:
        return len(self.gmap.rooms) if self.gmap else 0

    @property
    def visited_count(self) -> int:
        if not self.gmap:
            return 0
        return sum(1 for r in self.gmap.rooms.values() if r.visited)

    # ── 生命周期 ──────────────────────────────────

    def generate(self, seed: int | None = None, num_rooms: int = 10) -> "DungeonMap":
        """生成新的地牢地图，返回 self 支持链式调用。"""
        self.gmap, self.grid = generate_tile_map(seed=seed, num_rooms=num_rooms)
        return self

    def render(self) -> Path:
        """渲染当前地图到 PNG 文件（dungeongen OPD 手绘风格 + 游戏状态叠加）。
        
        返回绝对路径。"""
        if self.gmap is None or self.grid is None:
            raise RuntimeError("尚未生成地图，请先调用 generate()")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        _render_dungeongen_map(self.gmap, str(self.image_path))
        self._render_version += 1
        return self.image_path

    def move_to(self, room_id: str) -> Path:
        """移动到指定房间：更新状态、标记 visited、重新渲染。

        Returns:
            渲染后的图片路径。

        Raises:
            ValueError: 房间不存在
            RuntimeError: 尚未生成地图
        """
        if self.gmap is None:
            raise RuntimeError("尚未生成地图，请先调用 generate()")
        if room_id not in self.gmap.rooms:
            raise ValueError(f"房间 '{room_id}' 不存在（可用: {sorted(self.gmap.rooms.keys())}）")
        self.gmap.current_room_id = room_id
        room = self.gmap.rooms[room_id]
        if not room.visited:
            room.visited = True
        return self.render()

    # ── 查询 ──────────────────────────────────────

    def get_room(self, room_id: str) -> Room | None:
        return self.gmap.rooms.get(room_id) if self.gmap else None

    def is_boss_room(self, room_id: str) -> bool:
        return self.gmap is not None and room_id == self.gmap.boss_room_id

    def is_start_room(self, room_id: str) -> bool:
        return self.gmap is not None and room_id == self.gmap.start_room_id

    # ── 序列化 ────────────────────────────────────

    def to_dict(self) -> dict:
        """序列化为字典（含完整房间数据）。"""
        if self.gmap is None:
            return {"rooms": {}, "current_room_id": "", "start_room_id": "", "boss_room_id": ""}
        return map_to_dict(self.gmap)

    def to_frontend(self) -> dict:
        """返回前端需要的精简地图数据。"""
        if self.gmap is None:
            return {}
        return {
            "map": map_to_dict(self.gmap),
            "image": self.relative_path,
        }

    def room_context(self) -> dict:
        """构建当前房间的上下文信息（用于 prompt 注入）。"""
        if self.gmap is None or self.current_room is None:
            return {"name": "未知", "desc": "", "exits": "", "items": "", "threats": ""}
        room = self.current_room
        exits = []
        for cid in room.connections:
            neighbor = self.gmap.rooms.get(cid)
            if neighbor:
                direction = "已探索" if neighbor.visited else "未探索"
                exits.append(f"{neighbor.name}({direction})")
        return {
            "name": room.name,
            "desc": room.description,
            "exits": ", ".join(exits) if exits else "无",
            "items": ", ".join(room.items) if room.items else "无",
            "threats": ", ".join(t[0] for t in room.threats) if room.threats else "无",
        }

    # ── 房间物品操作 ──────────────────────────────

    def pickup_item(self, item_name: str) -> bool:
        """从当前房间拾取物品。返回是否成功。"""
        room = self.current_room
        if room is None:
            return False
        for item in list(room.items):
            if item_name.lower() in item.lower():
                room.items.remove(item)
                return True
        return False

    def clear_threats(self) -> None:
        """清除当前房间的威胁。"""
        room = self.current_room
        if room:
            room.cleared = True
