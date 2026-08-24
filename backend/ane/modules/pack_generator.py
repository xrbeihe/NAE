"""Worldview pack generator — turn a short author form into a complete pack.

The generic narrative kernel (叙事原则/输出 JSON 骨架/禁反问等) is engine-owned
and auto-appended via `assembly: "shell+kernel"`. The generator therefore
produces the *shell* only — role line, world setting, genre-specific NPC
behavior and state_change usage — plus the 10 data files. Authors can refine
the generated files by hand afterwards.
"""

import io
import json
import re
import zipfile
from pathlib import Path

from ane.worldview import _is_valid_id

# ── Shared template fragments ─────────────────────────────────

_GENRES = {
    "fantasy": {
        "role": "你是一个{worldview_name}的叙事引擎。你的职责是讲述故事、描写场景、扮演NPC。",
        "power_hint": "力量体系为剑与魔法——骑士、游侠、魔法师、盗贼、牧师等职业；魔法需要吟唱与施法材料。",
        "money_note": "货币为金币、银币、铜币。",
        "behavior": [
            "NPC按照各自的社会身份活动（农夫耕作、骑士巡逻、商贩叫卖等），玩家不主动接触则不会特意搭理玩家。",
            "不要让角色表现出超出其身份的能力（平民不会突然释放火球术，见习骑士不会单挑巨龙）。",
            "有领地归属的NPC，除非剧情需要，默认在自己的领地活动。",
        ],
        "rec_types": "冒险、委托、社交、探索",
        "scenes": ["城堡", "酒馆", "村庄", "森林", "地下城", "码头"],
    },
    "modern": {
        "role": "你是一个{worldview_name}的叙事引擎。你的职责是讲述故事、描写场景、扮演NPC。",
        "power_hint": "现实世界的社会规则（法律、职业、金钱、人际关系）即为力量体系，没有超自然或异世界元素。",
        "money_note": "货币为当地法定货币（如人民币、美元、欧元）。",
        "behavior": [
            "NPC按照各自的社会身份活动（上班、逛街、闲聊、通勤等），玩家不主动接触则不会特意搭理玩家。",
            "不要让角色表现出超出其社会地位的能力（学生不会突然有亿万资产，普通职员不会认识政要）。",
            "有工作的NPC，除非剧情需要，默认在各自的工作场所或住所活动。",
        ],
        "rec_types": "社交、工作、消费、探索",
        "scenes": ["写字楼", "地铁站", "便利店", "大学", "住宅小区", "商场"],
    },
    "scifi": {
        "role": "你是一个{worldview_name}的叙事引擎。你的职责是讲述故事、描写场景、扮演NPC。",
        "power_hint": "力量体系为未来科技——智能装备、义体改造、太空航行；科技虽先进但受物理与资源约束。",
        "money_note": "货币为通用信用点或星际通用货币。",
        "behavior": [
            "NPC按照各自的社会身份活动（船员执勤、商人交易、技师检修等），玩家不主动接触则不会特意搭理玩家。",
            "不要让角色表现出超出其身份的能力（底层工人不会突然指挥星舰，普通安保不会单挑机甲）。",
            "有组织的NPC，除非剧情需要，默认在各自的岗位或驻地活动。",
        ],
        "rec_types": "任务、社交、探索、贸易",
        "scenes": ["空间站", "星港", "都市贫民区", "科研实验室", "采矿基地", "废墟"],
    },
    "xianxia": {
        "role": "你是一个{worldview_name}的叙事引擎。你的职责是讲述故事、描写场景、扮演NPC。",
        "power_hint": "力量体系为修炼境界（炼气→筑基→金丹→元婴…），一切以灵气和修炼为基础，没有魔法或科技。",
        "money_note": "货币为灵石。",
        "behavior": [
            "NPC按照各自的社会身份活动（修士修行、坊市交易、宗门执勤等），玩家不主动接触则不会特意搭理玩家。",
            "不要让角色表现出超出其修为的能力。",
            "有宗门归属的NPC，除非剧情需要，默认在自己的宗门活动。",
        ],
        "rec_types": "修炼、社交、探索、任务",
        "scenes": ["宗门", "坊市", "秘境", "山脉", "城镇", "洞府"],
    },
}


