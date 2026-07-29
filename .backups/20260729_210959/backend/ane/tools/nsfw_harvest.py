#!/usr/bin/env python3
"""NSFW内容收割工具 — 从网页抓取小说片段 → LLM提取结构化数据 → 写入nsfw_templates.json

用法:
  ane/tools/nsfw_harvest.py <URL>                     # 从单个URL提取
  ane/tools/nsfw_harvest.py --search <搜索结果页URL>   # 列出搜索结果，选一个
  ane/tools/nsfw_harvest.py --batch <URL> <URL> ...    # 批量处理多个URL
"""

import asyncio
import json
import logging
import os
import re
import sys
import textwrap
from pathlib import Path

import httpx

# 项目路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("nsfw_harvest")

# NSFW模板文件路径
NSFW_PATH = _PROJECT_ROOT / "backend" / "ane" / "content" / "nsfw_templates.json"
UNDERAGE_PATH = _PROJECT_ROOT / "backend" / "ane" / "content" / "underage_templates.json"
NTR_PATH = _PROJECT_ROOT / "backend" / "ane" / "content" / "ntr_templates.json"

# ── 配置（从ANE配置继承） ────────────────────────────────────

def _load_config():
    """加载ANE配置以复用DeepSeek API key"""
    from ane.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
    return DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

# ── 第1步：抓取网页 ──────────────────────────────────────────

async def fetch_page(url: str, direct: bool = False) -> str:
    """抓取网页HTML"""
    logger.info(f"抓取: {url}")
    kwargs = dict(
        timeout=30.0,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
    )
    if direct:
        kwargs["trust_env"] = False
    async with httpx.AsyncClient(**kwargs) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        # 检测编码
        if resp.charset_encoding:
            resp.encoding = resp.charset_encoding
        else:
            # 常见中文编码检测
            content_type = resp.headers.get("content-type", "")
            if "gbk" in content_type or "gb2312" in content_type:
                resp.encoding = "gbk"
            elif "big5" in content_type:
                resp.encoding = "big5"
        return resp.text


# ── 第2步：提取正文 ──────────────────────────────────────────

def extract_content(html: str, url: str = "") -> str:
    """使用 readability 提取正文，去噪"""
    from bs4 import BeautifulSoup
    from readability import Document

    doc = Document(html)
    summary_html = doc.summary()
    title = doc.short_title() or ""

    if not summary_html or len(summary_html) < 100:
        # readability 失败时回退：取 body 文本
        soup = BeautifulSoup(html, "lxml")
        body = soup.find("body")
        text = body.get_text("\n", strip=True) if body else ""
    else:
        soup = BeautifulSoup(summary_html, "lxml")
        text = soup.get_text("\n", strip=True)

    # 清理多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return f"标题：{title}\n\n{text}" if title else text


# ── 第3步：搜索结果页链接提取 ───────────────────────────────

