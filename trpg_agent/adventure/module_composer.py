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

# ── 模块图片路径 ─────────────────────────────────────

_MODULES_IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "modules"
_MODULES_IMAGE_PREFIX = "/images/scenes/modules/"


def _resolve_module_image(module_id: str, scene_original_id: str) -> str:
    """检查模块场景图片是否存在，返回相对 URL 或空字符串。"""
    candidate = _MODULES_IMAGES_DIR / module_id / f"{scene_original_id}.png"
    if candidate.is_file():
        return f"{_MODULES_IMAGE_PREFIX}{module_id}/{scene_original_id}.png"
    # 降级：尝试 jpg
    candidate_jpg = _MODULES_IMAGES_DIR / module_id / f"{scene_original_id}.jpg"
    if candidate_jpg.is_file():
        return f"{_MODULES_IMAGE_PREFIX}{module_id}/{scene_original_id}.jpg"
    return ""


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

    # 结局标记——true 表示这是叙事终点（胜利/逃脱等），组合引擎不会再往下续接分支
    is_ending: bool = False

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
            is_ending=bool(d.get("is_ending", False)),
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
        new.exit_labels = {
            translate.get(k, k): v
            for k, v in new.exit_labels.items()
        }
        result.append(new)
    return result


def _apply_scene_variance(scene: Scene, variance: ModuleVariance) -> None:
    """将已选中的场景方差落到场景副本上。"""
    mood = variance.mood_variants.get(scene.id)
    if not mood or not mood.chosen_details:
        return

    details = " ".join(detail.strip() for detail in mood.chosen_details if detail.strip())
    if not details or details in scene.description:
        return

    joiner = "" if not scene.description else " "
    scene.description = f"{scene.description}{joiner}{details}".strip()


def _apply_npc_variance(npc: AdventureNpc, variance: ModuleVariance) -> None:
    """将已选中的 NPC 变体落到 NPC 副本上。"""
    for variant in variance.npc_variants:
        if variant.npc_name != npc.name or not variant.variants:
            continue
        if variant.chosen >= len(variant.variants):
            continue

        selected = variant.variants[variant.chosen]
        description = str(selected.get("description", "") or "").strip()
        attitude = str(selected.get("attitude", "") or "").strip()
        secret = str(selected.get("secret", "") or "").strip()
        if description:
            npc.description = description
        if attitude:
            npc.attitude = attitude
        if secret:
            npc.secret = secret
        return


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
    # {exit_index: [ModuleEdge, ...]}
    edges: dict[int, list["ModuleEdge"]] = field(default_factory=dict)


@dataclass
class ModuleEdge:
    """模块间的一条过渡边。"""

    next_node: ModuleNode
    transition_scene_id: str
    target_hint: str = ""
    label: str = ""
    required_element: str | None = None