def _field_names(author):
    """Map short author labels to the data keys used across the pack."""
    power = (author.get("power_name") or "").strip()
    role_label = (author.get("role_label") or "").strip()
    status_label = (author.get("status_label") or "").strip()

    cultivation_label = power or "修为"
    default_name = author.get("default_name") or "无名旅人"
    cultivation_value = "平民"
    if not status_label:
        status_label = role_label or "角色"

    return {
        "cultivation_label": cultivation_label,
        "default_name": default_name,
        "cultivation_value": cultivation_value,
        "status_label": status_label,
        "role_label": role_label or status_label,
    }


def build_system_prompt(author: dict) -> str:
    """Build the worldview shell (kernel is appended by the engine)."""
    genre = author.get("genre") or "fantasy"
    genre_cfg = _GENRES.get(genre, _GENRES["fantasy"])
    name = (author.get("name") or "").strip() or "新世界观"
    desc = (author.get("description") or "").strip()
    fields = _field_names(author)
    power_hint = (author.get("power_hint") or "").strip() or genre_cfg["power_hint"]
    money_note = (author.get("money_note") or "").strip() or genre_cfg["money_note"]

    # Rich optional inputs
    world_setting = (author.get("world_setting") or "").strip()  # 长描述
    era = (author.get("era") or "").strip()                       # 时代/纪元
    factions = author.get("factions") or []                       # 势力/组织
    if isinstance(factions, str):
        factions = [p.strip() for p in re.split(r"[、,，;；\n]", factions) if p.strip()]

    lines = [
        genre_cfg["role"].format(worldview_name=name),
        "",
    ]
    # World description: long setting takes precedence, else short description
    if world_setting:
        lines.append(f"世界观：{world_setting}")
    elif desc:
        lines.append(f"世界观：{desc}。")
    else:
        lines.append(f"世界观：{name}。")
    if era:
        lines.append(f"时代背景：{era}。")
    lines.append(f"{power_hint} {money_note}")
    lines.append("注意：世界观是背景框架而非限制——玩家的意愿凌驾于世界观之上。玩家希望角色拥有奇特设定，直接照做即可。")
    if factions:
        lines.append(f"主要势力：{'、'.join(factions)}。各势力有自己的利益、地盘与行事风格，NPC 依所属势力活动。")
    lines.append("")
    lines.append("【NPC行为·本世界观特定】")
    lines += ["- " + b for b in genre_cfg["behavior"]]
    lines += [
        "",
        "【输出格式·本世界观特定】",
        f"- recommendations 推荐内容多样化时涵盖{genre_cfg['rec_types']}等不同类型。",
        "- state_changes 各类型用法：",
        f'  - status_change：target="player", field="{fields["cultivation_label"]}", value="新值" → 更新玩家属性',
        "  - location_change：target=\"player\", value=\"新地名\" → 更新玩家位置",
        "  - npc_status / character_status：target=NPC名, field=\"任意字段\", value=\"新值\" → 更新NPC状态",
    ]
    return "\n".join(lines)


def _build_manifest(author: dict) -> dict:
    fields = _field_names(author)
    genre = author.get("genre") or "fantasy"
    return {
        "worldview_id": (author.get("id") or "").strip(),
        "name": (author.get("name") or "").strip() or "新世界观",
        "version": "0.1.0",
        "author": (author.get("author") or "").strip() or "ANE 作者",
        "description": (author.get("description") or "").strip(),
        "base_map": genre,
        "maturity_rating": "adult",
        "tags": author.get("tags") or [genre],
        "assembly": "shell+kernel",
        "player_defaults": {
            "name": fields["default_name"],
            "cultivation": fields["cultivation_value"],
            "status_label": fields["status_label"],
        },
        "extra_event_types": [],
    }


