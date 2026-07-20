"""Session 管理器 — 连接记忆、状态、prompt 组装到 pipeline。

职责：
1. 加载/创建游戏状态（GameState）
2. 管理对话历史（HistoryStore）
3. 上下文窗口管理（token 估算 + recap 压缩）
4. 组装完整 system prompt
5. 每轮后的持久化
"""

from __future__ import annotations

import logging
from pathlib import Path

from .memory.game_state import GameState, Investigator, Npc, Quest
from .memory.history import HistoryStore
from .llm.persona import load_system_prompt
from .llm.prompt_assembly import assemble_system_prompt
from .llm.client import OllamaClient
from .llm.roll_router import (
    RollRequest,
    classifier_schema,
    classifier_prompt,
    parse_router_response,
    parse_markers,
    clean_markers,
)
from .rules.coc import resolve_coc, describe_result
from .rules.sanity import san_check, SanLoss, SanCheckResult
from .rules.combat import resolve_attack, ActionType
from .rules.luck import spend_luck
from .rules.pushing import push_roll, can_push
from .adventure import Adventure, Scene

log = logging.getLogger(__name__)

# 默认 session 数据目录（项目根目录下的 data/）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = _PROJECT_ROOT / "data"
SESSIONS_DIR = DATA_DIR / "sessions"

