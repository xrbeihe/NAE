# NPC_MODELING 重要人物建模系统

> 版本：1.1
> 适用版本：ANE Phase 1 MVP

---

## 概述

当玩家勾选 ⭐ 标记一位 NPC 为"重要人物"时，系统为该 NPC 构建一份**结构化的人物档案**，存入数据库。后续轮次中该 NPC 再次出现时，LLM 从档案中获取精确的人物细节来辅助叙事，确保长期一致性。

---

## 触发条件

- 玩家在前端勾选「⭐ 重要人物」复选框发送输入
- `mark_important_npc=True` 传入后端
- 该 NPC **尚未拥有建模档案**（`long_term_state["model"]` 不存在）
- 已存在模型的重要人物再次勾选 ⭐ → 不触发建模，直接使用已有模型

---

## 链路

### 标记轮（4 次 API）

```
第1次：_extract ──────────── 认人 ────────────────────────────────
  输入：玩家输入
  输出：角色名 + 7 个基本字段
  用途：写入 DB 基本字段 + 注入 prompt 的【重要人物】块

   │
   ▼

第2次：NPC_MODELING ────── 从玩家输入中构建模板 ──────────────────
  输入：玩家输入（"一袭白衣、容貌清冷绝美"）
  输出：完整 16 块 JSON（没填的字段由 LLM 自动推演补全）
  用途：模型存入 NPC.long_term_state["model"]
        同时注入 temp_npc.model_data

   │
   ▼

第3次：llm_main ────────────── 带模型写叙事 ──────────────────────
  输入：完整 Prompt（【重要人物】块已包含模型全部数据）
  输出：narrative + nearby_characters + state_changes
  特点：模型在 prompt 里，LLM 直接引用细节写出场描写
        "写到眼睛就去查丹凤眼浅褐瞳，写到手指就参考修长如葱"

   │
   ▼

第4次：llm_summary ────────── 记笔记 ────────────────────────────
  输入：llm_main 叙事 + 玩家输入
  输出：结构化场景事实（shortmemory 摘要）
```

**建模当轮**叙事就是精细描写版，不需要等下一轮。

### 普通轮（2 次 API）

```
llm_main（带模型参考写叙事） → llm_summary（记笔记）
```

prompt 中【重要人物】块渲染完整模板，LLM 按需引用。无额外 API。

---

## 建模原则

1. **玩家输入提到什么就填什么**——不要忽略玩家给的任何信息
2. **没有明确依据的字段**，根据已有信息（身份、修为、背景、性格等）合理推演补全
3. 补全内容不能与玩家明确说的事实矛盾
4. 外貌/身体/穿着等描写**具体有画面感**
5. **大胆补全，不要留空**——玩家不满意后续可发指令修改（LLM 看到指令后自动识别并修改对应字段）

---

## 模型后续使用

### prompt 注入

重要人物出现在 Active Set 中时，`_render_important_npc_full()` 检测 `model_data`：

- 有模型 → 渲染完整模板块（16 块信息）
- 无模型 → 渲染旧版精简格式（仅有姓名/修为/身份/位置）

渲染后的模板块作为【重要人物】的子部分注入 llm_main 的 prompt：

```
【重要人物】
⭐ 白慕彩（重要人物）

—— 基础身份 ——
女 | 25岁 | 金丹期 | 青云宗长老 | 师尊

—— 外貌（整体） ——
整体印象：清冷如霜月，不怒自威
气质：清冷

—— 脸部 ——
脸型：瓜子脸
眼睛：丹凤眼，眼尾微挑，瞳色浅褐
嘴唇：上薄下厚，唇珠明显

—— 手部 ——
手指：修长如葱

—— 行为特征 ——
站姿：脊背挺直如松
小动作：思考时指尖轻叩桌面
……

—— 性格 ——
核心性格：外冷内热
执念：对剑道的极致追求近乎偏执

—— 对玩家的态度 ——
表层态度：冷淡而疏离
真实想法：留意到这个弟子的资质和心性，但不想表露
关系变化倾向：正在逐渐留意中
```

### NSFW 控制

- 普通轮：`nsfw_active=False` → 不渲染 NSFW 块
- NSFW 轮：`nsfw_active=True` → 渲染 NSFW 身体特征块

### 参考原则

模型只作为 LLM 的**参考素材**，LLM 自行决定何时调用哪些细节到叙事中。不需要程序判断"写到什么部位就注入什么数据"。

---

## 系统演化