def _build_world_templates(author: dict) -> dict:
    genre = author.get("genre") or "fantasy"
    genre_cfg = _GENRES.get(genre, _GENRES["fantasy"])
    user_places = author.get("places") or []
    if isinstance(user_places, str):
        user_places = [p.strip() for p in re.split(r"[、,，;；\n]", user_places) if p.strip()]
    scenes = list(dict.fromkeys(user_places or genre_cfg["scenes"]))
    regions = []
    for i, s in enumerate(scenes):
        regions.append({
            "name": s,
            "type": "city" if i == 0 else "area",
            "description": f"{s}——{author.get('name','')}世界中的一处地点。",
        })
    # Factions → organization-style regions so they exist in the world
    factions = author.get("factions") or []
    if isinstance(factions, str):
        factions = [p.strip() for p in re.split(r"[、,，;；\n]", factions) if p.strip()]
    for f in factions:
        regions.append({
            "name": f,
            "type": "faction",
            "description": f"{f}——{author.get('name','')}世界中的主要势力，有自己的利益与地盘。",
        })
    return {"regions": regions, "sects": [], "settlements": [], "sect_filters": []}


def _build_player_templates(author: dict) -> dict:
    fields = _field_names(author)
    professions = author.get("professions") or []
    if isinstance(professions, str):
        professions = [p.strip() for p in re.split(r"[、,，;；\n]", professions) if p.strip()]
    if not professions:
        professions = ["平民", "旅人", "商贩", "佣兵", "学者"]
    identities = {}
    for p in professions:
        identities[p] = {
            "label": p,
            "desc": f"{author.get('name','')}世界中的{p}",
            "clothing": "",
            "background": "",
        }
    # Author-provided special abilities → golden_fingers (card grid in the form)
    golden_fingers = []
    abilities = author.get("golden_fingers") or []
    if isinstance(abilities, str):
        abilities = [p.strip() for p in re.split(r"[、,，;；\n]", abilities) if p.strip()]
    for i, ab in enumerate(abilities):
        golden_fingers.append({
            "id": f"ability_{i+1}",
            "icon": "⭐",
            "name": ab,
            "tagline": "",
            "desc": f"{ab}——{author.get('name','')}世界中一种独特的能力或际遇。",
        })
    return {
        "genders": [{"value": "男", "label": "男"}, {"value": "女", "label": "女"}],
        "cultivations": [
            {"value": p, "label": p, "desc": f"{author.get('name','')}世界中的{p}"}
            for p in professions
        ],
        "personalities": [
            {"value": "随和", "label": "随和", "desc": "好相处，情绪稳定。"},
            {"value": "外向", "label": "外向开朗", "desc": "爱交朋友，自来熟。"},
            {"value": "谨慎", "label": "谨慎多疑", "desc": "凡事留三分。"},
            {"value": "理想主义", "label": "理想主义", "desc": "相信努力能改变世界。"},
        ],
        "backgrounds": [
            {
                "value": "普通出身",
                "label": "普通出身（无名之辈）",
                "desc": "出生在平凡环境中，一步步靠自己的努力走到今天。",
                "initial_resource": "微薄的积蓄和一身孤勇。",
                "personality_tendency": "踏实、坚韧，偶尔略显保守。",
                "typical_sect_path": "从底层开始，慢慢积累名声。",
            },
            {
                "value": "显赫家世",
                "label": "显赫家世（名门之后）",
                "desc": "出身望族，人脉与资源天然多于常人，但也背负家族期望。",
                "initial_resource": "充足的家底和一张熟悉的人脉网。",
                "personality_tendency": "自信、松弛，偶尔不食人间烟火。",
                "typical_sect_path": "家族安排的道路，或自己另辟蹊径。",
            },
        ],
        "identities": identities,
        "golden_fingers": golden_fingers,
    }


