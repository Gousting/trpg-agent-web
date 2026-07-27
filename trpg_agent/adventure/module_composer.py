"""模块组合引擎 —— 多模块 + 随机事件 → 完整剧情。

设计师写独立的小模块（module.json），每个模块声明自己的入口条件
和出口状态（支持多出口分支）。组合引擎在运行时加载模块池，按兼容性匹配
构建剧情分支图，模块间插入过渡场景和随机遭遇，最终输出标准 Adventure 对象。

现有管道零改动 —— Adventure 的接口保持不变。
"""

from __future__ import annotations

import copy
import json
import logging
import random
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import Adventure, AdventureNpc, Scene, SceneElement
from .variance import (
    ModuleVariance,
    RunSeed,
)

log = logging.getLogger(__name__)

# ── 过渡场景模板 ─────────────────────────────────────

_TRANSITION_TEMPLATES = [
    "穿过{from_loc}，你们来到了{to_loc}。",
    "离开{from_loc}后，你们循着线索赶往{to_loc}。路上{encounter}",
    "从{from_loc}到{to_loc}的路上，天色渐暗。{encounter}",
    "你们决定前往{to_loc}。{encounter}",
    "绕过{from_loc}的废墟，{to_loc}出现在眼前。{encounter}",
]

_TRANSITION_ENCOUNTERS = [
    "一阵冷风卷过，你们隐约听到了低语声。",
    "路边的草丛中有什么东西在蠕动，你们选择绕行。",
    "一只黑猫从阴影中窜出，竖着尾巴消失在拐角。",
    "你们注意到墙上有一道新鲜的抓痕——某种不自然的东西不久前经过这里。",
    "远处传来一声沉闷的撞击，像是重物落地的声音。",
]


# ── 数据类 ──────────────────────────────────────────


@dataclass
class ExitState:
    """模块的一个出口路径——声明模块完成后可以走向何方。

    每个出口有独立的线索产出、情绪变化、位置类型，
    以及可选的游戏内门控条件（requires_element）。
    """

    id: str = ""
    label: str = ""
    provides_clues: list[str] = field(default_factory=list)
    mood: str = ""
    next_location_type: str = ""
    requires_element: str | None = None  # 触发此出口需要解决的元素 ID

    @classmethod
    def from_dict(cls, d: dict) -> "ExitState":
        return cls(
            id=str(d.get("id", "") or ""),
            label=str(d.get("label", "") or ""),
            provides_clues=[str(c) for c in d.get("provides_clues", []) or []],
            mood=str(d.get("mood", "") or ""),
            next_location_type=str(d.get("next_location_type", "") or ""),
            requires_element=d.get("requires", {}).get("resolved_element") if isinstance(d.get("requires"), dict) else None,
        )


@dataclass
class ModuleMeta:
    """模块的元信息——入口/出口条件，不含场景数据。

    出口支持两种格式（向后兼容）：
    - exits (数组): 多出口分支，优先使用
    - exit (单对象): 兼容旧格式，自动转为单元素 exits
    """

    id: str
    title: str
    genre: list[str] = field(default_factory=list)
    difficulty: int = 0
    duration_estimate: str = ""

    # 入口条件
    entry_location_types: list[str] = field(default_factory=list)
    entry_required_clues: list[str] = field(default_factory=list)
    entry_forbidden_clues: list[str] = field(default_factory=list)
    entry_mood: str | None = None

    # 出口状态（多分支）
    exits: list[ExitState] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ModuleMeta":
        entry = d.get("entry", {}) or {}
        exits_raw = d.get("exits")
        exit_single = d.get("exit")

        exits: list[ExitState] = []
        if exits_raw and isinstance(exits_raw, list):
            exits = [ExitState.from_dict(e) for e in exits_raw]
        elif exit_single and isinstance(exit_single, dict):
            # 向后兼容：单 exit 转为单元素 exits
            exits = [ExitState(
                id="default",
                label="",
                provides_clues=[str(c) for c in exit_single.get("provides_clues", []) or []],
                mood=str(exit_single.get("mood", "") or ""),
                next_location_type=str(exit_single.get("next_location_type", "") or ""),
            )]

        return cls(
            id=str(d.get("id", "") or ""),
            title=str(d.get("title", "") or ""),
            genre=[str(g) for g in d.get("genre", []) or []],
            difficulty=int(d.get("difficulty", 0) or 0),
            duration_estimate=str(d.get("duration_estimate", "") or ""),
            entry_location_types=[str(t) for t in entry.get("location_types", []) or []],
            entry_required_clues=[str(c) for c in entry.get("required_clues", []) or []],
            entry_forbidden_clues=[str(c) for c in entry.get("forbidden_clues", []) or []],
            entry_mood=entry.get("mood"),
            exits=exits,
        )


