"""场景卡模组系统 — 结构化剧情加载与场景追踪。

采用场景卡式模组设计：每个地点/节拍是一张卡片，
代码追踪当前场景，LLM 只看到当前场景内容。剧情不跳章，线索不泄露。

纯数据操作，无 LLM 调用。

模块组合器：
    from trpg_agent.adventure import ModuleComposer
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

# ModuleComposer 从 .module_composer 导入，避免循环引用
# 用法: from trpg_agent.adventure.module_composer import ModuleComposer

log = logging.getLogger(__name__)


# ── 场景元素 ────────────────────────────────────────


@dataclass
class SceneElement:
    """一个可交互元素（线索/秘密/机会），带唯一 ID 用于追踪已解决状态。"""

    id: str
    text: str

    @classmethod
    def from_raw(cls, raw: str | dict) -> "SceneElement":
        if isinstance(raw, dict):
            return cls(
                id=str(raw.get("id", "") or ""),
                text=str(raw.get("text", "") or ""),
            )
        # 纯字符串 → 自动生成 id
        return cls(id="", text=str(raw))


# ── 场景卡 ──────────────────────────────────────────


@dataclass
class Scene:
    """一张场景卡——DM 运行当前节拍所需的全部信息。

    场景之间通过 leads_to 连接，组成故事图。
    opportunities 是调查员可以做的事（线索、互动），
    secrets 是 DM 知道但不应直接说出的隐藏信息。
    """

    id: str
    title: str
    part: int = 0
    description: str = ""
    npcs_here: list[str] = field(default_factory=list)
    opportunities: list[SceneElement] = field(default_factory=list)
    secrets: list[SceneElement] = field(default_factory=list)
    leads_to: list[str] = field(default_factory=list)
    exit_requires: dict[str, str] = field(default_factory=dict)  # {target_id: required_element_id}
    exit_labels: dict[str, str] = field(default_factory=dict)    # {target_id: semantic label}
    guidance: str = ""
    # COC 特有触发器
    san_check: dict | None = None   # {"trigger": "...", "level": "MAJOR"}
    combat: dict | None = None      # {"trigger": "...", "enemy": "...", "hp": N, "armor": N}

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        opportunities = [SceneElement.from_raw(o) for o in d.get("opportunities", []) or []]
        secrets = [SceneElement.from_raw(s) for s in d.get("secrets", []) or []]

        # 解析 exit_requires：{"target": "element_id"} → {target: element_id}
        exit_requires = {}
        raw_exits = d.get("exit_requires", {}) or {}
        if isinstance(raw_exits, dict):
            exit_requires = {str(k): str(v) for k, v in raw_exits.items()}

        exit_labels = {}
        raw_labels = d.get("exit_labels", {}) or {}
        if isinstance(raw_labels, dict):
            exit_labels = {str(k): str(v) for k, v in raw_labels.items()}

        return cls(
            id=str(d.get("id", "") or ""),
            title=str(d.get("title", "") or ""),
            part=int(d.get("part", 0) or 0),
            description=str(d.get("description", "") or ""),
            npcs_here=[str(n) for n in d.get("npcs_here", []) or []],
            opportunities=opportunities,
            secrets=secrets,
            leads_to=[str(t) for t in d.get("leads_to", []) or []],
            exit_requires=exit_requires,
            exit_labels=exit_labels,
            guidance=str(d.get("guidance", "") or ""),
            san_check=d.get("san_check") if isinstance(d.get("san_check"), dict) else None,
            combat=d.get("combat") if isinstance(d.get("combat"), dict) else None,
        )

    def element_ids(self) -> list[str]:
        """所有可标记为已解决的元素 ID（先 opportunities，后 secrets）。"""
        return [e.id for e in self.opportunities if e.id] + [e.id for e in self.secrets if e.id]

    def all_texts(self) -> list[str]:
        """所有元素文本（用于提示块渲染）。"""
        return [e.text for e in self.opportunities] + [e.text for e in self.secrets]


@dataclass
class SceneExit:
    """场景的语义出口视图。"""

    target_id: str
    title: str
    label: str
    required_element: str | None = None
    available: bool = True


# ── 冒险 NPC ────────────────────────────────────────


@dataclass
class AdventureNpc:
    """模组 NPC 的战斗数据。调查员外的角色从这里获取战斗属性。"""

    name: str
    attitude: str = "neutral"
    description: str = ""
    secret: str = ""
    hp: int = 10
    armor: int = 0
    attacks: list[dict] = field(default_factory=list)  # [{"name": "爪击", "skill": 50, "damage": "1d6"}]

    @classmethod
    def from_dict(cls, d: dict) -> "AdventureNpc":
        return cls(
            name=str(d.get("name", "") or ""),
            attitude=str(d.get("attitude", "neutral") or "neutral"),
            description=str(d.get("description", "") or ""),
            secret=str(d.get("secret", "") or ""),
            hp=int(d.get("hp", 10) or 10),
            armor=int(d.get("armor", 0) or 0),
            attacks=d.get("attacks", []) or [],
        )


# ── 冒险总纲 ────────────────────────────────────────


class Adventure:
    """一个完整的模组——场景卡集合 + NPC 数据。

    从 data/adventures/<id>/scenario.json 加载。
    """

    def __init__(
        self,
        *,
        id: str = "",
        title: str = "",
        era: str = "1920s",
        hook: str = "",
        summary: str = "",
        start_scene: str = "",
        resolution: str = "",
        scenes: list[Scene] | None = None,
        npcs: list[AdventureNpc] | None = None,
    ) -> None:
        self.id = id
        self.title = title
        self.era = era
        self.hook = hook
        self.summary = summary
        self.start_scene = start_scene
        self.resolution = resolution
        self._scenes: dict[str, Scene] = {s.id: s for s in (scenes or []) if s.id}
        self._npcs: dict[str, AdventureNpc] = {n.name: n for n in (npcs or []) if n.name}

    # ── 加载 ────────────────────────────────────

    @classmethod
    def load(cls, directory: Path) -> "Adventure | None":
        """从目录加载 scenario.json。失败返回 None（不阻塞游戏）。"""
        path = directory / "scenario.json"
        if not path.is_file():
            log.error("模组文件不存在: %s", path)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            scenes = [Scene.from_dict(s) for s in data.get("scenes", []) or []]
            npcs_data = data.get("npcs", []) or []
            npcs = [AdventureNpc.from_dict(n) for n in npcs_data]
            return cls(
                id=str(data.get("id", "") or directory.name),
                title=str(data.get("title", "") or ""),
                era=str(data.get("era", "1920s") or "1920s"),
                hook=str(data.get("hook", "") or ""),
                summary=str(data.get("summary", "") or ""),
                start_scene=str(data.get("start_scene", "") or (scenes[0].id if scenes else "")),
                resolution=str(data.get("resolution", "") or ""),
                scenes=scenes,
                npcs=npcs,
            )
        except (OSError, ValueError, KeyError):
            log.exception("模组加载失败: %s", path)
            return None

    # ── 查询 ────────────────────────────────────

    def get_scene(self, scene_id: str) -> Scene | None:
        return self._scenes.get((scene_id or "").strip())

    def get_npc(self, name: str) -> AdventureNpc | None:
        key = (name or "").strip()
        return self._npcs.get(key)

    def npc_names(self) -> list[str]:
        return list(self._npcs.keys())

    def scene_exits(
        self,
        scene_id: str,
        *,
        resolved_ids: set[str] | None = None,
        include_locked: bool = False,
    ) -> list[SceneExit]:
        """返回当前场景的语义出口，而不是内部场景 ID。"""
        scene = self.get_scene(scene_id)
        if scene is None:
            return []

        resolved = resolved_ids or set()
        exits: list[SceneExit] = []
        for target_id in scene.leads_to:
            required = scene.exit_requires.get(target_id)
            available = required is None or required in resolved
            if not include_locked and not available:
                continue

            target_scene = self.get_scene(target_id)
            title = target_scene.title if target_scene is not None else target_id
            label = scene.exit_labels.get(target_id, title)
            exits.append(SceneExit(
                target_id=target_id,
                title=title,
                label=label,
                required_element=required,
                available=available,
            ))
        return exits

    # ── 场景切换验证 ─────────────────────────────

    def can_move_to(
        self, current_id: str, target_id: str, *, resolved_ids: set[str] | None = None,
    ) -> bool:
        """验证是否可以切换到目标场景。

        - target 必须在当前场景的 leads_to 列表中
        - 如果有门控（exit_requires），对应的元素必须已解决
        """
        target_id = (target_id or "").strip()
        if not target_id or target_id == current_id:
            return False
        current = self.get_scene(current_id)
        if current is None:
            return False
        if target_id not in current.leads_to:
            return False
        required = current.exit_requires.get(target_id)
        if required and (resolved_ids is None or required not in resolved_ids):
            return False
        return True

    # ── Prompt 块生成 ────────────────────────────

    def adventure_block(
        self, scene_id: str, *, resolved_ids: set[str] | None = None,
    ) -> str:
        """生成注入系统 prompt 的冒险数据块。

        双阶段：始终存在的模组摘要 + 当前场景卡。
        未知 scene_id 时降级为仅摘要。
        """
        resolved = resolved_ids or set()
        lines = [
            "## 冒险模组（仅 DM 参考——切勿逐字朗读）",
            f"《{self.title}》— {self.era}",
            self.summary,
        ]

        scene = self.get_scene(scene_id)
        if scene is not None:
            lines.append("")
            lines.append(f"## 当前场景：{scene.title}（第 {scene.part} 幕）")
            lines.append(scene.description)

            if scene.npcs_here:
                lines.append(f"在场 NPC：{', '.join(scene.npcs_here)}")

            # 机会（可交互元素）
            open_opps = [e for e in scene.opportunities if e.id not in resolved]
            done_opps = [e for e in scene.opportunities if e.id in resolved]
            if open_opps:
                lines.append("")
                lines.append("可探索的线索：")
                for e in open_opps:
                    tag = f"[{e.id}] " if e.id else ""
                    lines.append(f"- {tag}{e.text}")
            if done_opps:
                lines.append("已发现的线索：")
                for e in done_opps:
                    tag = f"[{e.id}] " if e.id else ""
                    lines.append(f"- {tag}{e.text} ✓")

            # 秘密（DM 专属）
            secrets = [e for e in scene.secrets if e.id not in resolved]
            if secrets:
                lines.append("")
                lines.append("DM 秘密（切勿直接说出，最多暗示）：")
                for e in secrets:
                    tag = f"[{e.id}] " if e.id else ""
                    lines.append(f"- {tag}{e.text}")

            # SAN 检定
            if scene.san_check:
                lines.append(f"\nSAN 检定触发：{scene.san_check.get('trigger', '')} "
                             f"— 等级 {scene.san_check.get('level', 'MAJOR')}")

            # 战斗
            if scene.combat:
                lines.append(f"\n战斗触发：{scene.combat.get('trigger', '')} "
                             f"— 敌人 {scene.combat.get('enemy', '')}")

            # 出口
            exits = self.scene_exits(scene.id, resolved_ids=resolved, include_locked=True)
            if exits:
                lines.append("")
                lines.append("可前往：")
                for exit_view in exits:
                    line = f"- {exit_view.label}"
                    if exit_view.required_element and not exit_view.available:
                        line += f"（需先解决 {exit_view.required_element}）"
                    lines.append(line)

            # DM 指引
            if scene.guidance:
                lines.append(f"\nKP 指引：{scene.guidance}")

        return "\n".join(line for line in lines if line is not None).strip()
