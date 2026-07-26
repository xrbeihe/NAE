"""Fix truncated game_engine.py - append missing functions + singleton."""
import re

with open('D:/ANE/backend/ane/game_engine.py', 'r', encoding='utf-8') as f:
    c = f.read()

missing = r"""

    # ── Relationship character extraction ─────────────────────

    async def _extract_related_characters(
        self, db, session_id, user_input,
    ) -> list[str]:
        import re
        items = []
        m = re.search(
            r'(\S{1,4})的(丈夫|老婆|妻子|老公|女友|男友|未婚夫|未婚妻|夫君|娘子|道侣)是(\S{2,4})',
            user_input,
        )
        if m:
            name_or_role = m.group(3)
            target = m.group(1)
            relation = m.group(2)
            items.append(f"{name_or_role}（{target}的{relation}，不在现场）")
            return items
        m = re.search(r'(\S{2,4})的(丈夫|老婆|妻子|老公|女友|男友|未婚夫|未婚妻|夫君|娘子|道侣)', user_input)
        if m:
            target = m.group(1)
            relation = m.group(2)
            items.append(f"{target}的{relation}（不在现场）")
            return items
        return items

    # ── NSFW Material ──────────────────────────────────────────

    async def _build_nsfw_material(self, db, session_id, core_npcs):
        templates = nsfw_data()
        rnd = __import__("random").Random()
        underage_npcs = []
        for npc in (core_npcs or []):
            try:
                if getattr(npc, 'age', None) and npc.age < 18:
                    underage_npcs.append(npc)
            except (ValueError, TypeError):
                pass
        if underage_npcs:
            templates = underage_data()
        scenes = templates.get("scenes", [])
        chosen = rnd.choice(scenes) if scenes else {}
        positions = chosen.get("positions", [])
        chosen_pos = rnd.choice(positions) if positions else {}
        settings = chosen.get("settings", {})
        chosen_setting = rnd.choice(settings) if settings else {}
        entry_line = rnd.choice(chosen_pos.get("entry_lines", [""]))
        wet_line = rnd.choice(chosen_pos.get("wetness_lines", [""]))
        climax_line_f = rnd.choice(chosen_pos.get("female_climax_lines", [""]))
        climax_line_m = rnd.choice(chosen_pos.get("male_climax_lines", [""]))
        aftermath_line = rnd.choice(chosen_pos.get("aftermath_lines", [""]))
        lines = [f"【性爱参考素材】\n推荐体位：{chosen.get('name', '传教士体位')}"]
        if chosen.get('description'):
            lines.append(chosen['description'])
        if entry_line:
            lines.append(f"进入描写参考：{entry_line}")
        if wet_line:
            lines.append(f"湿润描写参考：{wet_line}")
        if climax_line_f:
            lines.append(f"高潮描写参考（女）：{climax_line_f}")
        if climax_line_m:
            lines.append(f"高潮描写参考（男）：{climax_line_m}")
        if aftermath_line:
            lines.append(f"事后描写参考：{aftermath_line}")
        if chosen_setting:
            lines.append(f"场景描述：{chosen_setting.get('description', '')}")
            scents = chosen_setting.get('scents', [])
            if scents:
                lines.append("气味：" + "、".join(rnd.sample(scents, min(len(scents), 2))))
            sounds = chosen_setting.get('sounds', [])
            if sounds:
                lines.append("声音：" + "、".join(rnd.sample(sounds, min(len(sounds), 2))))
        return "\n".join(lines)

    def _build_ntr_material(self, validation):
        if not validation.is_ntr and validation.intent != "ntr":
            return ""
        templates = ntr_data()
        rnd = __import__("random").Random()
        dynamics = templates.get("relationship_dynamics", [])
        arcs = templates.get("psychological_arcs", [])
        scenes = templates.get("scenes", [])
        contrasts = templates.get("contrast_humiliation", {})
        chosen_dyn = rnd.choice(dynamics) if dynamics else {}
        chosen_arc = rnd.choice(arcs) if arcs else {}
        chosen_scene = rnd.choice(scenes) if scenes else {}
        all_c = []
        for k, v in contrasts.items():
            if isinstance(v, list):
                all_c.extend(v)
        chosen_contrast = rnd.choice(all_c) if all_c else ""
        dialogue = templates.get("dialogue_examples", {})

        def _safe_choice(lst):
            return rnd.choice(lst) if lst else "(暂无)"

        tension_items = chosen_scene.get("tension_elements", [])
        tension_block = "\n".join("- " + e for e in tension_items) if tension_items else "(暂无)"

        parts = [
            "【NTR场景参考】",
            "",
            "关系类型：" + chosen_dyn.get("name", ""),
            chosen_dyn.get("description", ""),
            "子类型：" + "、".join(chosen_dyn.get("subtypes", [])),
            "",
            "心理变化链（" + chosen_arc.get("role", "") + "视角）：",
            chosen_arc.get("change_chain", ""),
            "",
            "内心独白示例：",
            _safe_choice(chosen_arc.get("typical_inner_monologue", [])),
            "",
            "场景模板：" + chosen_scene.get("name", ""),
            chosen_scene.get("description", ""),
            "张力要素：",
            tension_block.strip(),
            "",
            "对比/羞辱对话：",
            chosen_contrast,
            "",
            "对话示例：",
            "抵抗阶段：" + _safe_choice(dialogue.get("resistance_phase", [])),
            "动摇阶段：" + _safe_choice(dialogue.get("ambivalence_phase", [])),
            "沉沦阶段：" + _safe_choice(dialogue.get("surrender_phase", [])),
            "原配方：" + _safe_choice(dialogue.get("betrayed_lines", [])),
        ]
        return "\n".join(parts)

    # ── NPC name extraction helper ──────────────────────────────

    async def _random_npc_name(self, db, session_id) -> str:
        result = await db.execute(
            select(NPC.name).where(NPC.session_id == session_id)
        )
        existing_names = {row[0] for row in result.fetchall()}
        return npc_manager._random_name(existing_names)

    # ── Summary ─────────────────────────────────────────────────

    async def _cmd_summary(self, db, session_id, user_input) -> TurnResult:
        try:
            summary_text = await self._generate_summary(db, session_id, user_input, "")
            return TurnResult(is_system_command=True, system_response=f"【剧情回顾】\n{summary_text}")
        except Exception as e:
            logger.exception("Summary generation failed")
            return TurnResult(is_system_command=True, system_response=f"生成回顾失败：{e}")

    async def _generate_summary(
        self, db, session_id, user_input, last_narrative, user_id="",
    ) -> str:
        conv = await memory_manager.get_full_conversation(db, session_id)
        if not conv:
            return "（暂无剧情回顾）"
        conv_text = "\n".join(
            f"Turn {m.turn_number}: {m.content[:300]}" for m in conv[-10:]
        )
        prompt = (
            "你是一个修仙故事总结引擎。根据以下对话记录，生成一段200字以内的剧情回顾。\n"
            "重点概括：主要事件、关键NPC互动、位置变化、修为进展。\n"
            "使用中文叙事风格，语气像小说章节回顾一样自然。\n\n"
            f"对话记录：\n{conv_text}\n\n"
            "剧情回顾："
        )
        result = await model_adapter.generate(
            prompt,
            user_id=user_id, session_id=session_id, label="summary",
        )
        return result.strip() if result else "（暂无剧情回顾）"

    async def _cmd_list_facts(self, db, session_id) -> TurnResult:
        facts = await memory_manager.get_facts(db, session_id)
        if not facts:
            return TurnResult(is_system_command=True, system_response="【世界事实】\n（暂无事实）")
        lines = ["【世界事实】"]
        for f in facts:
            lines.append(f"[{f.category}] (P{f.priority}) {f.content}")
        return TurnResult(is_system_command=True, system_response="\n".join(lines))


# Singleton
game_engine = GameEngine()
"""

c += missing
with open('D:/ANE/backend/ane/game_engine.py', 'w', encoding='utf-8') as f:
    f.write(c)
print(f"Done! File size: {len(c)} bytes")
