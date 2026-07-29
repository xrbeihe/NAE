#!/usr/bin/env python3
"""人物外貌收割工具 — 从网页抓取小说/设定文 → DeepSeek提取外貌描写 → 写入portrait_templates.json

用法:
  python -m ane.tools.portrait_harvest <URL>                      # 从单个URL提取
  python -m ane.tools.portrait_harvest --search <搜索结果页URL>    # 列出搜索结果，选一个
  python -m ane.tools.portrait_harvest --batch <URL> <URL> ...     # 批量处理
  python -m ane.tools.portrait_harvest                             # 交互式粘贴
"""

import asyncio
import json
import logging
import re
import sys
from pathlib import Path

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("portrait_harvest")

PORTRAIT_PATH = _PROJECT_ROOT / "backend" / "ane" / "content" / "portrait_templates.json"

# ── 沿用 nsfw_harvest 的抓取函数 ──────────────────────────────

def _load_config():
    from ane.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
    return DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL


async def fetch_page(url: str, direct: bool = False) -> str:
    from ane.tools.nsfw_harvest import fetch_page as _fetch
    return await _fetch(url, direct=direct)


def extract_content(html: str, url: str = "") -> str:
    from ane.tools.nsfw_harvest import extract_content as _extract
    return _extract(html, url)


def extract_links(html: str, base_url: str) -> list[dict]:
    from ane.tools.nsfw_harvest import extract_links as _extract_links
    return _extract_links(html, base_url)


def extract_all_pages(url: str, max_pages: int = 5, direct: bool = False) -> list[dict]:
    from ane.tools.nsfw_harvest import extract_all_pages as _all
    return _all(url, max_pages, direct)


def _filter_articles(links: list[dict], max_count: int = 100) -> list[dict]:
    from ane.tools.nsfw_harvest import _filter_articles as _filter
    return _filter(links, max_count)


# ── 外貌提取 Prompt ───────────────────────────────────────────

EXTRACTION_PROMPT = """你是一个小说人物外貌分析器。分析以下小说片段，提取其中的人物外貌描写元素。

如果片段中没有人物外貌描写（纯对话、纯动作、无外貌描述），输出 {"has_portrait": false}。

输出严格JSON格式，不要加任何其他文字：

{
  "has_portrait": true,
  "extracted_female_portraits": [
    {
      "full_example": "完整的女性人物出场描写原文（50-150字，摘录原文一段话，包含衣着、体型、脸型、发型、气质等综合描写，有画面感）",
      "clothing": "衣着服饰描述（5-30字）",
      "figure": "体型身材描述（5-30字）",
      "face": "脸型五官描述（5-30字）",
      "eyes": "眼睛/眼神描述（5-30字）",
      "hair": "发型发饰描述（5-30字）",
      "aura": "气质气场描述（5-30字）"
    }
  ],
  "extracted_male_portraits": [
    {
      "full_example": "完整的男性人物出场描写原文（50-150字，摘录原文一段话，包含衣着、体型、脸型、发型、气质等综合描写，有画面感）",
      "clothing": "衣着服饰描述（5-30字）",
      "figure": "体型身材描述（5-30字）",
      "face": "脸型五官描述（5-30字）",
      "eyes": "眼睛/眼神描述（5-30字）",
      "hair": "发型发饰描述（5-30字）",
      "aura": "气质气场描述（5-30字）"
    }
  ]
}

重要规则：
1. 只提取片段中实际存在的内容，不要编造
2. full_example 必须是原文摘录（可微调连贯性），不是概括
3. 子字段（clothing/figure/face等）从 full_example 中提取或原文中其他句子的相关描写
4. 每个角色单独一个 portrait，不要合并
5. 如果某个子字段没有对应原文描述，留空字符串
6. 确保JSON格式正确，没有尾随逗号
"""


# ── LLM 提取 ──────────────────────────────────────────────────

async def llm_extract(text: str, api_key: str, base_url: str) -> dict:
    """调用 DeepSeek API 提取外貌数据"""
    max_chars = 8000
    if len(text) > max_chars:
        text = text[:max_chars]
        last_para = text.rfind("\n\n")
        if last_para > max_chars // 2:
            text = text[:last_para]

    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "user", "content": EXTRACTION_PROMPT + text}
        ],
        "temperature": 0.3,
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

    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {e}")
            return {"has_portrait": False, "parse_error": str(e)}
    return {"has_portrait": False, "parse_error": "No JSON in response"}


# ── 合并到 portrait_templates.json ─────────────────────────────