def extract_links(html: str, base_url: str) -> list[dict]:
    """从搜索结果页提取所有文章链接"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    links = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        text = a_tag.get_text(strip=True)

        # 过滤：至少2个字的链接文本，且不是导航/功能链接
        if len(text) < 4:
            continue
        if href.startswith("javascript:") or href == "#" or href.startswith("mailto:"):
            continue
        # 只保留有意义的链接
        if any(kw in text.lower() for kw in ["上一页", "下一页", "首页", "尾页", "返回", "回复", "引用"]):
            continue

        # 补全相对路径
        from urllib.parse import urljoin
        href = urljoin(base_url, href)

        links.append({"url": href, "title": text})

    return links


def extract_next_page_url(html: str, base_url: str) -> "str | None":
    """从 Blogger 等分页页面提取『下一页』链接（支持 updated-max 参数）"""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    soup = BeautifulSoup(html, "lxml")

    # Blogger 的翻页链接通常在 class="blog-pager-older-link" 或 a 标签含 "Older Posts"
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True)
        cls = " ".join(a_tag.get("class", []) or [])
        # 匹配多种翻页特征
        if any(kw in text.lower() for kw in ["older posts", "上一页", "下一页", "较早", "更早"]):
            return urljoin(base_url, href)
        if "blog-pager-older-link" in cls:
            return urljoin(base_url, href)
    return None


def extract_all_pages(url: str, max_pages: int = 5, direct: bool = False) -> list[dict]:
    """自动翻页，收集多页文章链接"""
    import httpx

    articles = []
    seen_urls = set()
    seen_titles = set()
    page_url = url

    for page_idx in range(max_pages):
        if page_idx > 0:
            print(f"\n  翻页 {page_idx + 1}...")
        try:
            html = httpx.get(page_url, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                             follow_redirects=True).text
        except Exception as e:
            logger.warning(f"翻页 {page_idx + 1} 失败: {e}")
            break

        links = extract_links(html, page_url)

        import re
        new_count = 0
        for l in links:
            title = l["title"]
            url = l["url"]
            if len(re.sub(r'[\s\d\W]', '', title)) < 4:
                continue
            if any(kw in url for kw in ["/search/", "/label/", "archive.html"]):
                continue
            if url.rstrip("/").endswith(".com/") or url.rstrip("/").endswith(".net/"):
                continue
            if title in seen_titles and url in seen_urls:
                continue
            # 去重（只看标题去重更可靠，因为多页可能含相同链接但URL参数不同）
            if title in seen_titles:
                continue
            seen_titles.add(title)
            seen_urls.add(url)
            articles.append(l)
            new_count += 1

        logger.info(f"  第 {page_idx + 1} 页: 新增 {new_count} 篇")

        if new_count == 0:
            logger.info("  无新文章，停止翻页")
            break

        # 找下一页链接
        next_url = extract_next_page_url(html, page_url)
        if not next_url or next_url == page_url:
            logger.info("  无下一页链接，停止翻页")
            break
        page_url = next_url

    return articles


# ── 第4步：LLM结构化提取 ────────────────────────────────────

EXTRACTION_PROMPT = """你是一个小说内容分析器。分析以下小说片段，提取其中的性爱/情色描写元素。

如果片段中没有性爱描写，输出 {"has_nsfw": false}。
如果片段中有性爱描写，提取以下结构：

输出严格JSON格式，不要加任何其他文字：

{
  "has_nsfw": true,
  "positions_used": ["体位名称，从以下标准列表中选择最匹配的：传教士体位、后入式、女上位（骑乘）、侧躺式（汤勺）、站立式、床边式、怀中抱（对面坐）、69式、莲花坐（双修式）、老汉推车、观音坐莲、隔山取火、倒浇蜡烛、推车式、屈膝跪姿、盘腿坐式、侧卧抬腿、骑马式、交叠式、背入趴伏、把尿式、单腿架肩、正位紧抱、背后抱腰、桌沿仰卧、悬空式。如果没有完全匹配的选最接近的，完全无法判断则留空数组"],
  "dialogue_lines": [
    "对话中可复用的精彩台词，每句完整摘录，不含动作描写"
  ],
  "sensory_highlights": {
    "entry": ["进入描写的精华句，1-2句"],
    "wet": ["湿润/体液描写的精华句，1-2句"],
    "rhythm": ["节奏/动态描写的精华句，1-2句"],
    "climax": ["高潮描写的精华句，1-2句"],
    "aftermath": ["事后描写的精华句，1-2句"]
  },
  "mood": "氛围标签，如温柔/激烈/粗暴/温情",
  "foreplay_techniques": ["前戏手法，如接吻、舔阴、揉胸等"],
  "interesting_techniques": ["值得学习的描写技巧，如视角切换、节奏控制、感官叠加等"],
  "body_parts_emphasized": ["重点描写的身体部位"],
  "emotional_arc": "情感变化线，如抗拒→接受→投入→高潮→疲惫",
  "female_state": {
    "mental": "精神状态，如迷离、失神、恍惚、羞涩、沉醉",
    "expression": "表情神态，如眼角含春、眉头微蹙、嘴唇微张、眼尾泛红",
    "speech_style": "语言语气特征，如颤抖、低喃、断断续续、带着哭腔、轻声喘息",
    "body_reaction": "身体反应，如潮红、颤抖、双腿发软、腰肢弓起、肌肤发烫"
  },
  "appearance_highlights": {
    "clothing_state": "衣物状态，如衣衫半褪、腰带松脱、裙摆被撩到腰间、衣襟敞开露出锁骨",
    "arousal_signs": "情动迹象，如乳尖在衣料下凸起、脸颊泛红、眼神湿润、呼吸急促",
    "disheveled": "凌乱美感，如发髻散落青丝垂落、衣物凌乱半遮半掩、吻痕从颈侧延伸到锁骨",
    "body_posture": "性爱姿态，如双腿不自觉地夹紧又松开、腰肢扭动、手指抓紧床单或对方的背",
    "nude_details": "裸露部位的细节，如雪白的肩头在烛光下泛光、大腿内侧的肌肤细腻汗湿、腰窝的阴影",
    "afterglow": "事后模样，如浑身泛着潮红、眼神失焦、呼吸尚未平复、肌肤上残留着汗和吻痕"
  },
  "has_underage": false,
  "character_age_hint": "角色年龄线索。如果有任何角色被直接或间接暗示为未成年（如少女、萝莉、幼女、十四岁、少年、学生、童等），描述具体线索；否则留空字符串",
  "underage_confidence": "low",
  "has_ntr": false,
  "ntr_type": "",
  "ntr_scene_type": "",
  "ntr_psychological_beats": []
}