@dataclass
class CompiledAdventure:
    """模块池编译出的运行时冒险包。"""

    adventure: Adventure
    run_seed: RunSeed
    module_ids: list[str]
    branch_points: int
    source_id: str
    compose_seed: int
    max_depth: int
    start_module: str | None
    difficulty_range: tuple[int, int] | None


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
        bundle = self.compile(
            seed=seed,
            max_depth=max_depth,
            start_module=start_module,
            difficulty_range=difficulty_range,
        )
        return bundle.adventure, bundle.run_seed

    def compile(
        self,
        *,
        seed: int | None = None,
        max_depth: int = 3,
        start_module: str | None = None,
        difficulty_range: tuple[int, int] | None = None,
    ) -> CompiledAdventure:
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

        compose_seed = seed if seed is not None else random.randint(0, 2**31 - 1)
        rng = random.Random(compose_seed)
        run_seed = RunSeed(rng.randint(0, 2**31 - 1))
        run_seed.rng = rng

        # 1. BFS 构建分支图
        root = self._build_graph(start_module, rng, difficulty_range, max_depth)

        # 2. 收集所有节点（BFS 遍历）
        all_nodes = self._collect_nodes(root)

        # 3. 先固定本次开局的随机变体，再组装 Adventure
        self._apply_variance(all_nodes, run_seed)

        # 4. 组装 Adventure
        adv = self._assemble(root, rng)
        branch_points = sum(1 for node in self._walk_nodes(root) if node.edges)
        module_ids = [mod.meta.id for mod in all_nodes]
        bundle = CompiledAdventure(
            adventure=adv,
            run_seed=run_seed,
            module_ids=module_ids,
            branch_points=branch_points,
            source_id=adv.id,
            compose_seed=compose_seed,
            max_depth=max_depth,
            start_module=start_module,
            difficulty_range=difficulty_range,
        )

        log.info(
            "组合完成: %s (seed=%d, %d 个模块, %d 个场景)",
            adv.title, run_seed.seed, len(all_nodes), len(adv._scenes),
        )
        return bundle

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
        queue.append((root, 1, set()))

        while queue:
            node, depth, pool_clues = queue.popleft()
            if depth >= max_depth:
                continue

            mod = node.module
            if mod.meta.is_ending:
                # 结局模块（胜利/逃脱等）——不再继续往下分支，避免叙事割裂
                continue

            exits = mod.meta.exits
            authored_targets = self._authored_external_targets(mod)
            if not exits and not authored_targets:
                # 既没有出口状态，也没有场景里手写的显式跳转——真正的终点模块
                continue

            used_ids = {n.meta.id for n in self._collect_nodes(root)}
            exit_contexts: list[tuple[int, ExitState, set[str], list[Module]]] = []
            for exit_idx, exit_state in enumerate(exits):
                ctx = _exit_context(mod, exit_state)
                full_clues = pool_clues | set(exit_state.provides_clues)
                candidates = self._find_compatible(
                    ctx, full_clues, used_ids, difficulty_range,
                )
                exit_contexts.append((exit_idx, exit_state, full_clues, candidates))

            # 本节点已经连出去的模块 id——防止手写目标和随机候选重复连同一个模块
            node_targets: set[str] = set()
            claimed_exits: set[int] = set()

            if authored_targets:
                for target_id in authored_targets:
                    child_mod = self._resolve_explicit_target(target_id, difficulty_range)
                    if child_mod is None or child_mod.meta.id in used_ids or child_mod.meta.id in node_targets:
                        continue

                    if exit_contexts:
                        exit_idx, exit_state, full_clues = self._pick_exit_for_target(
                            mod,
                            target_id,
                            exit_contexts,
                            claimed_exits,
                        )
                    else:
                        # 模块没有声明 meta.exits，完全依赖场景手写的 exit_labels/exit_requires
                        exit_idx, exit_state, full_clues = 0, ExitState(id="default"), pool_clues
                    claimed_exits.add(exit_idx)
                    node_targets.add(child_mod.meta.id)

                    if child_mod.meta.id in node_map:
                        child = node_map[child_mod.meta.id]
                    else:
                        child = ModuleNode(module=child_mod)
                        node_map[child_mod.meta.id] = child
                        queue.append((child, depth + 1, full_clues))

                    trans_scene_id = f"__trans__{mod.meta.id}_{exit_idx}_to_{child_mod.meta.id}"
                    node.edges.setdefault(exit_idx, []).append(ModuleEdge(
                        next_node=child,
                        transition_scene_id=trans_scene_id,
                        target_hint=target_id,
                        label=mod.scenes[-1].exit_labels.get(target_id, "") if mod.scenes else "",
                        required_element=(
                            mod.scenes[-1].exit_requires.get(target_id)
                            if mod.scenes else None
                        ) or exit_state.requires_element,
                    ))

            # 随机兼容匹配作为补充——即使出口已经有手写目标，仍可叠加其它兼容候选，
            # 以恢复"多出口分支图组合引擎"原本的随机多样性（此前手写目标会完全屏蔽随机匹配）。
            for exit_idx, exit_state, full_clues, candidates in exit_contexts:
                for candidate_mod in candidates:
                    if candidate_mod.meta.id in node_targets:
                        continue
                    node_targets.add(candidate_mod.meta.id)

                    if candidate_mod.meta.id in node_map:
                        child = node_map[candidate_mod.meta.id]
                    else:
                        child = ModuleNode(module=candidate_mod)
                        node_map[candidate_mod.meta.id] = child
                        # 子节点继承当前出口的线索池（不混入子模块其他出口的线索）
                        queue.append((child, depth + 1, full_clues))

                    trans_scene_id = f"__trans__{mod.meta.id}_{exit_idx}_to_{candidate_mod.meta.id}"
                    node.edges.setdefault(exit_idx, []).append(ModuleEdge(
                        next_node=child,
                        transition_scene_id=trans_scene_id,
                    ))

        return root

    def _authored_external_targets(self, module: Module) -> list[str]:
        """返回模块最后场景里作者显式声明的外部目标场景。"""
        if not module.scenes:
            return []

        last_scene = module.scenes[-1]
        ordered: list[str] = []
        seen: set[str] = set()
        for mapping in (last_scene.exit_labels, last_scene.exit_requires):
            for target_id in mapping:
                module_id = self._target_module_id(target_id)
                if not module_id or module_id == module.meta.id or module_id not in self._modules:
                    continue
                if target_id in seen:
                    continue
                seen.add(target_id)
                ordered.append(target_id)
        return ordered

    def _target_module_id(self, target_id: str) -> str | None:
        target = (target_id or "").strip()
        if not target:
            return None
        if "::" in target:
            return target.split("::", 1)[0]
        if target in self._modules:
            return target
        return None

    def _resolve_explicit_target(
        self,
        target_id: str,
        difficulty_range: tuple[int, int] | None,
    ) -> Module | None:
        module_id = self._target_module_id(target_id)
        if not module_id:
            return None
        module = self._modules.get(module_id)
        if module is None:
            return None
        if difficulty_range is None:
            return module
        lo, hi = difficulty_range
        if lo <= module.meta.difficulty <= hi:
            return module
        return None

    def _pick_exit_for_target(
        self,
        module: Module,
        target_id: str,
        exit_contexts: list[tuple[int, ExitState, set[str], list[Module]]],
        claimed_exits: set[int],
    ) -> tuple[int, ExitState, set[str]]:
        """为显式目标挑选最合适的出口状态。"""
        target_module_id = self._target_module_id(target_id)
        if target_module_id is None:
            return exit_contexts[0][0], exit_contexts[0][1], exit_contexts[0][2]

        exact_matches = [
            (exit_idx, exit_state, full_clues)
            for exit_idx, exit_state, full_clues, candidates in exit_contexts
            if any(candidate.meta.id == target_module_id for candidate in candidates)
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

        clue_only_matches = [
            (exit_idx, exit_state, full_clues)
            for exit_idx, exit_state, full_clues, _candidates in exit_contexts
            if self._matches_required_clues_only(full_clues, target_module_id)
        ]
        if len(clue_only_matches) == 1:
            return clue_only_matches[0]

        remaining = [
            (exit_idx, exit_state, full_clues)
            for exit_idx, exit_state, full_clues, _candidates in exit_contexts
            if exit_idx not in claimed_exits
        ]
        if len(remaining) == 1:
            return remaining[0]

        target_required = module.scenes[-1].exit_requires.get(target_id) if module.scenes else None
        if target_required:
            for exit_idx, exit_state, full_clues, _candidates in exit_contexts:
                if exit_state.requires_element == target_required:
                    return exit_idx, exit_state, full_clues

        return exit_contexts[0][0], exit_contexts[0][1], exit_contexts[0][2]

    def _matches_required_clues_only(self, pool_clues: set[str], target_module_id: str) -> bool:
        module = self._modules.get(target_module_id)
        if module is None:
            return False
        for clue in module.meta.entry_required_clues:
            if clue not in pool_clues:
                return False
        for clue in module.meta.entry_forbidden_clues:
            if clue in pool_clues:
                return False
        return True

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
                    for edge in exits:
                        queue.append(edge.next_node)
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
        # Phase 1: 翻译表
        translate: dict[str, str] = {}
        for mod in all_nodes:
            for sc in mod.scenes:
                translate[sc.id] = f"{mod.meta.id}::{sc.id}"

        # Phase 2: 前缀化 + 方差落盘 + 图片回链
        all_scenes: list[Scene] = []
        all_npcs: list[AdventureNpc] = []
        for mod in all_nodes:
            varied_scenes = [copy.deepcopy(scene) for scene in mod.scenes]
            for scene in varied_scenes:
                _apply_scene_variance(scene, mod.variance)
            prefixed = _prefix_scenes(varied_scenes, mod.meta.id, translate_leads_to=translate)
            # 图片回链：检查 modules/{module_id}/{scene_original_id}.png
            for scene, original in zip(prefixed, mod.scenes):
                if not scene.image:
                    resolved = _resolve_module_image(mod.meta.id, original.id)
                    if resolved:
                        scene.image = resolved
            all_scenes.extend(prefixed)
            for npc in mod.npcs:
                if npc.name not in {n.name for n in all_npcs}:
                    npc_copy = copy.deepcopy(npc)
                    _apply_npc_variance(npc_copy, mod.variance)
                    all_npcs.append(npc_copy)

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
                # 保留模块作者手写的 exit_labels + exit_requires（翻译 key 到过渡场景 ID）
                authored_labels: dict[str, str] = dict(last_scene.exit_labels)
                authored_exits: dict[str, str] = dict(last_scene.exit_requires)
                new_leads: list[str] = []
                new_exits: dict[str, str] = {}
                new_labels: dict[str, str] = {}

                for exit_idx, exit_edges in node.edges.items():
                    exit_state = node.module.meta.exits[exit_idx] if exit_idx < len(node.module.meta.exits) else None
                    for edge in exit_edges:
                        child_node = edge.next_node
                        trans_id = edge.transition_scene_id
                        child_first = translate.get(_first_scene_id(child_node.module), "")
                        child_module_id = child_node.module.meta.id
                        trans = _make_transition_scene(
                            last_scene.id,
                            node.module.meta.title,
                            child_node.module.meta.title,
                            rng=rng,
                            target_scene_id=child_first,
                            label="",
                        )
                        trans.id = trans_id
                        if trans.id not in {s.id for s in final_scenes}:
                            final_scenes.append(trans)
                        new_leads.append(trans.id)

                        # 标签：优先用手写的 exit_labels，回退到出口状态标签
                        label = edge.label or authored_labels.get(edge.target_hint)
                        if not label:
                            label = authored_labels.get(child_module_id) or authored_labels.get(child_first)
                        if not label and exit_state and exit_state.label:
                            label = exit_state.label
                        if label:
                            new_labels[trans.id] = label
                        # 门控：优先手写的，回退到出口状态
                        req = edge.required_element or authored_exits.get(edge.target_hint)
                        if not req:
                            req = authored_exits.get(child_module_id) or authored_exits.get(child_first)
                        if not req and exit_state and exit_state.requires_element:
                            req = exit_state.requires_element
                        if req:
                            new_exits[trans.id] = req

                last_scene.leads_to = new_leads
                last_scene.exit_requires = new_exits
                last_scene.exit_labels = new_labels

                # 递归处理所有子节点
                for exit_edges in node.edges.values():
                    for edge in exit_edges:
                        _process_node(edge.next_node, processed)

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
            summary=f"由 {len(all_nodes)} 个模块随机组合生成的冒险（含 {sum(1 for node in self._walk_nodes(root) if node.edges)} 个分支点）。模块：{'、'.join(mod.meta.title for mod in all_nodes)}。",
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

    def _walk_nodes(self, root: ModuleNode) -> list[ModuleNode]:
        """BFS 收集所有不重复的模块节点对象。"""
        seen: set[str] = set()
        result: list[ModuleNode] = []
        queue: deque[ModuleNode] = deque([root])
        while queue:
            node = queue.popleft()
            if node.module.meta.id in seen:
                continue
            seen.add(node.module.meta.id)
            result.append(node)
            for exits in node.edges.values():
                for edge in exits:
                    queue.append(edge.next_node)
        return result