def merge_to_templates(extracted: dict, source_url: str = ""):
    """将LLM提取的外貌数据合并到 portrait_templates.json"""
    if not extracted.get("has_portrait"):
        logger.info("未检测到人物外貌内容，跳过")
        return False

    if PORTRAIT_PATH.exists():
        with open(PORTRAIT_PATH, "r", encoding="utf-8") as f:
            templates = json.load(f)
    else:
        templates = {
            "clothing": [], "figure": [], "face": [], "eyes": [],
            "hair": [], "aura": [],
            "full_examples_female": [],
            "full_examples_male": [],
        }

    changes = 0

    def _dedup_add(lst, item):
        """去重添加"""
        if item and item not in lst:
            lst.append(item)
            return True
        return False

    # 处理女性
    for p in extracted.get("extracted_female_portraits", []):
        full = p.get("full_example", "").strip()
        if full:
            if _dedup_add(templates["full_examples_female"], full):
                changes += 1
                logger.info(f"  + 女性完整示例 ({len(full)}字)")
        for field in ["clothing", "figure", "face", "eyes", "hair", "aura"]:
            val = p.get(field, "").strip()
            if val and len(val) > 3 and val not in templates.get(field, []):
                templates.setdefault(field, []).append(val)
                changes += 1
                logger.info(f"  + {field}: {val[:40]}...")

    # 处理男性
    for p in extracted.get("extracted_male_portraits", []):
        full = p.get("full_example", "").strip()
        if full:
            if _dedup_add(templates["full_examples_male"], full):
                changes += 1
                logger.info(f"  + 男性完整示例 ({len(full)}字)")
        for field in ["clothing", "figure", "face", "eyes", "hair", "aura"]:
            val = p.get(field, "").strip()
            if val and len(val) > 3 and val not in templates.get(field, []):
                templates.setdefault(field, []).append(val)
                changes += 1
                logger.info(f"  + {field}: {val[:40]}...")

    if changes > 0:
        with open(PORTRAIT_PATH, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)
        logger.info(f"✓ 写入完成: {changes} 条新内容 → {PORTRAIT_PATH.name}")
    else:
        logger.info("没有新内容需要添加")
    return changes > 0


# ── 主体流程 ──────────────────────────────────────────────────

async def process_url(url: str, api_key: str, base_url: str, direct: bool = False) -> bool:
    print(f"\n{'='*60}")
    print(f"处理: {url}")
    print(f"{'='*60}")

    try:
        html = await fetch_page(url, direct=direct)
        logger.info(f"HTML大小: {len(html)} bytes")
    except Exception as e:
        logger.error(f"抓取失败: {e}")
        return False

    text = extract_content(html, url)
    logger.info(f"正文长度: {len(text)} 字")

    if len(text) < 100:
        logger.warning("正文太短")
        return False

    logger.info("正在调用 DeepSeek 分析人物外貌...")
    extracted = await llm_extract(text, api_key, base_url)

    if not extracted.get("has_portrait"):
        logger.info("未检测到人物外貌内容")
        return False

    f_count = len(extracted.get("extracted_female_portraits", []))
    m_count = len(extracted.get("extracted_male_portraits", []))
    logger.info(f"检测到 {f_count} 个女性外貌, {m_count} 个男性外貌")

    return merge_to_templates(extracted, source_url=url)


async def search_mode(search_url: str, api_key: str, base_url: str, direct: bool = False):
    from ane.tools.nsfw_harvest import extract_all_pages, _filter_articles

    print(f"\n搜索页: {search_url}")

    if direct:
        html = await fetch_page(search_url, direct=True)
        links = extract_links(html, search_url)
        articles = _filter_articles(links)
    else:
        articles = extract_all_pages(search_url, max_pages=10, direct=False)

    if not articles:
        logger.warning("未找到有效文章链接")
        return

    print(f"\n找到 {len(articles)} 篇文章:")
    for i, link in enumerate(articles, 1):
        print(f"  [{i}] {link['title'][:70]}")
    print(f"  [0] 全部处理")
    print(f"  [q] 退出")

    choice = input("\n选择: ").strip()
    if choice.lower() == "q":
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


async def batch_mode(urls: list[str], api_key: str, base_url: str, direct: bool = False):
    success = 0
    total = len(urls)
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{total}]")
        if await process_url(url, api_key, base_url, direct=direct):
            success += 1
    print(f"\n完成: {success}/{total} 成功")


async def interactive_mode(api_key: str, base_url: str, direct: bool = False):
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

    if text.startswith("http://") or text.startswith("https://"):
        await process_url(text, api_key, base_url, direct=direct)
    else:
        extracted = await llm_extract(text, api_key, base_url)
        if extracted.get("has_portrait"):
            fp = extracted.get("extracted_female_portraits", [])
            mp = extracted.get("extracted_male_portraits", [])
            print(f"女性外貌: {len(fp)} 个, 男性外貌: {len(mp)} 个")
            merge_to_templates(extracted, source_url="(用户粘贴)")
        else:
            print("未检测到人物外貌内容")


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
        asyncio.run(interactive_mode(api_key, base_url, direct=direct))
    elif args[0] == "--search" and len(args) >= 2:
        asyncio.run(search_mode(args[1], api_key, base_url, direct=direct))
    elif args[0] == "--batch":
        asyncio.run(batch_mode(args[1:], api_key, base_url, direct=direct))
    elif args[0].startswith("http"):
        asyncio.run(process_url(args[0], api_key, base_url, direct=direct))
    else:
        print(f"用法:")
        print(f"  python -m ane.tools.portrait_harvest <URL>                      # 处理单个URL")
        print(f"  python -m ane.tools.portrait_harvest --search <搜索页URL>       # 搜索模式")
        print(f"  python -m ane.tools.portrait_harvest --batch <URL> <URL> ...    # 批量处理")
        print(f"  python -m ane.tools.portrait_harvest                            # 交互式粘贴")
        print(f"  python -m ane.tools.portrait_harvest --direct <URL>             # 绕过代理直连")
        sys.exit(1)


if __name__ == "__main__":
    main()