重要规则：
1. 只提取片段中实际存在的内容，不要编造
2. dialogue_lines 只摘录原文对话，不要改写
3. sensory_highlights 摘录原文中最有画面感的句子，严格按原文用词一字不改（包括肉棒、淫水、骚穴等粗俗词汇），禁止润色或替换为文雅说法
4. 如果某个字段没有合适内容，留空数组或空字符串
5. 确保JSON格式正确，没有尾随逗号
6. 年龄检测：如果文本中出现明确或暗示的未成年角色（少女、萝莉、幼女、学生、少年、丫头、童子等），必须将 has_underage 设为 true，在 character_age_hint 中注明具体线索，并根据线索明确程度设置 underage_confidence 为 high/medium/low
7. NTR/NTL检测（非常重要，须严格执行）：如果片段涉及以下任何情节，必须将 has_ntr 设为 true：
   - 出轨、偷情、第三者、绿帽、绿奴、原配背叛
   - 标题或内容中出现 NTR、NTL、寝取、寝取り、Netorare、Netori
   - 角色有配偶/伴侣但与第三方发生性关系
   - 在伴侣不知情/在场/睡在身边的情况下与其他人发生关系
   - 一方有固定伴侣（丈夫/妻子/男友/女友）仍与他人偷欢
   ntr_type 从以下选择：male_take_female（男夺女，即经典NTR）/ female_take_male（女夺男）/ dual_ntr（双向背叛）/ spiritual_ntr（精神出轨）/ coerced_ntr（强迫型）。ntr_scene_type 从以下选择：phone_scene（电话中偷情）/ sleep_scene（同床偷情）/ home_invasion（家中被侵犯）/ workplace（职场偷情）/ public_place（半公开场所），无法判断则留空。ntr_psychological_beats 摘录原文中体现角色心理变化的句子（愧疚、挣扎、沉沦、对比等），每句15字以上。