| 版本 | 标记轮 API 次数 | 建模触发时机 | 模型当轮生效 |
|------|-----------------|-------------|-------------|
| v1（初版） | 5 次 | llm_main 之后独立建模 + llm_main 重写 | ✅ 当轮重写 |
| v1.1（当前） | **4 次** | llm_main 之前从玩家输入建模 | ✅ 当轮直接生效 |

- **v1 去掉了 HTEM**，普通轮从 3 次降到 2 次
- **v1.1 把建模移到 llm_main 之前**，不再需要双次重写，不再需要 llm_main 额外输出 `character_model` 字段

---

## 文件职责

| 文件 | 职责 |
|------|------|
| `modules/npc_modeler.py` | `parse_modeling_response()` 校验模型 JSON + 注入 `model_version`；`render_model_for_prompt()` 渲染成 prompt 文本块 |
| `modules/prompt_builder.py` | `PromptContext` 增加 `nsfw_active` 开关；`_render_important_npc_full()` 检测 `model_data` 存在时渲染完整模板块 |
| `game_engine.py` | 标记轮：`_extract` 认人 → pre-llm_main 建模 → 存 DB + 注入 `NPCContext.model_data` → llm_main |
| `output_parser.py` | `ParsedOutput.character_model` 字段（保留但不再使用——建模已前置） |

### 数据库

**零表结构变更。** 模型数据存入 `NPC.long_term_state["model"]` JSON 字段。

---

## 模板完整结构

```json
{
  "model_version": "1.0",

  "basic": {
    "name": "", "race": "", "gender": "", "age": 0, "height": 0,
    "cultivation": "", "identity": "", "faction": "", "position": ""
  },

  "appearance": {
    "overall_impression": "", "body_proportion": "", "aura": "",
    "face": {
      "shape": "", "features": "", "eyes": "", "lashes": "", "eyebrows": "",
      "nose": "", "lips": "", "teeth": "", "dimples": "",
      "tear_mole": "", "expression_habit": ""
    },
    "skin": { "color": "", "luster": "", "fineness": "" },
    "hair": { "length": "", "style": "", "color": "", "ornament": "" },
    "neck": "", "collarbone": "", "shoulders": "",
    "chest": { "size": "", "shape": "", "fullness": "" },
    "waist": { "muscle_line": "", "slimness": "", "softness": "" },
    "belly": "",
    "buttocks": { "size": "", "curve": "" },
    "hips": "",
    "legs": { "length": "", "muscle_tone": "", "thighs": "" },
    "feet": { "shape": "", "size": "", "barefoot": false },
    "hands": { "fingers": "", "back": "" }
  },

  "voice": { "timbre": "", "speed": "", "volume": "" },

  "clothing": {
    "type": "", "color": "", "material": "", "pattern": "", "collar": "",
    "outerwear": "", "belt": "", "hosiery": "", "shoes": ""
  },

  "jewelry": { "earrings": "", "necklace": "", "rings": "", "bracelets": "" },

  "equipment": [{ "name": "", "description": "", "position": "" }],

  "behavior": {
    "stance": "", "sitting": "", "gait": "", "smile": "",
    "mannerisms": "", "speech_rhythm": "", "catchphrase": ""
  },

  "speech_style": {
    "word_habits": "", "particles": "", "address_player": "",
    "address_others": "", "when_angry": ""
  },

  "combat_style": {
    "preference": "", "weapon_usage": "", "battle_cry": "",
    "spirit_power_signature": ""
  },

  "personality": {
    "core": "", "values": "", "principles": "", "bottom_line": "",
    "interests": "", "fears": "", "aversions": "", "likes": "", "obsession": ""
  },

  "background": {
    "history": "", "major_events": "", "faction_affiliation": "", "family": ""
  },

  "cultivation": {
    "spiritual_root": "", "special_constitution": "", "techniques": "",
    "divine_powers": "", "ring_storage": "", "wealth": ""
  },

  "knowledge_bounds": {
    "knows": [], "does_not_know": [], "suspicious_of": []
  },

  "attitude_to_player": {
    "surface": "", "true_feelings": "", "relationship_trend": ""
  },

  "relationships": {
    "father": "", "mother": "", "master": "", "senior_brother": "",
    "senior_sister": "", "junior_brother": "", "junior_sister": "",
    "teacher": "", "superior": "", "subordinate": "",
    "friends": [], "enemies": [],
    "lover": "", "fiance": "", "beloved": "", "rival": "", "pursuer": ""
  },

  "nsfw": {
    "is_virgin": true, "fertility": "",
    "desire_toward_target": "", "rejection_toward_target": "",
    "male_genital": "", "female_genital": ""
  }
}
```