def _build_constraints(author: dict) -> dict:
    fields = _field_names(author)
    name = (author.get("name") or "").strip() or "新世界观"
    taboos = author.get("taboos") or []
    if isinstance(taboos, str):
        taboos = [p.strip() for p in re.split(r"[、,，;；\n]", taboos) if p.strip()]
    factions = author.get("factions") or []
    if isinstance(factions, str):
        factions = [p.strip() for p in re.split(r"[、,，;；\n]", factions) if p.strip()]

    hard = [
        f"这是一个{name}世界。遵循该世界的物理规律与社会规则，不得混入其他世界观的力量体系。",
        "NPC的性格和行为必须与其设定一致。冷漠的人不会突然热情，高傲的人不会低声下气。",
        "NPC只能在自身当前位置出现。在远方的NPC不能突然现身。",
        f"保持角色（包括玩家和NPC）的所有状态信息（{fields['cultivation_label']}、身份、位置）与数据库记录完全一致，不得随意修改或编造。前一回合的状态必须继承到当前回合。",
        "禁止使用'洗得发白'这个短语来形容衣物——属于低质量模板化描写。如需描写旧衣物，请用更具体的手法（褪色的纹路、磨破的袖口、浆洗发硬的布料、颜色不均的补丁等）。",
    ]
    # World taboos → hard constraints in in-world phrasing
    for t in taboos:
        hard.append(f"此方世界的禁忌：{t}。违背者将付出代价，叙事中必须体现这一规则。")
    if factions:
        hard.append(f"此方世界的主要势力为{'、'.join(factions)}。势力间的冲突与结盟是剧情的背景，NPC的行动受其所属势力约束。")

    return {
        "hard": hard,
        "soft": [
            "场景描述应体现当前地点的独特氛围，避免千篇一律的模板化描写。",
            "对话应体现角色的性格特征和社会身份。",
            "适度引入环境细节（天气、声音、气味）增强代入感。",
        ],
        "triggers": [],
        "context_templates": {
            "ability_cap": f"玩家当前{fields['cultivation_label']}为{{cultivation}}，不得表现出超出此水平的资源、权力或能力。提升需要时间和契机，不能突然暴涨。",
            "nsfw_intent": "玩家意图进行性爱互动。根据场景性质选择Type 1（刺激插曲）或Type 2（情节性性爱）的描写模式，详见【性爱场景核心规范】。严格遵循描写规范中的所有规则。无论哪种模式，NPC不能以反问/拖延/兜圈子的方式阻滞叙事，每轮都必须有推进感。",
        },
        "modeler_blurb": author.get("description") or f"{name}世界观。",
    }


def _build_panel(author: dict) -> dict:
    fields = _field_names(author)
    return {
        "title": "【主角面板】",
        "join": " ｜ ",
        "fields": [
            {"label": "姓名", "kind": "composite", "format": "{name} ｜ {gender} ｜ {age}岁",
             "source": {"name": "player.name", "gender": "attrs.gender", "age": "attrs.age"}},
            {"label": fields["cultivation_label"], "key": "cultivation", "source": "player"},
            {"label": "性格", "key": "personality", "source": "attrs", "default": "未知"},
            {"label": "身份", "key": "identity", "source": "attrs", "default": "未知"},
            {"label": "位置", "key": "location", "source": "player", "default": "未知"},
            {"label": "衣物", "key": "clothing", "source": "attrs", "default": "未设定"},
            {"label": "物品", "key": "inventory", "source": "items", "join": "、"},
            {"label": "扩展", "key": "_extensions", "source": "exts", "show_if": "truthy"},
        ],
    }


def _build_ui(author: dict) -> dict:
    fields = _field_names(author)
    name = (author.get("name") or "").strip() or "新世界观"
    return {
        "labels": {
            "role": fields["status_label"],
            "cultivation": fields["cultivation_label"],
            "spiritual_root": "",
            "sect": "",
            "golden_finger": "",
        },
        "create_button": author.get("create_button") or "开始旅程",
        "modal_title": "创建你的" + (fields["role_label"] or "角色") + "（" + name + "）",
        "default_session_name": name,
        "welcome_prefix": "已进入「",
        "welcome_suffix": "」",
        "character_card": {
            "title": "📋 **角色创建成功**",
            "lines": [
                {"label": "姓名", "key": "name"},
                {"label": "性别", "composite": "性别：{gender} ｜ 年龄：{age}岁"},
                {"label": fields["cultivation_label"], "key": "cultivation"},
                {"label": "身份", "composite": "身份：{identity} — {identity_desc}"},
                {"label": "性格", "key": "personality"},
                {"label": "出身", "key": "background_summary"},
                {"label": "衣物", "key": "clothing", "default": "未设定"},
            ],
            "conditional": [
                {"label": "初始位置", "key": "location"},
            ],
        },
        "initial_recommendations": {
            "base": ["四处走走，熟悉周围的环境", "找当地人打听本地的消息", "去市集或商店看看有没有合适的装备"],
            "with_sect": [],
            "with_golden_finger": [],
            "without_golden_finger": [],
            "tail": ["检查随身物品，清点积蓄", "向遇到的人打听附近的风土人情", "寻找能提升自己能力的机会"],
        },
        "npc_archetypes": [
            {"name": "路人甲", "desc": "路过的普通人"},
            {"name": "店主", "desc": "常去的店铺老板"},
            {"name": "同路人", "desc": "与玩家同行的旅伴"},
        ],
    }


