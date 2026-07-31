"""Narrative constraints — guardrails injected into the prompt.

Phase 1+ Htem: constraints are structured as hard/soft/triggers layers.
  - hard: 不可违反的规则 (inviolable rules)
  - soft: 软引导，建议遵守 (advisory guidance)
  - triggers: 条件触发行为 (conditional triggers)

Constraints can optionally be phrased as in-world "laws" rather than
meta-instructions, which improves LLM compliance.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ConstraintSet:
    """A structured collection of constraints for a specific turn context.

    Replaces the old flat category-based structure (world/character/location/ability).
    """
    hard: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    triggers: list[dict] = field(default_factory=list)


class NarrativeConstraints:
    """Manages narrative guardrails.

    Constraints are injected into the prompt via PromptBuilder.
    They follow the Htem [场景约束] block format:
      hard → 不可违反
      soft → 建议遵守
      triggers → 条件触发

    Constraints are loaded from the active worldview pack's
    constraints.json; the module-level hardcoded lists serve as the
    xianxia default / fallback.
    """

    # Default global rules (xianxia worldview). A worldview pack may
    # override these via constraints.json. Kept in code as the fallback
    # so the engine never depends on pack files being present.
    _global_hard: list[str] = [
        "此方天地灵气虽充沛，但修士的能力严格受其修为境界限制。"
        "筑基期修士不可能击败元婴期修士，凡人不可能施展法术。",
        "NPC的性格和行为必须与其设定一致。冷漠的人不会突然热情，"
        "高傲的人不会低声下气。",
        "NPC只能在自身当前位置出现。正在闭关或远在他方的NPC不能突然现身。",
        "这是一个修仙世界，力量体系为炼气→筑基→金丹→元婴→化神→"
        "炼虚→合体→大乘→渡劫。世界中没有魔法、科技或异世界元素。"
        "一切以灵气和修炼为基础。",
        "凡人占人口大多数，修士是少数精英。凡人对修士既敬畏又向往。",
        "保持角色（包括玩家和NPC）的所有状态信息（修为、身份、位置）"
        "与数据库记录完全一致，不得随意修改或编造。前一回合的修为、"
        "身份、位置等信息必须继承到当前回合。",
        "禁止使用'洗得发白'这个短语来形容衣物——属于低质量模板化描写，"
        "既缺乏视觉特异性，也不符合修仙世界修士衣物的实际情况。"
        "如需描写旧衣物，请用更具体的手法（褪色的纹路、磨破的袖口、"
        "浆洗发硬的布料、颜色不均的补丁等）。",
    ]
    _global_soft: list[str] = [
        "场景描述应体现当前地点的独特氛围，避免千篇一律的模板化描写。",
        "对话应体现角色的性格特征和修为境界，高阶修士言谈间自带威压。",
        "适度引入环境细节（天气、声音、气味）增强代入感。",
    ]
    _global_triggers: list[dict] = []

    def _pack_hard(self, worldview: str) -> list[str]:
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        hard = (wv.constraints or {}).get("hard")
        return list(hard) if isinstance(hard, list) and hard else list(self._global_hard)

    def _pack_soft(self, worldview: str) -> list[str]:
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        soft = (wv.constraints or {}).get("soft")
        return list(soft) if isinstance(soft, list) and soft else list(self._global_soft)

    def _pack_triggers(self, worldview: str) -> list[dict]:
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        triggers = (wv.constraints or {}).get("triggers")
        return list(triggers) if isinstance(triggers, list) and triggers else list(self._global_triggers)

    def _pack_context_template(self, worldview: str, key: str, fallback: str) -> str:
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        ct = (wv.constraints or {}).get("context_templates", {}) or {}
        return ct.get(key) or fallback

    def get_context_constraints(
        self,
        player_cultivation: str,
        player_location: str,
        active_npc_names: list[str],
        intent: str = "",
        worldview: str | None = None,
    ) -> ConstraintSet:
        """Build a context-specific constraint set for the current turn.

        Merges the worldview pack's global rules with context-specific
        constraints derived from player state and active NPC data.
        """
        ctx = ConstraintSet(
            hard=list(self._pack_hard(worldview)),
            soft=list(self._pack_soft(worldview)),
            triggers=list(self._pack_triggers(worldview)),
        )

        # NSFW intent: inject sex scene generation directive
        if intent == "nsfw":
            nsfw_rule = self._pack_context_template(
                worldview, "nsfw_intent",
                "玩家意图进行性爱互动。根据场景性质选择Type 1（刺激插曲）"
                "或Type 2（情节性性爱）的描写模式，详见【性爱场景核心规范】。"
                "严格遵循描写规范中的所有规则。无论哪种模式，NPC不能以反问/"
                "拖延/兜圈子的方式阻滞叙事，每轮都必须有推进感。"
            )
            ctx.hard.append(nsfw_rule)

        # Context-specific: player ability cap
        ability_template = self._pack_context_template(
            worldview, "ability_cap",
            "玩家当前修为为{cultivation}，不得表现出超出此修为的能力。"
            "修为提升需要时间和契机，不能突然暴涨。"
        )
        ctx.hard.append(ability_template.replace("{cultivation}", player_cultivation))

        # Context-specific: active NPC boundary — only list important NPCs
        if active_npc_names:
            npc_list = "、".join(active_npc_names[:12])  # cap at 12 names
            ctx.hard.append(
                f"当前场景中有以下角色可自然出现：{npc_list}。"
                f"其他角色不得无故出现，但可通过叙事合理引入。"
            )
        # else: if no important NPCs, don't add an active NPC constraint
        else:
            ctx.hard.append("当前场景无活跃NPC，AI不得凭空创造新角色。")

        return ctx

    def to_prompt_block(self, constraints: ConstraintSet) -> str:
        """Serialize constraints into the Htem [场景约束] block.

        Output format:
          [场景约束]
          硬限制：
            - rule
          软引导：
            - rule
          强制触发：
            - 当condition时：action
        """
        sections: list[str] = []

        if constraints.hard:
            hard_lines = ["硬限制："]
            for r in constraints.hard:
                hard_lines.append(f"  - {r}")
            sections.append("\n".join(hard_lines))

        if constraints.soft:
            soft_lines = ["软引导："]
            for r in constraints.soft:
                soft_lines.append(f"  - {r}")
            sections.append("\n".join(soft_lines))

        if constraints.triggers:
            trigger_lines = ["强制触发："]
            for t in constraints.triggers:
                condition = t.get("condition", "")
                action = t.get("action", "")
                trigger_lines.append(f"  - 当{condition}时：{action}")
            sections.append("\n".join(trigger_lines))

        if not sections:
            return ""

        return "【场景约束】\n" + "\n".join(sections)


# Singleton
constraints = NarrativeConstraints()