@dataclass
class Module:
    """一个完整的剧情模块——元信息 + 场景 + NPC + 变体配置。"""

    meta: ModuleMeta
    scenes: list[Scene]
    npcs: list[AdventureNpc]
    variance: ModuleVariance


# ── 工具函数 ────────────────────────────────────────


def _prefix_scenes(
    scenes: list[Scene], prefix: str, *,
    translate_leads_to: dict[str, str] | None = None,
) -> list[Scene]:
    """为模块内所有场景 ID 加前缀，同时翻译 leads_to 引用。"""
    result: list[Scene] = []
    for sc in scenes:
        new = copy.deepcopy(sc)
        new_id = f"{prefix}::{sc.id}"
        translate = translate_leads_to or {}
        translate[sc.id] = new_id
        new.id = new_id
        new.leads_to = [
            translate.get(t, t) for t in new.leads_to
        ]
        new.exit_requires = {
            translate.get(k, k): v
            for k, v in new.exit_requires.items()
        }
        result.append(new)
    return result


def _make_transition_scene(
    from_scene_id: str,
    from_title: str,
    to_title: str,
    *,
    rng: random.Random | None = None,
    target_scene_id: str = "",
    label: str = "",
) -> Scene:
    """在模块间生成单条过渡场景。"""
    rng = rng or random.Random()
    tmpl = rng.choice(_TRANSITION_TEMPLATES)
    encounter = rng.choice(_TRANSITION_ENCOUNTERS)

    description = tmpl.format(
        from_loc=from_title,
        to_loc=to_title,
        encounter=encounter,
    )
    if label:
        description = f"【{label}】{description}"

    trans_id = f"__transition__{from_scene_id}__{target_scene_id}"
    return Scene(
        id=trans_id,
        title=f"前往{to_title}",
        part=0,
        description=description,
        leads_to=[target_scene_id] if target_scene_id else [],
        guidance="过渡场景。无需玩家互动，自动推进。",
    )


def _last_scene_id(module: Module) -> str:
    """模块最后一个场景的原始 ID（未经前缀化）。"""
    return module.scenes[-1].id if module.scenes else ""


def _first_scene_id(module: Module) -> str:
    """模块第一个场景的原始 ID。"""
    return module.scenes[0].id if module.scenes else ""


# ── 兼容性检查 ──────────────────────────────────────


@dataclass
class ExitContext:
    """一个出口的上下文——用于匹配下游模块。"""
    location_types: list[str] = field(default_factory=list)
    pool_clues: set[str] = field(default_factory=set)


def _compatible(ctx: ExitContext, meta: ModuleMeta) -> bool:
    """检查模块 meta 是否与上游出口上下文兼容。"""
    if meta.entry_location_types:
        if not set(meta.entry_location_types) & set(ctx.location_types):
            return False
    for clue in meta.entry_required_clues:
        if clue not in ctx.pool_clues:
            return False
    for clue in meta.entry_forbidden_clues:
        if clue in ctx.pool_clues:
            return False
    return True