def _build_modeler(author: dict) -> str:
    name = (author.get("name") or "").strip() or "新世界观"
    desc = author.get("description") or f"{name}世界观。"
    return (
        "你是一个{worldview_name}角色建模师。玩家在{worldview_name}中标记了一个重要人物「{npc_name}」。\n\n"
        f"世界观：{desc}\n"
        "注意：世界观是背景参考而非限制。玩家的意愿凌驾于世界观之上。\n"
        "如果玩家要求的设定看起来不符合{worldview_name}，直接照做即可——玩家说怎么穿就怎么穿，说长什么样就长什么样。\n"
        "玩家没有明确说的一律按{worldview_name}惯例推演补全。\n"
    )


_AGE_RULES = (
    "6. 年龄限制：对年轻俊美的角色（无论男女），除非玩家明确给出年龄，一律设定在40岁以下。外貌应符合实际年龄印象。"
    "如果玩家没有提及年龄，按该世界观的常规推断。年轻俊美的角色统一在40岁以下外貌。\n"
)


def _build_modeler_schema(author: dict) -> dict:
    """Build a worldview-neutral NPC model field tree for the generated pack.

    Generic across genres: identity + appearance + inner self. Genres with a
    power system get an extra power_details section; authors can extend freely.
    """
    power_label = (author.get("power_system") or "").strip() or "能力"
    schema = {
        "basic": {"name": "", "race": "", "gender": "", "age": 0, "identity": "", "position": ""},
        "appearance": {
            "overall_impression": "", "body_proportion": "", "aura": "",
            "hair": {"length": "", "style": "", "color": ""},
        },
        "personality": {"core": "", "values": "", "fears": "", "likes": ""},
        "background": {"history": "", "major_events": ""},
        "relationships": {"friends": [], "enemies": [], "lover": ""},
        "attitude_to_player": {"surface": "", "true_feelings": ""},
        "knowledge_bounds": {"knows": [], "does_not_know": []},
    }
    if power_label:
        schema["power_details"] = {
            "system": power_label,
            "level": "",
            "abilities": [],
        }
    return schema


