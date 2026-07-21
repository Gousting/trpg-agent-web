"""DungeonMap 组件全面测试 — 覆盖生成、渲染、状态管理、序列化、边界条件。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PIL import Image

from trpg_agent.mapgen import DungeonMap, GameMap, Room, generate_tile_map


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════

@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def dmap(tmp_dir):
    return DungeonMap(tmp_dir)


@pytest.fixture
def generated_map(tmp_dir):
    dmap = DungeonMap(tmp_dir)
    dmap.generate(seed=42, num_rooms=10)
    return dmap


# ═══════════════════════════════════════════════════════
# 生命周期测试
# ═══════════════════════════════════════════════════════

class TestLifecycle:
    """测试生成 → 渲染 → 销毁完整流程。"""

    def test_generate_creates_valid_map(self, dmap):
        dmap.generate(seed=42, num_rooms=10)
        assert dmap.gmap is not None
        assert dmap.grid is not None
        assert dmap.room_count == 10
        assert dmap.gmap.start_room_id == "0"
        assert dmap.gmap.current_room_id == "0"
        assert dmap.gmap.boss_room_id == "_boss"

    def test_generate_returns_self_for_chaining(self, dmap):
        result = dmap.generate(seed=1, num_rooms=8)
        assert result is dmap

    def test_generate_different_seeds_produce_different_maps(self, tmp_dir):
        a = DungeonMap(tmp_dir).generate(seed=1, num_rooms=10)
        b = DungeonMap(tmp_dir).generate(seed=2, num_rooms=10)
        a_names = sorted(r.name for r in a.gmap.rooms.values())
        b_names = sorted(r.name for r in b.gmap.rooms.values())
        # 房间名不一定不同（共享模板池），但连接结构应有差异
        a_conns = {rid: sorted(r.connections) for rid, r in a.gmap.rooms.items()}
        b_conns = {rid: sorted(r.connections) for rid, r in b.gmap.rooms.items()}
        assert a_conns != b_conns, "不同 seed 应产生不同连接拓扑"

    def test_generate_same_seed_produces_same_map(self, tmp_dir):
        a = DungeonMap(tmp_dir).generate(seed=42, num_rooms=10)
        b = DungeonMap(tmp_dir).generate(seed=42, num_rooms=10)
        assert a.to_dict() == b.to_dict()

    def test_render_creates_image_file(self, generated_map):
        path = generated_map.render()
        assert path.exists()
        assert path.stat().st_size > 1000  # 至少 1KB

    def test_render_output_is_valid_png(self, generated_map):
        path = generated_map.render()
        img = Image.open(path)
        assert img.format == "PNG"
        assert img.size == (3456, 2688)  # (48+6)*38 × (36+6)*38

    def test_render_before_generate_raises(self, dmap):
        with pytest.raises(RuntimeError, match="尚未生成地图"):
            dmap.render()

    def test_render_multiple_times_overwrites(self, generated_map):
        p1 = generated_map.render()
        mtime1 = p1.stat().st_mtime
        import time
        time.sleep(0.1)
        p2 = generated_map.render()
        assert p1 == p2  # 路径相同
        assert p2.stat().st_mtime > mtime1  # 文件已更新


# ═══════════════════════════════════════════════════════
# 房间移动测试
# ═══════════════════════════════════════════════════════

class TestRoomMovement:
    """测试 move_to 的状态转换。"""

    def test_move_to_valid_room_updates_state(self, generated_map):
        # 找一个与入口相连的房间
        start_room = generated_map.current_room
        assert start_room is not None
        assert start_room.visited is True  # 入口自动 visited
        assert len(start_room.connections) > 0, "入口应有至少一个相邻房间"

        next_room_id = start_room.connections[0]
        generated_map.move_to(next_room_id)

        assert generated_map.gmap.current_room_id == next_room_id
        target = generated_map.get_room(next_room_id)
        assert target.visited is True

    def test_move_to_marks_visited_only_once(self, generated_map):
        start_room = generated_map.current_room
        next_room_id = start_room.connections[0]

        generated_map.move_to(next_room_id)
        assert generated_map.visited_count == 2  # 入口 + 新房间

        # 再次移动到同一房间，visited_count 不变
        generated_map.move_to(next_room_id)
        assert generated_map.visited_count == 2

    def test_move_to_invalid_room_raises(self, generated_map):
        with pytest.raises(ValueError, match="不存在"):
            generated_map.move_to("nonexistent")

    def test_move_to_before_generate_raises(self, dmap):
        with pytest.raises(RuntimeError, match="尚未生成地图"):
            dmap.move_to("0")

    def test_move_to_triggers_rerender(self, generated_map):
        # 先渲染一次，确保文件存在
        generated_map.render()
        start_room = generated_map.current_room
        next_room_id = start_room.connections[0]

        old_mtime = generated_map.image_path.stat().st_mtime
        import time
        time.sleep(0.1)

        generated_map.move_to(next_room_id)
        assert generated_map.image_path.stat().st_mtime > old_mtime

    def test_move_to_renders_new_current_room_highlight(self, generated_map):
        """移动到新房间后图片应有变化（尺寸和格式测试确保渲染成功）。"""
        start_room = generated_map.current_room
        next_room_id = start_room.connections[0]
        generated_map.move_to(next_room_id)
        img = Image.open(generated_map.image_path)
        assert img.size == (3456, 2688)

    def test_visited_count_after_exploring_multiple(self, generated_map):
        start = generated_map.current_room
        conns = list(start.connections)
        assert len(conns) >= 1
        # 走到第一个相邻房间
        generated_map.move_to(conns[0])
        assert generated_map.visited_count >= 2
        # 从当前房间再找非入口的相邻房间
        mid = generated_map.current_room
        assert mid is not None
        next_conns = [c for c in mid.connections
                      if c != generated_map.gmap.start_room_id]
        if next_conns:
            generated_map.move_to(next_conns[0])
            assert generated_map.visited_count >= 3
        # 否则至少有 2 个 visited（入口 + 第一个邻居）


# ═══════════════════════════════════════════════════════
# 序列化测试
# ═══════════════════════════════════════════════════════

class TestSerialization:
    """测试 to_dict / to_frontend / room_context。"""

    def test_to_dict_before_generate_returns_empty(self, dmap):
        result = dmap.to_dict()
        assert result["rooms"] == {}
        assert result["current_room_id"] == ""

    def test_to_dict_has_all_fields(self, generated_map):
        d = generated_map.to_dict()
        assert "rooms" in d
        assert "current_room_id" in d
        assert "start_room_id" in d
        assert "boss_room_id" in d
        assert len(d["rooms"]) == generated_map.room_count

    def test_to_dict_room_has_all_fields(self, generated_map):
        d = generated_map.to_dict()
        first_room = next(iter(d["rooms"].values()))
        assert "id" in first_room
        assert "name" in first_room
        assert "type" in first_room
        assert "visited" in first_room
        assert "cleared" in first_room
        assert "connections" in first_room
        assert "items" in first_room
        assert "threats" in first_room

    def test_to_frontend_includes_image_path(self, generated_map):
        data = generated_map.to_frontend()
        assert "map" in data
        assert "image" in data
        assert data["image"] == generated_map.relative_path

    def test_room_context_has_required_keys(self, generated_map):
        ctx = generated_map.room_context()
        assert "name" in ctx
        assert "desc" in ctx
        assert "exits" in ctx
        assert "items" in ctx
        assert "threats" in ctx
        assert ctx["name"]  # 非空
        assert ctx["desc"]  # 非空

    def test_room_context_reflects_current_room(self, generated_map):
        ctx_before = generated_map.room_context()
        start = generated_map.current_room
        if start and start.connections:
            generated_map.move_to(start.connections[0])
        ctx_after = generated_map.room_context()
        assert ctx_after["name"] != ctx_before["name"], "移动后 room_context 应反映新房间"

    def test_room_context_before_generate(self, dmap):
        ctx = dmap.room_context()
        assert ctx["name"] == "未知"


# ═══════════════════════════════════════════════════════
# 物品和威胁操作测试
# ═══════════════════════════════════════════════════════

class TestItemsAndThreats:
    """测试拾取物品和清除威胁。"""

    def test_pickup_item_removes_from_room(self, generated_map):
        room = generated_map.current_room
        if room and room.items:
            item = room.items[0]
            assert generated_map.pickup_item(item) is True
            assert item not in room.items

    def test_pickup_nonexistent_item_returns_false(self, generated_map):
        assert generated_map.pickup_item("不存在的物品") is False

    def test_pickup_item_before_generate(self, dmap):
        assert dmap.pickup_item("anything") is False

    def test_clear_threats_sets_cleared(self, generated_map):
        # 找一个有威胁的房间
        for rid, room in generated_map.gmap.rooms.items():
            if room.threats:
                generated_map.move_to(rid)
                assert generated_map.current_room.cleared is False
                generated_map.clear_threats()
                assert generated_map.current_room.cleared is True
                break

    def test_clear_threats_noop_when_no_threats(self, generated_map):
        # 入口通常没有威胁
        generated_map.clear_threats()
        assert generated_map.current_room is not None  # 不会崩溃


# ═══════════════════════════════════════════════════════
# 查询测试
# ═══════════════════════════════════════════════════════

class TestQueries:
    """测试 is_boss_room / is_start_room / get_room。"""

    def test_is_start_room(self, generated_map):
        assert generated_map.is_start_room("0") is True
        assert generated_map.is_start_room("_boss") is False

    def test_is_boss_room(self, generated_map):
        assert generated_map.is_boss_room("_boss") is True
        assert generated_map.is_boss_room("0") is False

    def test_get_room_existing(self, generated_map):
        room = generated_map.get_room("0")
        assert room is not None
        assert room.id == "0"

    def test_get_room_nonexistent(self, generated_map):
        assert generated_map.get_room("nonexistent") is None

    def test_get_room_before_generate(self, dmap):
        assert dmap.get_room("0") is None

    def test_current_room_property(self, generated_map):
        assert generated_map.current_room is not None
        assert generated_map.current_room.id == "0"

    def test_current_room_after_move(self, generated_map):
        start = generated_map.current_room
        if start and start.connections:
            generated_map.move_to(start.connections[0])
            assert generated_map.current_room.id != "0"


# ═══════════════════════════════════════════════════════
# 属性测试
# ═══════════════════════════════════════════════════════

class TestProperties:
    """测试 image_path / relative_path / room_count / visited_count。"""

    def test_image_path_is_absolute(self, generated_map):
        assert generated_map.image_path.is_absolute()

    def test_image_path_ends_with_png(self, generated_map):
        assert generated_map.image_path.suffix == ".png"
        # 包含 session ID
        assert generated_map.image_path.stem.startswith("current_")

    def test_relative_path_format(self, generated_map):
        assert generated_map.relative_path.startswith("/static/maps/current_")
        assert ".png" in generated_map.relative_path
        assert "?v=" in generated_map.relative_path

    def test_room_count_matches(self, generated_map):
        assert generated_map.room_count == 10

    def test_visited_count_initial(self, generated_map):
        assert generated_map.visited_count == 1  # 只有入口

    def test_room_count_before_generate(self, dmap):
        assert dmap.room_count == 0

    def test_visited_count_before_generate(self, dmap):
        assert dmap.visited_count == 0


# ═══════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════

class TestIntegration:
    """端到端场景测试。"""

    def test_full_exploration_flow(self, generated_map):
        """模拟探索所有房间的流程。"""
        visited = set()
        current = generated_map.gmap.start_room_id

        for _ in range(20):  # 最多 20 步
            visited.add(current)
            room = generated_map.get_room(current)
            if not room:
                break
            # 尝试去一个未探索的相邻房间
            moved = False
            for conn_id in room.connections:
                if conn_id not in visited:
                    generated_map.move_to(conn_id)
                    current = conn_id
                    moved = True
                    break
            if not moved:
                break

        assert len(visited) >= 2, "至少应探索 2 个房间"
        assert generated_map.visited_count == len(visited)

    def test_boss_room_has_threats(self, generated_map):
        boss = generated_map.get_room("_boss")
        assert boss is not None
        assert len(boss.threats) > 0, "Boss 房应有威胁"
        assert "Boss" in boss.threats[0][0] or "boss" in boss.threats[0][0].lower()

    def test_boss_room_reachable_from_some_room(self, generated_map):
        """验证 Boss 房至少与一个中间房间相连。"""
        boss_connections = generated_map.get_room("_boss").connections
        assert len(boss_connections) > 0
        # 连接的不是入口
        assert "0" not in boss_connections

    def test_map_connectivity(self, generated_map):
        """验证所有房间都可从入口到达（连通性）。"""
        visited = set()
        stack = [generated_map.gmap.start_room_id]
        while stack:
            rid = stack.pop()
            if rid in visited:
                continue
            visited.add(rid)
            room = generated_map.get_room(rid)
            if room:
                stack.extend(room.connections)
        assert len(visited) == generated_map.room_count, \
            f"有 {generated_map.room_count - len(visited)} 个房间不可达"

    def test_render_after_multiple_moves(self, generated_map):
        """多次移动后渲染不出错。"""
        prev = None
        for _ in range(5):
            room = generated_map.current_room
            if room:
                unvisited = [c for c in room.connections
                             if not generated_map.get_room(c).visited]
                next_room = unvisited[0] if unvisited else room.connections[0]
                generated_map.move_to(next_room)
            generated_map.render()  # 不应抛异常
        # 图片应存在且有效
        img = Image.open(generated_map.image_path)
        assert img.size == (3456, 2688)

    def test_image_paths_dont_leak_between_instances(self, tmp_dir):
        """不同 DungeonMap 实例应有独立的输出文件。"""
        a = DungeonMap(tmp_dir, "map_a").generate(seed=1)
        b = DungeonMap(tmp_dir, "map_b").generate(seed=2)
        a.render()
        b.render()
        assert a.image_path != b.image_path
        assert a.image_path.exists()
        assert b.image_path.exists()