def _exit_context(module: Module, exit_state: ExitState) -> ExitContext:
    """从模块 + 出口构建上下文。"""
    loc_types = [exit_state.next_location_type] if exit_state.next_location_type else module.meta.entry_location_types
    return ExitContext(location_types=loc_types)


# ── 分支图 ──────────────────────────────────────────


@dataclass
class ModuleNode:
    """分支图中的一个节点——模块实例 + 其出口匹配的下游节点。"""
    module: Module
    # {exit_index: [(next_node, transition_scene_id), ...]}
    edges: dict[int, list[tuple["ModuleNode", str]]] = field(default_factory=dict)


# ── 组合引擎 ────────────────────────────────────────


class ModuleComposer:
    """多模块组合引擎。

    用法:
        composer = ModuleComposer(Path("data/modules"))
        adv, seed = composer.compose(max_depth=3)
        # adv 是标准 Adventure 对象，可直接传入现有管道
    """

    def __init__(self, modules_dir: Path) -> None:
        self._dir = Path(modules_dir)
        self._modules: dict[str, Module] = {}

    # ── 加载 ────────────────────────────────────

    def load_all(self) -> int:
        self._modules.clear()
        if not self._dir.is_dir():
            log.warning("模块目录不存在: %s", self._dir)
            return 0
        for sub in sorted(self._dir.iterdir()):
            if not sub.is_dir():
                continue
            mod = self._load_one(sub)
            if mod is not None:
                self._modules[mod.meta.id] = mod
        log.info("ModuleComposer 加载 %d 个模块", len(self._modules))
        return len(self._modules)

    def _load_one(self, directory: Path) -> Module | None:
        path = directory / "module.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            meta = ModuleMeta.from_dict(data)
            scenes = [Scene.from_dict(s) for s in data.get("scenes", []) or []]
            npcs = [AdventureNpc.from_dict(n) for n in data.get("npcs", []) or []]
            variance = ModuleVariance.from_dict(data.get("variance"))
            return Module(meta=meta, scenes=scenes, npcs=npcs, variance=variance)
        except (OSError, ValueError, KeyError):
            log.exception("模块加载失败: %s", path)
            return None

    @property
    def module_count(self) -> int:
        return len(self._modules)

    def module_ids(self) -> list[str]:
        return sorted(self._modules.keys())

    # ── 组合 ────────────────────────────────────

    def compose(
        self,
        *,
        seed: int | None = None,
        max_depth: int = 3,
        start_module: str | None = None,
        difficulty_range: tuple[int, int] | None = None,
    ) -> tuple[Adventure, RunSeed]:
        """从模块池组合生成完整冒险（支持分支图）。

        Args:
            seed: 随机种子
            max_depth: BFS 最大深度（模块层数）
            start_module: 强制指定起始模块 ID
            difficulty_range: 难度限制

        Returns:
            (Adventure, RunSeed)
        """
        if not self._modules:
            self.load_all()
        if not self._modules:
            raise ValueError("模块池为空，无法组合")

        rng = random.Random(seed or random.randint(0, 2**31 - 1))
        run_seed = RunSeed(rng.randint(0, 2**31 - 1))
        run_seed.rng = rng

        # 1. BFS 构建分支图
        root = self._build_graph(start_module, rng, difficulty_range, max_depth)

        # 2. 收集所有节点（BFS 遍历）
        all_nodes = self._collect_nodes(root)

        # 3. 组装 Adventure
        adv = self._assemble(root, rng)

        # 4. 应用方差
        self._apply_variance(all_nodes, run_seed)

        log.info(
            "组合完成: %s (seed=%d, %d 个模块, %d 个场景)",
            adv.title, run_seed.seed, len(all_nodes), len(adv._scenes),
        )
        return adv, run_seed

    def _build_graph(
        self,
        start_id: str | None,
        rng: random.Random,
        difficulty_range: tuple[int, int] | None,
        max_depth: int,
    ) -> ModuleNode:
        """BFS 构建模块分支图。"""
        # 选起始模块
        start_mod = self._pick_start(start_id, rng, difficulty_range)
        root = ModuleNode(module=start_mod)
        node_map: dict[str, ModuleNode] = {start_mod.meta.id: root}

        # BFS: (node, depth, inherited_clues)
        queue: deque[tuple[ModuleNode, int, set[str]]] = deque()
        queue.append((root, 1, set(start_mod.meta.exits[0].provides_clues) if start_mod.meta.exits else set()))

        while queue:
            node, depth, pool_clues = queue.popleft()
            if depth >= max_depth:
                continue

            mod = node.module
            exits = mod.meta.exits
            if not exits:
                continue

            for exit_idx, exit_state in enumerate(exits):
                ctx = _exit_context(mod, exit_state)
                # 合并池线索
                full_clues = pool_clues | set(exit_state.provides_clues)

                candidates = self._find_compatible(
                    ctx, full_clues, {n.meta.id for n in self._collect_nodes(root)},
                    difficulty_range,
                )
                if not candidates:
                    continue

                for candidate_mod in candidates:
                    if candidate_mod.meta.id in node_map:
                        child = node_map[candidate_mod.meta.id]
                    else:
                        child = ModuleNode(module=candidate_mod)
                        node_map[candidate_mod.meta.id] = child
                        # 子节点继承当前出口的线索池（不混入子模块其他出口的线索）
                        queue.append((child, depth + 1, full_clues))

                    trans_scene_id = f"__trans__{mod.meta.id}_{exit_idx}_to_{candidate_mod.meta.id}"
                    node.edges.setdefault(exit_idx, []).append((child, trans_scene_id))

        return root

    def _collect_nodes(self, root: ModuleNode) -> list[Module]:
        """BFS 收集所有不重复的模块节点。"""
        seen: set[str] = set()
        result: list[Module] = []
        queue: deque[ModuleNode] = deque([root])
        while queue:
            node = queue.popleft()
            if node.module.meta.id not in seen:
                seen.add(node.module.meta.id)
                result.append(node.module)
                for exits in node.edges.values():
                    for child, _ in exits:
                        queue.append(child)
        return result

    def _pick_start(
        self, start_id: str | None, rng: random.Random,
        difficulty_range: tuple[int, int] | None,
    ) -> Module:
        candidates: list[Module] = []
        if start_id and start_id in self._modules:
            candidates = [self._modules[start_id]]
        else:
            for mod in self._modules.values():
                if not mod.meta.entry_required_clues:
                    candidates.append(mod)
        if difficulty_range:
            lo, hi = difficulty_range
            candidates = [m for m in candidates if lo <= m.meta.difficulty <= hi]
        if not candidates:
            candidates = list(self._modules.values())
        return rng.choice(candidates)

    def _find_compatible(
        self,
        ctx: ExitContext,
        pool_clues: set[str],
        used_ids: set[str],
        difficulty_range: tuple[int, int] | None,
    ) -> list[Module]:
        ctx.pool_clues = pool_clues
        candidates = [
            m for m in self._modules.values()
            if m.meta.id not in used_ids
            and _compatible(ctx, m.meta)
        ]
        if difficulty_range:
            lo, hi = difficulty_range
            candidates = [m for m in candidates if lo <= m.meta.difficulty <= hi]
        return candidates

    # ── 组装 ────────────────────────────────────

    def _assemble(self, root: ModuleNode, rng: random.Random) -> Adventure:
        """将分支图组装为标准 Adventure 对象。

        三阶段：翻译表 → 前缀化 → 写入场景图（含分支过渡）。
        """
        all_nodes = self._collect_nodes(root)
        modules_by_id = {n.meta.id: n for n in all_nodes}

        # Phase 1: 翻译表
        translate: dict[str, str] = {}
        for mod in all_nodes:
            for sc in mod.scenes:
                translate[sc.id] = f"{mod.meta.id}::{sc.id}"

        # Phase 2: 前缀化
        all_scenes: list[Scene] = []
        all_npcs: list[AdventureNpc] = []
        for mod in all_nodes:
            prefixed = _prefix_scenes(mod.scenes, mod.meta.id, translate_leads_to=translate)
            all_scenes.extend(prefixed)
            for npc in mod.npcs:
                if npc.name not in {n.name for n in all_npcs}:
                    all_npcs.append(npc)

        # Phase 3: 组装——处理分支边
        final_scenes: list[Scene] = []
        scene_offset: dict[str, int] = {}  # {module_id: start_index in all_scenes}
        idx = 0
        for mod in all_nodes:
            scene_offset[mod.meta.id] = idx
            idx += len(mod.scenes)

        def _process_node(node: ModuleNode, processed: set[str]):
            if node.module.meta.id in processed:
                return
            processed.add(node.module.meta.id)
            offset = scene_offset.get(node.module.meta.id, 0)

            # 添加工序场景（跳过已在 final 中的）
            mod_scenes = all_scenes[offset:offset + len(node.module.scenes)]
            for sc in mod_scenes:
                if sc.id not in {s.id for s in final_scenes}:
                    final_scenes.append(sc)

            # 处理该模块的出口边：修改最后场景的 leads_to 和 exit_requires
            if node.edges and mod_scenes:
                last_scene = mod_scenes[-1]
                # 有分支出口时，清除模块内原始的 leads_to（用分支过渡替代）
                new_leads: list[str] = []
                new_exits: dict[str, str] = {}

                for exit_idx, exit_edges in node.edges.items():
                    exit_state = node.module.meta.exits[exit_idx] if exit_idx < len(node.module.meta.exits) else None
                    for child_node, trans_id in exit_edges:
                        child_first = translate.get(_first_scene_id(child_node.module), "")
                        trans = _make_transition_scene(
                            last_scene.id,
                            node.module.meta.title,
                            child_node.module.meta.title,
                            rng=rng,
                            target_scene_id=child_first,
                            label=exit_state.label if exit_state else "",
                        )
                        trans.id = trans_id
                        if trans.id not in {s.id for s in final_scenes}:
                            final_scenes.append(trans)
                        new_leads.append(trans.id)
                        if exit_state and exit_state.requires_element:
                            new_exits[trans.id] = exit_state.requires_element

                last_scene.leads_to = new_leads
                last_scene.exit_requires = new_exits

                # 递归处理所有子节点
                for exit_edges in node.edges.values():
                    for child_node, _ in exit_edges:
                        _process_node(child_node, processed)

        processed: set[str] = set()
        _process_node(root, processed)

        if not final_scenes:
            raise ValueError("组合结果无场景")

        title = " → ".join(mod.meta.title for mod in all_nodes)
        return Adventure(
            id=f"composed_{root.module.meta.id}_{len(all_nodes)}",
            title=title,
            era="1920s",
            hook=root.module.scenes[0].description[:120] if root.module.scenes else "",
            summary=f"由 {len(all_nodes)} 个模块随机组合生成的冒险（含 {sum(1 for n in [root] if n.edges)} 个分支点）。模块：{'、'.join(mod.meta.title for mod in all_nodes)}。",
            start_scene=translate.get(_first_scene_id(root.module), ""),
            resolution="",
            scenes=final_scenes,
            npcs=all_npcs,
        )

    def _apply_variance(self, all_modules: list[Module], seed: RunSeed) -> None:
        for mod in all_modules:
            if mod.variance.clue_variants:
                seed.place_clues(mod.variance.clue_variants)
            if mod.variance.npc_variants:
                seed.pick_npcs(mod.variance.npc_variants)
            if mod.variance.mood_variants:
                seed.pick_moods(mod.variance.mood_variants)