# 上下文窗口预算（token 估算：中文约 1.5 字/token）
DEFAULT_MAX_CONTEXT = 4096         # Ollama num_ctx 默认值
HISTORY_MAX_TURNS = 20             # 最多保留轮数
RECAP_TRIGGER_RATIO = 0.75         # 超过 75% 上下文时触发 recap 压缩


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数。中文约 1.5 字/token，英文约 4 字/token。"""
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


# ── 历史列表适配器（db 模式兼容 HistoryStore API）───

class _HistoryListAdapter:
    """将 list[dict] 适配为 HistoryStore 兼容接口。"""

    def __init__(self, entries: list[dict[str, str]]):
        self._entries = entries

    def append(self, role: str, content: str, *, speaker: str | None = None) -> None:
        entry: dict = {"role": role, "content": content}
        if speaker:
            entry["speaker"] = speaker
        self._entries.append(entry)

    def entries(self) -> list[dict[str, str]]:
        return list(self._entries)

    def as_messages(self) -> list[dict[str, str]]:
        return [{"role": e["role"], "content": e["content"]} for e in self._entries]

    def as_text(self, last: int = 0) -> str:
        entries = self._entries[-last:] if last > 0 else self._entries
        lines = []
        for e in entries:
            prefix = "玩家" if e["role"] == "user" else "KP"
            lines.append(f"{prefix}: {e['content']}")
        return "\n".join(lines)

    def clear(self) -> None:
        self._entries.clear()

    def count(self) -> int:
        return len(self._entries)

    def trim(self, keep_last: int) -> None:
        if len(self._entries) > keep_last:
            del self._entries[:-keep_last]

    def last_user_message(self) -> str | None:
        for e in reversed(self._entries):
            if e["role"] == "user":
                return e["content"]
        return None


class Session:
    """管理一局 COC 跑团的完整会话状态。"""

    def __init__(
        self,
        session_id: str = "default",
        *,
        max_context: int = DEFAULT_MAX_CONTEXT,
        data_dir: Path | None = None,
        db = None,  # Database | None — SQLite 后端（可选）
    ):
        self.session_id = session_id
        self.max_context = max_context
        self._dir = data_dir or SESSIONS_DIR
        self._state_path = self._dir / session_id / "state.json"
        self._history_path = self._dir / session_id / "history.jsonl"
        self._db = db  # None → JSON 兼容模式

        # 加载或创建状态
        if self._db is not None:
            self.state = self._db.load_session_state(session_id)
        else:
            self.state = GameState.load(self._state_path)

        if self.state is None:
            self.state = GameState(session_id=session_id)
            log.info("新建 session: %s", session_id)
        else:
            log.info("加载 session: %s (第 %d 轮)", session_id, self.state.turn_count)

        # 加载对话历史
        if self._db is not None:
            self._history_list: list[dict[str, str]] = self._db.load_history(session_id)
        else:
            self._history_store = HistoryStore(self._history_path)

        # 加载 KP 人格
        self._persona = load_system_prompt()

        # 自动加载角色卡
        self.load_characters()

    # ── 历史访问统一接口 ──────────────────────────

    @property
    def history(self):
        """兼容旧代码：db 模式返回列表适配器，JSON 模式返回 HistoryStore。"""
        if self._db is not None:
            return _HistoryListAdapter(self._history_list)
        return self._history_store

    # ── 角色管理 ────────────────────────────────────

    def load_characters(self, characters_path: Path | None = None) -> None:
        """从 JSON 文件加载调查员。

        JSON 格式：{"investigators": [{"name": "陈明", "hp": 12, ...}]}
        或直接的 list：[{"name": "陈明", ...}]

        characters_path 为 None 时先尝试 session 目录，再尝试 default 目录。
        """
        import json

        if characters_path is None:
            session_path = self._dir / self.session_id / "characters.json"
            default_path = self._dir / "default" / "characters.json"
            characters_path = session_path if session_path.is_file() else default_path

        if not characters_path.is_file():
            log.warning("角色文件不存在: %s", characters_path)
            return

        data = json.loads(characters_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            investigators = data
        else:
            investigators = data.get("investigators", [])

        for char_data in investigators:
            inv = Investigator.from_dict(char_data)
            existing = self.state.find_investigator(inv.name)
            if existing:
                existing.hp = inv.hp
                existing.max_hp = inv.max_hp
                existing.san = inv.san
                existing.max_san = inv.max_san
                existing.luck = inv.luck
                existing.skills = inv.skills
            else:
                self.state.investigators.append(inv)

        # 同时加载 NPC
        if isinstance(data, dict) and "npcs" in data:
            for npc_data in data["npcs"]:
                npc = Npc.from_dict(npc_data)
                if not self.state.find_npc(npc.name):
                    self.state.npcs.append(npc)

        # 加载任务
        if isinstance(data, dict) and "quests" in data:
            for q_data in data["quests"]:
                quest = Quest.from_dict(q_data)
                if not any(q.title == quest.title for q in self.state.quests):
                    self.state.quests.append(quest)

        log.info("加载了 %d 个调查员", len(self.state.investigators))

        # DB 模式：同步调查员到数据库
        if self._db is not None:
            for inv in self.state.investigators:
                self._db.save_investigator(inv)
                self._db.add_investigator_to_session(self.session_id, inv.name)
            self._db.save_session_npcs(self.session_id, self.state.npcs)
            self._db.save_session_quests(self.session_id, self.state.quests)

    # ── 上下文管理 ──────────────────────────────────

    def _system_token_budget(self) -> int:
        """计算系统 prompt 的基础 token 消耗（不含历史）。"""
        base = _estimate_tokens(self._persona) + _estimate_tokens(self.state.scene_summary())
        if self.state.recap:
            base += _estimate_tokens(self.state.recap) + 20  # "前情提要" 标题
        return base

    def _history_token_usage(self) -> int:
        """对话历史的 token 估算。"""
        total = 0
        for entry in self.history.entries():
            total += _estimate_tokens(entry.get("content", ""))
        return total

    def _should_compress(self) -> bool:
        """判断是否需要 recap 压缩。"""
        system_tokens = self._system_token_budget()
        history_tokens = self._history_token_usage()
        total = system_tokens + history_tokens
        return total > self.max_context * RECAP_TRIGGER_RATIO

    # ── Recap ─────────────────────────────────────

    def build_recap_context(self) -> str:
        """构建 recap 上下文——供 LLM 生成前情提要。"""
        if self.state.recap:
            return f"前情提要：{self.state.recap}\n\n最近对话：\n{self.history.as_text(last=10)}"
        return f"对话记录：\n{self.history.as_text(last=15)}"

    async def maybe_compress(self, client: OllamaClient) -> bool:
        """当上下文接近上限时，用 LLM 生成前情提要替代旧历史。

        Returns:
            True 如果执行了压缩
        """
        if not self._should_compress():
            return False

        log.info("上下文超限（%d tokens），触发 recap 压缩",
                 self._system_token_budget() + self._history_token_usage())

        # 构建压缩提示
        recap_prompt = (
            "你是桌面角色扮演游戏的主持人，正在写一份简短的前情提要。\n"
            "根据以下对话记录，总结已发生的关键事件：去了哪里、遇到谁、做了什么决定、发现了什么线索。\n"
            "写 3-6 句话，紧凑散文，用中文。不要编造，只总结。以未解决线索结尾。\n\n"
            f"对话记录：\n{self.history.as_text()}"
        )

        try:
            raw = await client.chat(
                recap_prompt,
                [{"role": "user", "content": "请总结以上对话。"}],
            )
            self.set_recap(raw)
            log.info("Recap 压缩完成 (%d 字)", len(self.state.recap))
            return True
        except Exception:
            log.exception("Recap 压缩失败")
            return False

    def set_recap(self, text: str) -> None:
        """设置前情提要。"""
        self.state.recap = text.strip()
        self.history.clear()  # 摘要已包含关键信息，清旧对话腾出窗口
        log.info("Recap 已更新 (%d 字), 对话历史已清理", len(self.state.recap))

    # ── Prompt 组装 ────────────────────────────────

    def build_system_prompt(self, *, adventure: Adventure | None = None) -> str:
        """组装完整的 system prompt。"""
        return assemble_system_prompt(
            persona=self._persona,
            recap=self.state.recap if self.state.recap else None,
            adventure=self._build_adventure_block(adventure),
            state_summary=self.state.scene_summary(),
            npc_memory=self._build_npc_memory_block(),
        )

    def _build_npc_memory_block(self) -> str | None:
        """生成当前场景的 NPC 记忆/态度提示块。

        包含在场 NPC 的态度、描述和关键信息。
        """
        present = [n for n in self.state.npcs if n.location == self.state.location]
        if not present:
            return None

        from .memory.game_state import ATTITUDE_LABELS
        lines = ["## 在场 NPC（角色扮演参考）"]
        for npc in present:
            att = ATTITUDE_LABELS.get(npc.attitude, npc.attitude)
            line = f"- {npc.name}（{att}）"
            if npc.description:
                line += f"：{npc.description}"
            lines.append(line)
        return "\n".join(lines)

    def build_messages(self, player_input: str, *, dice_context: str = "",
                       speaker: str | None = None) -> list[dict[str, str]]:
        """构建发给 Ollama 的消息列表。

        Args:
            player_input: 玩家输入
            dice_context: 检定结果（可选）
            speaker: 说话人/调查员名（多人联机时自动加前缀）
        """
        messages = self.history.as_messages()

        user_msg = player_input
        if speaker:
            user_msg = f"[{speaker}] {player_input}"
        if dice_context:
            user_msg = f"[检定结果] {dice_context}\n\n[调查员行动] {user_msg}"

        messages.append({"role": "user", "content": user_msg})
        return messages

    # ── 检定路由（Phase 3）────────────────────────────

    def _collect_skills(self) -> list[str]:
        """收集所有调查员的技能列表（去重）。"""
        skills: set[str] = set()
        for inv in self.state.investigators:
            skills.update(inv.skills.keys())
        return sorted(skills)

    def _get_skill_value(self, skill: str, character: str | None) -> int | None:
        """查找指定角色（或任一拥有该技能的调查员）的技能值。"""
        candidates = self.state.investigators
        if character:
            inv = self.state.find_investigator(character)
            candidates = [inv] if inv else []
        for inv in candidates:
            if skill in inv.skills:
                return inv.skills[skill]
        return None

    async def classify_and_resolve(
        self, client: OllamaClient, player_input: str,
    ) -> tuple[str, RollRequest | None]:
        """分类玩家行动是否需要检定，如需则执行并返回上下文。

        Returns:
            (dice_context, roll_request) — dice_context 为空字符串表示无需检定。
        """
        import json as _json

        # 先尝试 router（constrained JSON 分类器）
        skills = self._collect_skills()
        if not skills:
            log.warning("角色卡中没有技能，检定路由跳过")
            return "", None

        if skills:
            try:
                schema = classifier_schema(skills, ["常规", "困难", "极难"])
                prompt = classifier_prompt(skills, ["常规", "困难", "极难"])
                raw = await client.chat(
                    prompt,
                    [{"role": "user", "content": player_input}],
                    format=schema,
                    options={"temperature": 0.2},
                )
                data = _json.loads(raw)
                request = parse_router_response(data)
            except _json.JSONDecodeError as e:
                log.info("分类器 JSON 解析失败（模型输出格式错误）: %s", e)
                request = None
            except Exception:
                log.exception("分类器调用异常")
                request = None

        if request is None:
            return "", None

        # 查找技能值
        sv = self._get_skill_value(request.skill, request.character)
        if sv is None:
            log.info("技能 '%s' 不在任何调查员卡中，跳过检定", request.skill)
            return "", None

        # 执行检定
        result = resolve_coc(sv, request.difficulty)
        desc = describe_result(result, request.skill)
        context = request.to_context(desc)
        log.info("检定: %s", desc)
        return context, request

    # ── 回合管理 ────────────────────────────────────

    def record_turn(self, player_input: str, kp_answer: str, *,
                    speaker: str | None = None) -> None:
        """记录一轮对话。空回答自动填入兜底文本。

        KP 回复中的 <!--GS ... --> 块会被解析为游戏状态变更，
        并在存入历史前从回复中移除（玩家不可见）。

        Args:
            player_input: 玩家输入
            kp_answer: KP 回复
            speaker: 说话人/调查员名（多人联机时记录身份）
        """
        if not kp_answer.strip():
            kp_answer = "（KP 沉思片刻，等待着调查员的下一步行动。）"
            log.warning("第 %d 轮 KP 回答为空，使用兜底文本", self.state.turn_count + 1)

        # 解析 GS 标记块，更新 GameState，返回清洗后的回复
        from .memory.gs_parser import parse_and_apply
        kp_answer = parse_and_apply(self.state, kp_answer)

        self.history.append("user", player_input, speaker=speaker)
        self.history.append("assistant", kp_answer)
        self.state.turn_count += 1

        # 数据库模式：同步写入
        if self._db is not None:
            self._db.save_session_state(self.state)
            self._db.append_history(
                self.session_id, self.state.turn_count,
                "user", player_input, speaker,
            )
            self._db.append_history(
                self.session_id, self.state.turn_count,
                "assistant", kp_answer,
            )

        # 限制历史长度
        max_entries = HISTORY_MAX_TURNS * 2
        if self.history.count() > max_entries:
            self.history.trim(keep_last=max_entries)
            if self._db is not None:
                self._db.clear_history(self.session_id)
                for i, entry in enumerate(self.history.entries()):
                    self._db.append_history(
                        self.session_id, i + 1,
                        entry["role"], entry["content"],
                        entry.get("speaker"),
                    )
            log.debug("对话历史已裁剪至 %d 条", max_entries)

    def persist(self) -> None:
        """持久化状态和历史。"""
        if self._db is not None:
            self._db.save_session_state(self.state)
            self._db.save_session_npcs(self.session_id, self.state.npcs)
            self._db.save_session_quests(self.session_id, self.state.quests)
        else:
            self.state.save(self._state_path)
        log.debug("状态已保存 (第 %d 轮)", self.state.turn_count)

    # ── 模组系统 ─────────────────────────────────────

    def load_adventure(self, adventure_id: str) -> Adventure | None:
        """加载模组。填充 GameState 的场景、NPC、任务。

        Returns:
            Adventure 对象，加载失败返回 None
        """
        adv_dir = DATA_DIR / "adventures" / adventure_id
        adv = Adventure.load(adv_dir)
        if adv is None:
            return None

        self.state.adventure_id = adventure_id
        self.state.location = adv.title
        self.state.scene_id = adv.start_scene
        self.state.resolved_elements.clear()

        # 注册模组 NPC
        for name in adv.npc_names():
            if not self.state.find_npc(name):
                npc_data = adv.get_npc(name)
                if npc_data:
                    self.state.npcs.append(Npc(
                        name=npc_data.name,
                        description=npc_data.description,
                        location=adv.start_scene,
                    ))

        log.info("模组已加载: %s (起始场景: %s, %d 个场景, %d 个 NPC)",
                 adv.title, adv.start_scene, len(adv._scenes), len(adv._npcs))
        return adv

    def move_to_scene(self, scene_id: str, adventure: Adventure) -> Scene | None:
        """切换到目标场景（仅限当前场景的 leads_to 列表）。

        Returns:
            目标 Scene，切换失败返回 None
        """
        if not adventure.can_move_to(
            self.state.scene_id, scene_id,
            resolved_ids=self.state.resolved_elements,
        ):
            log.info("场景切换被拒绝: %s → %s", self.state.scene_id, scene_id)
            return None

        scene = adventure.get_scene(scene_id)
        if scene is None:
            return None

        self.state.scene_id = scene_id

        # 更新场景 NPC 位置
        for npc_name in scene.npcs_here:
            npc = self.state.find_npc(npc_name)
            if npc:
                npc.location = scene_id

        log.info("场景切换: → %s (%s)", scene_id, scene.title)
        return scene

    def resolve_element(self, element_id: str) -> bool:
        """标记元素为已解决。"""
        if not element_id:
            return False
        self.state.resolved_elements.add(element_id)
        return True

    def _build_adventure_block(self, adventure: Adventure | None = None) -> str | None:
        """生成模组数据 prompt 块。"""
        if adventure is None or not self.state.scene_id:
            return None
        return adventure.adventure_block(
            self.state.scene_id,
            resolved_ids=self.state.resolved_elements,
        )

    # ── Phase 4: SAN / 战斗 / 幸运 / 孤注一掷 ──────────────

    def perform_san_check(
        self, investigator_name: str, loss_level: str,
    ) -> SanCheckResult | None:
        """对指定调查员执行 SAN 检定。

        Args:
            investigator_name: 调查员名字
            loss_level: SAN 损失等级名（如 "MAJOR", "SEVERE"）

        Returns:
            SanCheckResult，找不到调查员返回 None
        """
        inv = self.state.find_investigator(investigator_name)
        if inv is None:
            log.warning("SAN 检定：找不到调查员 '%s'", investigator_name)
            return None

        try:
            level = SanLoss[loss_level.upper()]
        except KeyError:
            log.warning("未知 SAN 损失等级: %s", loss_level)
            return None

        result = san_check(inv.san, level)
        inv.san = result.san_after

        if result.went_insane:
            condition = f"疯狂: {result.symptom}"
            if condition not in inv.conditions:
                inv.conditions.append(condition)

        log.info("SAN 检定 %s: %s", investigator_name, result.description)
        return result

    def perform_attack(
        self, attacker_name: str, defender_name: str,
        attack_type: str, attack_skill: int,
        *, defense_type: str | None = None, defense_skill: int | None = None,
        damage_dice: str = "1d3", damage_bonus: int = 0,
    ) -> AttackResult | None:
        """执行一次攻击结算。

        Args:
            attacker_name: 攻击方名字
            defender_name: 防守方名字
            attack_type: 攻击类型 "FIGHTING" | "FIREARMS"
            attack_skill: 攻击技能值
            defense_type: 防守类型 "DODGE" | "FIGHTING_BACK" | None
            defense_skill: 防守技能值
            damage_dice: 伤害骰
            damage_bonus: 伤害加值

        Returns:
            AttackResult，如造成伤害则自动应用到防守方 HP
        """
        atk_type = ActionType[attack_type.upper()]
        def_type = ActionType[defense_type.upper()] if defense_type else None

        result = resolve_attack(
            attacker_name, defender_name, atk_type, attack_skill,
            defense_type=def_type, defense_skill=defense_skill or 0,
            damage_dice=damage_dice, damage_bonus=damage_bonus,
        )

        # 自动应用伤害
        if result.total_damage > 0 and result.attack_success:
            defender = self.state.find_investigator(defender_name)
            if defender is None:
                defender_npc = self.state.find_npc(defender_name)
                if defender_npc is None:
                    log.warning("战斗：找不到防守方 '%s'", defender_name)
                # NPC 伤害暂不追踪（简化）
            else:
                defender.take_damage(result.total_damage)
                log.info("战斗：%s 受到 %d 点伤害（HP %d/%d）",
                         defender_name, result.total_damage, defender.hp, defender.max_hp)

        return result

    def spend_luck_for_roll(
        self, investigator_name: str,
        roll_value: int, target_value: int,
    ) -> str | None:
        """消耗调查员的幸运值来调整检定。

        Returns:
            KP 叙述文本，找不到调查员返回 None
        """
        inv = self.state.find_investigator(investigator_name)
        if inv is None:
            return None

        result = spend_luck(inv.luck, roll_value, target_value)
        inv.luck = result.luck_after
        return result.description

    def try_push_roll(
        self, skill_value: int, difficulty: str = "常规",
        *, previous_roll: int = 0,
    ) -> dict | None:
        """尝试孤注一掷。

        Returns:
            {success, roll, level, description} 或 None（当前无前次检定结果则跳过）
        """
        if previous_roll <= 0:
            return None

        from .rules.coc import resolve_coc, CocTestResult, SuccessLevel
        # 构造上次结果
        prev = CocTestResult(
            roll=previous_roll, skill_value=skill_value, difficulty=difficulty,
            target=skill_value, success=False, level=SuccessLevel.FAILURE,
            is_critical=False, is_fumble=False,
        )
        if not can_push(prev):
            return {"success": False, "roll": previous_roll, "level": "无法孤注一掷",
                    "description": "大失败或已成功，不能孤注一掷。"}

        pushed = push_roll(skill_value, difficulty, previous_result=prev)
        return {
            "success": pushed.pushed.success,
            "roll": pushed.pushed.roll,
            "level": pushed.pushed.level.value,
            "description": pushed.description,
        }

    # ── 存档系统 ─────────────────────────────────────

    def save_game(self, name: str) -> Path:
        """命名保存——将当前 session 完整复制到存档槽。

        Returns:
            存档目录路径
        """
        import shutil
        save_dir = DATA_DIR / "saves" / self.session_id / name
        save_dir.mkdir(parents=True, exist_ok=True)

        if self._db is not None:
            # db 模式：创建快照
            self._db.save_session_state(self.state)
            self._db.save_session_npcs(self.session_id, self.state.npcs)
            self._db.save_session_quests(self.session_id, self.state.quests)
            self._db.save_snapshot(self.session_id, name)
        else:
            # JSON 模式
            self.state.save(save_dir / "state.json")
            if self._history_path.is_file():
                shutil.copy2(self._history_path, save_dir / "history.jsonl")

        log.info("存档: %s/%s (第 %d 轮)", self.session_id, name, self.state.turn_count)
        return save_dir

    @staticmethod
    def list_saves(session_id: str = "default", *, db=None) -> list[str]:
        """列出某个 session 的所有存档名。db 模式优先查数据库快照。"""
        if db is not None:
            return db.list_saves(session_id)
        save_root = DATA_DIR / "saves" / session_id
        if not save_root.is_dir():
            return []
        return sorted(
            d.name for d in save_root.iterdir()
            if d.is_dir() and (d / "state.json").is_file()
        )

    @classmethod
    def load_game(cls, session_id: str, save_name: str,
                  *, data_dir: Path | None = None, db=None) -> "Session | None":
        """从命名存档恢复 session。

        Returns:
            Session 对象，存档不存在返回 None
        """
        if db is not None:
            # 数据库模式：从快照恢复
            result = db.load_snapshot(session_id, save_name)
            if result is None:
                return None
            state, history_list = result
            session = cls(session_id, data_dir=data_dir or SESSIONS_DIR, db=db)
            session.state = state
            session._history_list = history_list
            log.info("读档: %s/%s (第 %d 轮)", session_id, save_name, state.turn_count)
            return session

        # JSON 模式
        save_dir = DATA_DIR / "saves" / session_id / save_name
        state_path = save_dir / "state.json"
        if not state_path.is_file():
            log.warning("存档不存在: %s/%s", session_id, save_name)
            return None

        session = cls(session_id, data_dir=data_dir or SESSIONS_DIR)
        loaded = GameState.load(state_path)
        if loaded is None:
            return None
        session.state = loaded

        history_path = save_dir / "history.jsonl"
        if history_path.is_file():
            import shutil
            session._history_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(history_path, session._history_path)
            session._history_store = HistoryStore(session._history_path)

        log.info("读档: %s/%s (第 %d 轮, %d 条历史)",
                 session_id, save_name, session.state.turn_count, session.history.count())
        return session

    @staticmethod
    def delete_save(session_id: str, name: str, *, db=None) -> bool:
        """删除命名存档。"""
        if db is not None:
            return db.delete_snapshot(session_id, name)
        import shutil
        save_dir = DATA_DIR / "saves" / session_id / name
        if save_dir.is_dir():
            shutil.rmtree(save_dir)
            log.info("删除存档: %s/%s", session_id, name)
            return True
        return False

    # ── 便捷方法 ────────────────────────────────────

    def summary(self) -> str:
        """Session 概览。"""
        inv_names = [i.name for i in self.state.investigators]
        npc_names = [n.name for n in self.state.npcs]
        return (
            f"Session {self.session_id} | 第 {self.state.turn_count} 轮\n"
            f"地点: {self.state.location or '未设定'}\n"
            f"调查员: {', '.join(inv_names) or '无'}\n"
            f"NPC: {', '.join(npc_names) or '无'}\n"
            f"历史: {self.history.count()} 条, ~{self._history_token_usage()} tokens\n"
            f"上下文: system ~{self._system_token_budget()} + history ~{self._history_token_usage()}"
            f" = {self._system_token_budget() + self._history_token_usage()} / {self.max_context}"
        )