def _build_form(author: dict) -> dict:
    """Build a declarative form.json for the generated pack."""
    fields = _field_names(author)
    professions = author.get("professions") or []
    if isinstance(professions, str):
        professions = [p.strip() for p in re.split(r"[、,，;；\n]", professions) if p.strip()]
    if not professions:
        professions = ["平民", "旅人", "商贩", "佣兵", "学者"]
    name = (author.get("name") or "").strip() or "新世界观"

    return {
        "title": "创建你的" + (fields["role_label"] or "角色") + "（" + name + "）",
        "fields": [
            {"key": "name", "label": "姓名", "kind": "text", "placeholder": "输入你的名字", "default": "", "maxlength": 20, "random_button": True, "store": "player.name"},
            {"key": "age", "label": "年龄", "kind": "number", "default": 19, "min": 12, "max": 999, "store": "attrs.age"},
            {"key": "gender", "label": "性别", "kind": "select", "options_from": "genders", "store": "attrs.gender"},
            {"key": "background", "label": "出身背景", "kind": "select", "options_from": "backgrounds",
             "hint_template": "{desc} ｜ 初始资源：{initial_resource} ｜ 性格倾向：{personality_tendency}",
             "store": "attrs.background", "derive": ["background_summary"]},
            {"key": "cultivation", "label": fields["cultivation_label"], "kind": "select", "options_from": "cultivations",
             "hint_template": "{desc}", "allow_custom": True, "custom_label": "自定义" + fields["cultivation_label"],
             "store": "player.cultivation"},
            {"key": "personality", "label": "性格", "kind": "select", "options_from": "personalities",
             "hint_template": "{desc}", "allow_custom": True, "custom_label": "自定义性格描述", "store": "attrs.personality"},
            {"key": "identity", "label": "身份", "kind": "select", "options_from": "identities",
             "hint_template": "衣物：{clothing}",
             "allow_custom": True, "custom_label": "自定义身份描述",
             "store": "attrs.identity", "derive": ["identity_desc", "clothing", "background_summary"]},
            {"key": "golden_finger", "label": "特殊能力", "kind": "card_grid", "options_from": "golden_fingers",
             "allow_custom": True, "custom_label": "自定义", "visible_if": "has_golden_fingers",
             "option_map": {"id": "golden_finger_id", "name": "golden_finger_name", "tagline": "golden_finger_tagline", "desc": "golden_finger_desc"},
             "store": "attrs.golden_finger_id"},
        ],
    }


def _build_npc_templates(author: dict) -> dict:
    """Build a name pool + identity pool matching the chosen genre.

    Keeps generated packs' passerby NPCs from falling back to xianxia names.
    """
    genre = author.get("genre") or "fantasy"
    pools = {
        "fantasy": {
            "surnames": ["阿尔文", "贝里安", "凯尔", "多恩", "埃德加", "法尔克", "加雷斯", "哈罗德", "伊索德", "杰斯珀"],
            "given_m": ["罗恩", "塞德里克", "加里安", "奥尔德", "佩特", "威尔", "摩根", "卢克", "托比", "加文"],
            "given_f": ["艾琳", "布蕾妮", "赛琳娜", "多丽丝", "艾莉诺", "菲奥娜", "格温", "哈莉", "伊莎贝拉", "杰西卡"],
            "identities": ["村民", "铁匠", "酒馆老板", "商贩", "猎人", "守卫", "流浪诗人", "药剂师", "马夫", "旅店伙计"],
        },
        "modern": {
            "surnames": ["陈", "李", "王", "张", "刘", "杨", "黄", "赵", "吴", "周"],
            "given_m": ["伟", "强", "磊", "洋", "勇", "军", "杰", "涛", "明", "超"],
            "given_f": ["芳", "娜", "敏", "静", "丽", "婷", "娟", "倩", "颖", "玲"],
            "identities": ["程序员", "店员", "教师", "快递员", "护士", "司机", "销售", "学生", "设计师", "会计"],
        },
        "scifi": {
            "surnames": ["维恩", "卡恩", "拉迪斯", "奥雷尔", "森", "齐格", "诺瓦", "埃克斯", "塔娜", "尤里"],
            "given_m": ["泽恩", "科尔", "达里安", "埃德", "福克斯", "盖尔", "海顿", "伊恩", "杰斯", "凯文"],
            "given_f": ["阿雅", "布琳娜", "塞拉", "德拉", "伊芙", "法拉", "格蕾丝", "霍普", "艾拉", "朱诺"],
            "identities": ["星舰工程师", "空间站保安", "货运商", "医生", "驾驶员", "技师", "信息员", "矿工", "科学家", "商人"],
        },
        "xianxia": {
            "surnames": ["林", "苏", "柳", "沈", "萧", "叶", "云", "白", "墨", "韩"],
            "given_m": ["寒渊", "无极", "辰逸", "子墨", "昊天", "云霆", "凌霄", "长风", "星河", "千帆"],
            "given_f": ["雨凝", "如烟", "清漪", "若雪", "霜华", "月瑶", "灵素", "芷柔", "璃歌", "云裳"],
            "identities": ["散修", "宗门弟子", "坊市掌柜", "药童", "守山弟子", "猎妖人", "丹师", "器师", "镖师", "驿卒"],
        },
    }
    pool = pools.get(genre, pools["fantasy"])
    # Author-provided surnames override the genre default
    author_surnames = author.get("npc_names") or []
    if isinstance(author_surnames, str):
        author_surnames = [p.strip() for p in re.split(r"[、,，;；\n]", author_surnames) if p.strip()]
    surnames = author_surnames or pool["surnames"]
    return {
        "surnames": surnames,
        "given_names_male": pool["given_m"],
        "given_names_female": pool["given_f"],
        "identities": pool["identities"],
    }