小说片段：
"""


async def llm_extract(text: str, api_key: str, base_url: str) -> dict:
    """调用DeepSeek API提取结构化数据"""
    # 截断太长文本（API限制）
    max_chars = 8000
    if len(text) > max_chars:
        # 尽量在段落边界截断
        text = text[:max_chars]
        last_para = text.rfind("\n\n")
        if last_para > max_chars // 2:
            text = text[:last_para]

    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "user", "content": EXTRACTION_PROMPT + text}
        ],
        "temperature": 0.3,  # 低温度保证提取一致性
        "max_tokens": 2048,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]

    # 从响应中提取JSON
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {e}")
            logger.debug(f"原始响应: {content[:500]}")
            return {"has_nsfw": False, "parse_error": str(e)}
    return {"has_nsfw": False, "parse_error": "No JSON in response"}


# ── 第5步：合并到nsfw_templates.json ────────────────────────

def merge_to_templates(extracted: dict, source_url: str = "", target_path=None):
    """将LLM提取的结构化数据合并到指定的模板JSON文件"""
    if not extracted.get("has_nsfw"):
        logger.info("片段中未检测到NSFW内容，跳过")
        return False

    # 根据年龄标记选择目标文件
    if target_path is None:
        if extracted.get("has_underage"):
            target_path = UNDERAGE_PATH
            logger.info("检测到未成年内容 → 写入 underage_templates.json")
        else:
            target_path = NSFW_PATH

    # 读取现有数据
    if target_path.exists():
        with open(target_path, "r", encoding="utf-8") as f:
            templates = json.load(f)
    else:
        templates = {
            "positions": [],
            "foreplay_actions": [],
            "dialogue": {"dominant_male": [], "submissive_female": [],
                         "foreplay_teasing": [], "cultivation_dual_cultivation": [], "aftercare": []},
            "sensory_bundles": {"entry": [], "wet": [], "tight": [],
                                "climax_female": [], "climax_male": [],
                                "pain": [], "breath": [], "aftermath": []},
            "context_rules": [],
            "flow_sequences": {},
            "body_descriptions_female": {"breasts": [], "ass": [], "legs": [],
                                          "waist": [], "mouth": [], "eyes": []},
            "body_descriptions_male": {"chest": [], "cock": [], "hands": []},
        }

    changes = 0

    # 是否有足够的感官内容来填充体位？
    sensory = extracted.get("sensory_highlights", {}) or {}
    has_sensory_content = any(
        isinstance(v, str) and len(v) > 10
        or isinstance(v, list) and any(len(s) > 10 for s in v)
        for v in sensory.values()
    )

    # 合并体位
    positions_used = extracted.get("positions_used", [])
    for pos_name in positions_used:
        pos_name = pos_name.strip()
        if not pos_name:
            continue
        # 检查是否已存在同名体位
        existing = [p for p in templates["positions"] if p["name"] == pos_name]
        if existing:
            # 已存在：尝试用新内容更新描写字段（如果当前为空）
            ex = existing[0]
            updated = False
            if not ex.get("sensory_entry") and sensory.get("entry"):
                entry_text = sensory["entry"]
                if isinstance(entry_text, list):
                    entry_text = "、".join(entry_text)
                if len(entry_text) > 10:
                    ex["sensory_entry"] = entry_text
                    updated = True
            if not ex.get("sensory_rhythm") and sensory.get("rhythm"):
                rhythm_text = sensory["rhythm"]
                if isinstance(rhythm_text, list):
                    rhythm_text = "、".join(rhythm_text)
                if len(rhythm_text) > 10:
                    ex["sensory_rhythm"] = rhythm_text
                    updated = True
            if not ex.get("sensory_climax") and sensory.get("climax"):
                climax_text = sensory["climax"]
                if isinstance(climax_text, list):
                    climax_text = "、".join(climax_text)
                if len(climax_text) > 10:
                    ex["sensory_climax"] = climax_text
                    updated = True
            if updated:
                changes += 1
                logger.info(f"  ~ 更新体位描写: {pos_name}")
        elif has_sensory_content:
            # 有足够内容才创建新体位
            entry_text = sensory.get("entry", "")
            rhythm_text = sensory.get("rhythm", "")
            climax_text = sensory.get("climax", "")
            if isinstance(entry_text, list):
                entry_text = "、".join(entry_text)
            if isinstance(rhythm_text, list):
                rhythm_text = "、".join(rhythm_text)
            if isinstance(climax_text, list):
                climax_text = "、".join(climax_text)

            templates["positions"].append({
                "id": pos_name[:20].replace(" ", "_"),
                "name": pos_name,
                "description": f"来自{source_url}",
                "tags": [extracted.get("mood", "")],
                "sensory_entry": entry_text,
                "sensory_rhythm": rhythm_text,
                "sensory_climax": climax_text,
                "variations": [],
                "suitable_contexts": [extracted.get("mood", "")]
            })
            changes += 1
            logger.info(f"  + 新增体位: {pos_name}")

    # 合并对话
    dialogue_lines = extracted.get("dialogue_lines", [])
    if dialogue_lines:
        # 分到"顺从方"（大部分小说中女性说的）/ "主导方"
        dom_count = 0
        sub_count = 0
        for line in dialogue_lines:
            line = line.strip()
            if not line or len(line) < 3:
                continue
            # 去重
            if line in templates["dialogue"]["dominant_male"] or line in templates["dialogue"]["submissive_female"]:
                continue
            # 简单区分配对到主导方/顺从方
            if any(kw in line for kw in ["轻", "别", "不要", "不行", "受不了", "去了", "嗯", "啊", "哈"]):
                # 再细看长度和语气 —— 短的、拒绝倾向的算顺从方
                if len(line) < 10:
                    if line not in templates["dialogue"]["submissive_female"]:
                        templates["dialogue"]["submissive_female"].append(line)
                        sub_count += 1
                else:
                    if line not in templates["dialogue"]["dominant_male"]:
                        templates["dialogue"]["dominant_male"].append(line)
                        dom_count += 1
            else:
                if line not in templates["dialogue"]["dominant_male"]:
                    templates["dialogue"]["dominant_male"].append(line)
                    dom_count += 1
        changes += dom_count + sub_count
        if dom_count:
            logger.info(f"  + 新增主导对话 x{dom_count}")
        if sub_count:
            logger.info(f"  + 新增顺从对话 x{sub_count}")

    # 合并感官描写
    sensory = extracted.get("sensory_highlights", {})
    bundle_map = {
        "entry": "entry", "wet": "wet", "rhythm": "tight",
        "climax": "climax_female", "aftermath": "aftermath",
    }
    for src_key, dst_key in bundle_map.items():
        lines = sensory.get(src_key, [])
        if isinstance(lines, str):
            lines = [lines]
        for line in lines:
            line = line.strip()
            if line and line not in templates["sensory_bundles"].get(dst_key, []):
                if len(line) > 10:  # 忽略过短的句子
                    templates["sensory_bundles"].setdefault(dst_key, []).append(line)
                    changes += 1
                    logger.info(f"  + 新增感官描写 [{dst_key}]: {line[:40]}...")

    # 合并女性状态描写
    female_state = extracted.get("female_state", {})
    if female_state and "female_states" not in templates:
        templates["female_states"] = {
            "mental": [], "expression": [], "speech_style": [], "body_reaction": []
        }
    if female_state and isinstance(female_state, dict):
        for key in ["mental", "expression", "speech_style", "body_reaction"]:
            val = female_state.get(key, "")
            if isinstance(val, str) and val.strip() and len(val) > 3:
                if val not in templates.get("female_states", {}).get(key, []):
                    templates.setdefault("female_states", {}).setdefault(key, []).append(val)
                    changes += 1
                    logger.info(f"  + 新增女性状态 [{key}]: {val[:40]}")

    # 合并外貌描写
    appearance = extracted.get("appearance_highlights", {})
    if appearance and isinstance(appearance, dict) and any(v for v in appearance.values()):
        if "appearance_highlights" not in templates:
            templates["appearance_highlights"] = {
                "clothing_state": [], "arousal_signs": [], "disheveled": [],
                "body_posture": [], "nude_details": [], "afterglow": []
            }
        for key in ["clothing_state", "arousal_signs", "disheveled", "body_posture", "nude_details", "afterglow"]:
            val = appearance.get(key, "")
            if isinstance(val, str) and val.strip() and len(val) > 5:
                if val not in templates["appearance_highlights"].get(key, []):
                    templates["appearance_highlights"].setdefault(key, []).append(val)
                    changes += 1
                    logger.info(f"  + 新增成人外貌 [{key}]: {val[:40]}")

    # 如果检测到前戏手法，记录下来
    foreplay = extracted.get("foreplay_techniques", [])
    for fp in foreplay:
        fp = fp.strip()
        if not fp:
            continue
        existing_fp = [f for f in templates["foreplay_actions"] if f["name"] == fp]
        if not existing_fp:
            templates["foreplay_actions"].append({
                "id": fp[:20].replace(" ", "_"),
                "name": fp,
                "details": f"来自{source_url}",
                "tags": [extracted.get("mood", "")]
            })
            changes += 1
            logger.info(f"  + 新增前戏手法: {fp}")

    # 写入文件
    if changes > 0:
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)
        logger.info(f"✓ 写入完成: {changes} 条新内容 → {target_path.name}")
    else:
        logger.info("没有新内容需要添加")

    # NTR分流：独立写入ntr_templates.json
    if extracted.get("has_ntr"):
        merge_ntr_data(extracted, source_url)

    return changes > 0


NTR_PATH = _PROJECT_ROOT / "backend" / "ane" / "content" / "ntr_templates.json"


def merge_ntr_data(extracted: dict, source_url: str = ""):
    """将NTR相关的结构化数据合并到ntr_templates.json"""
    ntr_type = extracted.get("ntr_type", "")
    scene_type = extracted.get("ntr_scene_type", "")
    psych_beats = extracted.get("ntr_psychological_beats", [])
    dialogue_lines = extracted.get("dialogue_lines", [])
    female_state = extracted.get("female_state", {}) or {}
    appearance = extracted.get("appearance_highlights", {}) or {}
    sensory = extracted.get("sensory_highlights", {}) or {}

    # 读取或初始化ntr_templates.json
    if NTR_PATH.exists():
        with open(NTR_PATH, "r", encoding="utf-8") as f:
            ntr_data = json.load(f)
    else:
        ntr_data = {
            "relationship_dynamics": [],
            "psychological_arcs": [],
            "ntr_scene_templates": [],
            "humiliation_contrasts": {"size_comparison": [], "skill_comparison": [],
                                      "ownership_declaration": [], "boundary_violation": [],
                                      "aftercare_humiliation": []},
            "dialogue_examples": {"resistance_phase": [], "ambivalence_phase": [],
                                  "surrender_phase": [], "dominant_lines": [],
                                  "submissive_lines": [], "betrayed_lines": []},
            "sensory_highlights": {"guilt_pleasure": [], "danger_arousal": [],
                                    "comparison_shame": [], "aftermath_mixed": []},
            "female_states_ntr": {"mental": [], "expression": [], "speech_style": [], "body_reaction": []},
            "appearance_highlights_ntr": {"clothing_state": [], "arousal_signs": [],
                                           "disheveled": [], "afterglow": []},
        }

    changes = 0

    # 对话按阶段分类
    for line in dialogue_lines:
        line = line.strip()
        if not line or len(line) < 3:
            continue
        # 去重检查所有对话列表
        all_dialogue = []
        for key in ntr_data["dialogue_examples"]:
            all_dialogue.extend(ntr_data["dialogue_examples"][key])
        if line in all_dialogue:
            continue
        # 根据内容判断阶段
        if any(kw in line for kw in ["求", "别", "不要", "放过", "不能这样", "不对"]):
            target = "resistance_phase"
        elif any(kw in line for kw in ["不要停", "给我", "我还要", "操我", "插我", "干我", "你是我的"]):
            target = "surrender_phase"
        elif any(kw in line for kw in ["男朋友", "老公", "妻子", "他", "被发现", "你男人"]):
            target = "ambivalence_phase"
        elif any(kw in line for kw in ["你是谁", "她是谁", "贱人", "婊子", "耍我", "被骗"]):
            target = "betrayed_lines"
        elif any(kw in line for kw in ["知道", "不知", "小声", "叫大声", "让你", "离不开"]):
            target = "dominant_lines"
        else:
            target = "submissive_lines"
        ntr_data["dialogue_examples"].setdefault(target, []).append(line)
        changes += 1

    # 心理变化描写
    valid_beats = [b.strip() for b in psych_beats if isinstance(b, str) and len(b.strip()) > 15]
    existing_beats = []
    for key in ntr_data["sensory_highlights"]:
        existing_beats.extend(ntr_data["sensory_highlights"][key])
    for beat in valid_beats:
        if beat not in existing_beats:
            # 按内容分配到具体子类
            if any(kw in beat for kw in ["罪恶", "愧疚", "羞耻", "不该", "不对"]):
                ntr_data["sensory_highlights"]["guilt_pleasure"].append(beat)
            elif any(kw in beat for kw in ["发现", "危险", "紧张", "随时", "声音"]):
                ntr_data["sensory_highlights"]["danger_arousal"].append(beat)
            elif any(kw in beat for kw in ["比", "不如", "更", "不同", "他"]):
                ntr_data["sensory_highlights"]["comparison_shame"].append(beat)
            else:
                ntr_data["sensory_highlights"]["aftermath_mixed"].append(beat)
            changes += 1

    # 女性状态
    if female_state and isinstance(female_state, dict):
        for key in ["mental", "expression", "speech_style", "body_reaction"]:
            val = female_state.get(key, "")
            if isinstance(val, str) and val.strip() and len(val) > 3:
                ntr_key = f"female_states_ntr"
                if val not in ntr_data.get(ntr_key, {}).get(key, []):
                    ntr_data.setdefault(ntr_key, {}).setdefault(key, []).append(val)
                    changes += 1

    # 外貌描写
    if appearance and isinstance(appearance, dict):
        for key in ["clothing_state", "arousal_signs", "disheveled", "afterglow"]:
            val = appearance.get(key, "")
            if isinstance(val, str) and val.strip() and len(val) > 5:
                ntr_key = "appearance_highlights_ntr"
                if val not in ntr_data.get(ntr_key, {}).get(key, []):
                    ntr_data.setdefault(ntr_key, {}).setdefault(key, []).append(val)
                    changes += 1

    if changes > 0:
        with open(NTR_PATH, "w", encoding="utf-8") as f:
            json.dump(ntr_data, f, ensure_ascii=False, indent=2)
        logger.info(f"✓ NTR数据写入完成: {changes} 条 → {NTR_PATH.name}")
    else:
        logger.info("没有新的NTR内容需要添加")


# ── 主体流程 ────────────────────────────────────────────────

async def process_url(url: str, api_key: str, base_url: str, direct: bool = False) -> bool:
    """处理单个URL的完整流程"""
    print(f"\n{'='*60}")
    print(f"处理: {url}")
    print(f"{'='*60}")

    try:
        # 第1步：抓取
        html = await fetch_page(url, direct=direct)
        logger.info(f"HTML大小: {len(html)} bytes")
    except Exception as e:
        logger.error(f"抓取失败: {e}")
        return False

    # 第2步：提取正文
    text = extract_content(html, url)
    logger.info(f"正文长度: {len(text)} 字")

    if len(text) < 100:
        logger.warning("正文太短，可能提取失败")
        # 尝试直接输出前500字符看看
        preview = text[:500]
        logger.debug(f"正文预览: {preview[:200]}")

    # 第3步：LLM提取
    logger.info("正在调用DeepSeek分析...")
    extracted = await llm_extract(text, api_key, base_url)

    extracted_nsfw = extracted.get("has_nsfw", False)
    if not extracted_nsfw:
        logger.info("未检测到NSFW内容，继续提取世界观...")
    else:
        logger.info(f"检测到体位: {extracted.get('positions_used', [])}")
        logger.info(f"检测到对话: {len(extracted.get('dialogue_lines', []))} 条")
        logger.info(f"氛围: {extracted.get('mood', 'N/A')}")

        # 第4步：合并写入NSFW（有才写）
        merge_to_templates(extracted, source_url=url)

    # 第5步：世界观提取（每次都跑，不依赖NSFW检测结果）
    # 注意：NSFW小说正文主要是性爱描写，世界观信息稀疏
    # 如果需要高质量世界观数据，建议单独跑 world_harvest.py 爬设定文
    try:
        from ane.tools.world_harvest import llm_extract as world_extract, merge_to_templates as world_merge
        logger.info("正在提取世界观数据...")
        world_data = await world_extract(text, api_key, base_url)
        if world_data.get("has_world_content"):
            world_merge(world_data, source_url=url)
            logger.info("世界观数据提取完成")
        else:
            logger.info("未检测到世界观内容")
    except Exception as e:
        logger.warning(f"世界观提取失败（不影响主流程）: {e}")

    return extracted_nsfw


async def search_mode(search_url: str, api_key: str, base_url: str, direct: bool = False):
    """搜索结果页模式：列出链接让用户选（自动翻页）"""
    print(f"\n搜索页: {search_url}")

    if direct:
        # 直连模式不用翻页（代理关闭）
        html = await fetch_page(search_url, direct=True)
        links = extract_links(html, search_url)
        articles = _filter_articles(links)
    else:
        # 自动翻页收集所有文章
        articles = extract_all_pages(search_url, max_pages=10, direct=False)

    if not articles:
        logger.warning("未找到有效文章链接")
        return

    print(f"\n找到 {len(articles)} 篇文章:")
    for i, link in enumerate(articles, 1):
        print(f"  [{i}] {link['title'][:70]}")
    print(f"  [0] 全部处理")
    print(f"  [q] 退出")
    print(f"  [回车] 换网址")

    choice = input("\n选择: ").strip()
    if choice.lower() == "q":
        return
    elif choice == "":
        print("\nD:\\ANE>python -m ane.tools.nsfw_harvest --search ")
        return
    elif choice == "0":
        for link in articles:
            await process_url(link["url"], api_key, base_url, direct=direct)
            print()
    else:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(articles):
                await process_url(articles[idx]["url"], api_key, base_url, direct=direct)
        except (ValueError, IndexError):
            logger.error("无效选择")


def _filter_articles(links: list[dict], max_count: int = 100) -> list[dict]:
    """通用文章链接过滤"""
    import re
    return [
        l for l in links
        if len(re.sub(r'[\s\d\W]', '', l["title"])) >= 4
        and not any(kw in l["url"] for kw in ["/search/", "/label/", "archive.html"])
        and not l["url"].rstrip("/").endswith(".com/")
        and not l["url"].rstrip("/").endswith(".net/")
    ][:max_count]


async def batch_mode(urls: list[str], api_key: str, base_url: str, direct: bool = False):
    """批量处理模式"""
    success = 0
    total = len(urls)
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{total}]")
        if await process_url(url, api_key, base_url, direct=direct):
            success += 1
    print(f"\n完成: {success}/{total} 成功")


async def interactive_mode(api_key: str, base_url: str, direct: bool = False):
    """交互式模式：手动粘贴内容"""
    print("粘贴小说片段（支持多行），输入空行 + @@end 结束")
    print("或直接输入URL")
    lines = []
    while True:
        line = input()
        if line == "@@end":
            break
        if not line and lines and lines[-1] == "":
            break
        lines.append(line)

    text = "\n".join(lines).strip()
    if not text:
        return

    # 判断是URL还是纯文本
    if text.startswith("http://") or text.startswith("https://"):
        await process_url(text, api_key, base_url, direct=direct)
    else:
        # 直接发给LLM提取
        extracted = await llm_extract(text, api_key, base_url)
        if extracted.get("has_nsfw"):
            print(f"体位: {extracted.get('positions_used', [])}")
            print(f"对话: {len(extracted.get('dialogue_lines', []))} 条")
            print(f"氛围: {extracted.get('mood', '')}")
            print(f"情感线: {extracted.get('emotional_arc', '')}")
            merge_to_templates(extracted, source_url="(用户粘贴)")
        else:
            print("未检测到NSFW内容")


# ── 启动入口 ────────────────────────────────────────────────

def main():
    api_key, base_url = _load_config()
    if not api_key:
        print("错误: DEEPSEEK_API_KEY 未配置。请检查 .env 文件。")
        sys.exit(1)

    args = sys.argv[1:]
    direct = "--direct" in args
    if direct:
        args.remove("--direct")

    if not args:
        # 无参数 → 交互模式
        asyncio.run(interactive_mode(api_key, base_url, direct=direct))
    elif args[0] == "--search" and len(args) >= 2:
        asyncio.run(search_mode(args[1], api_key, base_url, direct=direct))
    elif args[0] == "--batch":
        asyncio.run(batch_mode(args[1:], api_key, base_url, direct=direct))
    elif args[0].startswith("http"):
        asyncio.run(process_url(args[0], api_key, base_url, direct=direct))
    else:
        print(f"用法:")
        print(f"  {sys.argv[0]} <URL>                     # 处理单个URL")
        print(f"  {sys.argv[0]} --search <搜索页URL>       # 搜索模式")
        print(f"  {sys.argv[0]} --batch <URL> <URL> ...    # 批量处理")
        print(f"  {sys.argv[0]}                          # 交互式粘贴")
        print(f"  {sys.argv[0]} --direct <模式> <URL>      # 绕过代理直接连接")
        sys.exit(1)


if __name__ == "__main__":
    main()