def _build_world_facts(author: dict) -> dict | None:
    """Build a world_facts.json skeleton for IP-based worldviews.

    Only produced when the author marks the pack as based on an existing work.
    """
    if not author.get("ip_based"):
        return None
    ip_work = (author.get("ip_work") or "").strip()
    name = (author.get("name") or "").strip() or "新世界观"
    must = []
    if ip_work:
        must.append(f"故事基于作品《{ip_work}》的世界观展开")
    return {
        "knowledge_mode": "hybrid",
        "must_follow": must,
        "forbidden": [],
        "characters": [],
    }


def _build_events(author: dict) -> dict:
    genre = author.get("genre") or "fantasy"
    if genre == "xianxia":
        event_type = "cultivation_progress"
    elif genre == "modern":
        event_type = "routine_progress"
    elif genre == "scifi":
        event_type = "advancement_progress"
    else:
        event_type = "adventure_progress"
    # Author event theme → used in idle event descriptions
    theme = (author.get("event_theme") or "").strip()
    if theme:
        idle_desc = f"听闻{{npc_name}}与{theme}相关的事有了新进展。"
        seclusion_desc = f"{{npc_name}}在静修后，似乎与{theme}的关联更进了一步。"
    else:
        idle_desc = "听闻{npc_name}近日有所进展。"
        seclusion_desc = "{npc_name}在静修/闭关后有所精进。"
    return {
        "seclusion_threshold": 2160,
        "seclusion_event": {"type": event_type, "description": seclusion_desc},
        "idle_threshold": 2160,
        "idle_probability": 0.2,
        "idle_events": [
            {"type": event_type, "description": idle_desc},
            {"type": "random_encounter", "description": "{npc_name}似乎经历了一些事，但详情不明。"},
        ],
    }


# ── Public API ───────────────────────────────────────────────

def build_pack(author: dict) -> dict:
    """Build a full pack file map {relative_path: str}. Raises ValueError on bad id."""
    wv_id = (author.get("id") or "").strip()
    if not _is_valid_id(wv_id):
        raise ValueError(f"无效的世界观 ID: {wv_id!r}（仅允许小写字母/数字/下划线，1-48 字符）")

    files = {
        "manifest.json": _build_manifest(author),
        "system_prompt.txt": build_system_prompt(author),
        "intent_keywords.json": {"exclusions": {}},
        "constraints.json": _build_constraints(author),
        "world_templates.json": _build_world_templates(author),
        "player_templates.json": _build_player_templates(author),
        "npc_templates.json": _build_npc_templates(author),
        "panel.json": _build_panel(author),
        "ui.json": _build_ui(author),
        "events.json": _build_events(author),
        "form.json": _build_form(author),
        "modeler/role.txt": _build_modeler(author),
        "modeler/age_rules.txt": _AGE_RULES,
        "modeler/schema.json": _build_modeler_schema(author),
    }
    # IP-based worldviews also ship a world_facts.json (authoritative canon)
    wf = _build_world_facts(author)
    if wf is not None:
        files["world_facts.json"] = wf
    out = {}
    for path, data in files.items():
        if path.endswith(".json"):
            out[path] = json.dumps(data, ensure_ascii=False, indent=2)
        else:
            out[path] = data
    return out


def pack_to_zip(pack_files: dict) -> bytes:
    """Zip a pack file map into bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in pack_files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def generate_pack_zip(author: dict) -> bytes:
    """Build a pack from the form and return installable zip bytes."""
    return pack_to_zip(build_pack(author))
