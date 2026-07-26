#!/usr/bin/env python3
"""人物外貌收割工具 GUI — 极简版，双击运行"""

import asyncio
import json
import os
import sys
import threading
import traceback
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_PROJ / "backend"))

import tkinter as tk
from tkinter import scrolledtext, messagebox

from ane.tools.portrait_harvest import fetch_page, extract_content, llm_extract, merge_to_templates
from ane.tools.nsfw_harvest import extract_all_pages

API_KEY = ""
API_URL = ""
_PORTRAIT_PATH = _PROJ / "backend" / "ane" / "content" / "portrait_templates.json"


def load_config():
    global API_KEY, API_URL
    try:
        from ane.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
        API_KEY = DEEPSEEK_API_KEY
        API_URL = DEEPSEEK_BASE_URL
    except Exception:
        pass


class PortraitGUI:
    def __init__(self):
        self.win = tk.Tk()
        self.win.title("人物外貌收割工具")
        self.win.geometry("700x550")
        self.win.resizable(True, True)
        self.win.attributes("-topmost", True)

        bg = "#2b2b2b"
        fg = "#d4d4d4"
        btn_bg = "#3c3c3c"
        entry_bg = "#1e1e1e"
        self.win.configure(bg=bg)

        title = tk.Label(self.win, text="人物外貌收割工具",
                         bg=bg, fg="#c9a96e",
                         font=("Microsoft YaHei", 14, "bold"), pady=8)
        title.pack(fill="x")

        mode_frame = tk.Frame(self.win, bg=bg)
        mode_frame.pack(fill="x", padx=10, pady=(0, 5))
        tk.Label(mode_frame, text="模式:", bg=bg, fg=fg,
                 font=("Microsoft YaHei", 10)).pack(side="left", padx=(0, 8))
        self.mode_var = tk.StringVar(value="single")
        tk.Radiobutton(mode_frame, text="单个链接", variable=self.mode_var,
                       value="single", bg=bg, fg=fg, selectcolor="#3c3c3c",
                       font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
        tk.Radiobutton(mode_frame, text="搜索列表", variable=self.mode_var,
                       value="search", bg=bg, fg=fg, selectcolor="#3c3c3c",
                       font=("Microsoft YaHei", 9)).pack(side="left", padx=2)
        self.direct_var = tk.BooleanVar(value=False)
        tk.Checkbutton(mode_frame, text="直连（不开代理）",
                       variable=self.direct_var,
                       bg=bg, fg=fg, selectcolor="#3c3c3c",
                       font=("Microsoft YaHei", 9)).pack(side="right")

        url_frame = tk.Frame(self.win, bg=bg)
        url_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(url_frame, text="网址:", bg=bg, fg=fg,
                 font=("Microsoft YaHei", 10)).pack(anchor="w")
        self.url_entry = tk.Entry(url_frame, bg=entry_bg, fg=fg,
                                  insertbackground=fg, relief="flat",
                                  font=("Microsoft YaHei", 10), bd=3)
        self.url_entry.pack(fill="x", ipady=5, pady=(3, 0))
        self.url_entry.bind("<Return>", lambda e: self.start_harvest())

        self.select_frame = tk.Frame(self.win, bg=bg)
        self.select_frame.pack(fill="x", padx=10, pady=(0, 5))
        self.select_frame.pack_forget()
        tk.Label(self.select_frame, text="选择文章编号:", bg=bg, fg=fg,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 5))
        self.select_entry = tk.Entry(self.select_frame, bg=entry_bg, fg=fg,
                                     insertbackground=fg, relief="flat",
                                     font=("Microsoft YaHei", 10), bd=3, width=8)
        self.select_entry.pack(side="left", ipady=3, padx=(0, 5))
        self.select_btn = tk.Button(
            self.select_frame, text="收割选中", command=self._on_select_article,
            bg="#c9a96e", fg="#1a1410", activebackground="#d4b87a",
            font=("Microsoft YaHei", 9, "bold"), relief="flat",
            padx=10, pady=2, cursor="hand2")
        self.select_btn.pack(side="left", padx=2)
        self.select_entry.bind("<Return>", lambda e: self._on_select_article())

        btn_frame = tk.Frame(self.win, bg=bg)
        btn_frame.pack(fill="x", padx=10, pady=8)
        self.btn_frame = btn_frame
        self.go_btn = tk.Button(
            btn_frame, text="开始收割", command=self.start_harvest,
            bg="#c9a96e", fg="#1a1410", activebackground="#d4b87a",
            font=("Microsoft YaHei", 10, "bold"), relief="flat",
            padx=20, pady=4, cursor="hand2")
        self.go_btn.pack(side="left", padx=(0, 8))
        self.paste_btn = tk.Button(
            btn_frame, text="粘贴", command=self.paste_url,
            bg="#3c3c3c", fg=fg, activebackground="#4c4c4c",
            font=("Microsoft YaHei", 9), relief="flat", padx=10, cursor="hand2")
        self.paste_btn.pack(side="left", padx=2)
        self.clear_btn = tk.Button(
            btn_frame, text="清空", command=self.clear_all,
            bg="#3c3c3c", fg=fg, activebackground="#4c4c4c",
            font=("Microsoft YaHei", 9), relief="flat", padx=10, cursor="hand2")
        self.clear_btn.pack(side="left", padx=2)
        self.status_label = tk.Label(btn_frame, text="就绪",
                                     bg=bg, fg="#888",
                                     font=("Microsoft YaHei", 9))
        self.status_label.pack(side="right")

        result_frame = tk.Frame(self.win, bg=bg)
        result_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.result_text = scrolledtext.ScrolledText(
            result_frame, bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="#d4d4d4", relief="flat",
            font=("Consolas", 9), wrap="word", state="disabled", bd=0)
        self.result_text.pack(fill="both", expand=True)

        self.win.bind("<Control-v>", lambda e: self.paste_url())
        self.win.after(500, lambda: self.win.attributes("-topmost", False))
        self._search_articles = None
        self._search_direct = None

    def paste_url(self):
        try:
            text = self.win.clipboard_get()
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, text.strip())
        except Exception:
            pass

    def clear_all(self):
        self.url_entry.delete(0, tk.END)
        self.set_result("")
        self.set_status("就绪", "#888")

    def set_status(self, text, color="#888"):
        self.status_label.config(text=text, fg=color)
        self.win.update()

    def set_result(self, text):
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", text)
        self.result_text.config(state="disabled")
        self.win.update()

    def append_result(self, text):
        self.result_text.config(state="normal")
        self.result_text.insert(tk.END, text)
        self.result_text.see(tk.END)
        self.result_text.config(state="disabled")
        self.win.update()

    def start_harvest(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("提示", "请输入网址")
            return
        if not API_KEY:
            messagebox.showerror("错误", "未配置 DEEPSEEK_API_KEY")
            return
        self.go_btn.config(state="disabled", text="收集中...")
        self.set_status("正在处理...", "#c9a96e")
        self.set_result("")
        t = threading.Thread(target=self._run_task, args=(url,), daemon=True)
        t.start()

    def _run_task(self, url):
        try:
            asyncio.run(self._async_harvest(url))
        except Exception as e:
            self.win.after(0, self._on_error, str(e))
        finally:
            self.win.after(0, self._on_done)

    async def _async_harvest(self, url):
        direct = self.direct_var.get()
        mode = self.mode_var.get()
        if mode == "search":
            await self._search_mode(url, direct)
        else:
            await self._process_url(url, direct)

    async def _process_url(self, url, direct):
        self.append_result(f"抓取: {url}\n")
        html = await fetch_page(url, direct=direct)
        self.append_result(f"HTML大小: {len(html)} bytes\n")
        text = extract_content(html, url)
        self.append_result(f"正文长度: {len(text)} 字\n")
        if len(text) < 100:
            self.append_result("⚠️ 正文太短\n")
            return
        lines_preview = text[:200].replace("\n", " ")
        self.append_result(f"预览: {lines_preview}...\n\n")
        self.append_result("DeepSeek 分析中...\n")
        extracted = await llm_extract(text, API_KEY, API_URL)
        if not extracted.get("has_portrait"):
            self.append_result("未检测到人物外貌内容\n")
            return
        fp = extracted.get("extracted_female_portraits", [])
        mp = extracted.get("extracted_male_portraits", [])
        self.append_result(f"女性外貌: {len(fp)} 个\n")
        self.append_result(f"男性外貌: {len(mp)} 个\n")
        ok = merge_to_templates(extracted, source_url=url)
        if ok:
            d = json.loads(_PORTRAIT_PATH.read_text(encoding="utf-8"))
            total = sum(len(d.get(k, [])) for k in ["clothing", "figure", "face", "eyes", "hair", "aura", "full_examples_female", "full_examples_male"])
            self.append_result(f"\n✅ 写入成功 → portrait_templates.json\n")
            self.append_result(f"当前外貌库: 总计{total}条\n")
        else:
            self.append_result("无新内容需要添加\n")

    async def _search_mode(self, url, direct):
        self.append_result(f"搜索页: {url}\n\n")
        self.append_result("正在翻页收集文章（最多10页）...\n")
        import asyncio as _a, concurrent.futures as _cf
        loop = _a.get_event_loop()
        with _cf.ThreadPoolExecutor() as pool:
            articles = await loop.run_in_executor(pool, extract_all_pages, url, 10, direct)
        if not articles:
            self.append_result("未找到文章链接\n")
            return
        self.append_result(f"找到 {len(articles)} 篇文章:\n")
        for i, a in enumerate(articles, 1):
            self.append_result(f"  [{i}] {a['title'][:60]}\n")
        self.append_result(f"\n输入编号选择文章（如 1 3 5 或 1-5），输入 0 全量处理:\n")
        self._search_articles = articles
        self._search_direct = direct
        self.select_frame.pack(before=self.btn_frame, fill="x", padx=10, pady=(0, 5))
        self.select_entry.delete(0, tk.END)
        self.select_entry.focus()
        self.set_status("请选择文章编号", "#c9a96e")
        self.go_btn.config(state="normal", text="开始收割")

    def _on_select_article(self):
        raw = self.select_entry.get().strip()
        if not raw:
            return
        self.select_frame.pack_forget()
        self._search_articles_go(raw)

    def _search_articles_go(self, raw):
        articles = self._search_articles
        if not articles:
            return
        indices = set()
        for part in raw.replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    a, b = int(a.strip()), int(b.strip())
                    indices.update(range(a, b + 1))
                except ValueError:
                    continue
            else:
                try:
                    indices.add(int(part))
                except ValueError:
                    continue
        if 0 in indices:
            selected = articles
        else:
            selected = [articles[i - 1] for idx in sorted(indices) if 1 <= idx <= len(articles)]
        if not selected:
            self.set_status("无效编号", "#e06c75")
            self.select_frame.pack(before=self.btn_frame, fill="x", padx=10, pady=(0, 5))
            return
        self.go_btn.config(state="disabled", text="收集中...")
        t = threading.Thread(target=self._run_batch, args=(selected, self._search_direct), daemon=True)
        t.start()

    def _run_batch(self, articles, direct):
        try:
            asyncio.run(self._async_batch(articles, direct))
        except Exception as e:
            self.win.after(0, self._on_error, str(e))
        finally:
            self.win.after(0, self._on_done)

    async def _async_batch(self, articles, direct):
        total = len(articles)
        for i, a in enumerate(articles, 1):
            self.append_result(f"\n[{i}/{total}] {a['title'][:40]}...\n")
            await self._process_url(a["url"], direct)
        self.append_result(f"\n✅ 全部处理完成!\n")

    def _on_error(self, msg):
        self.set_result(f"❌ 错误:\n{msg}\n\n{traceback.format_exc()}")
        self.set_status("失败", "#e06c75")

    def _on_done(self):
        self.go_btn.config(state="normal", text="开始收割")
        if "失败" not in self.status_label.cget("text"):
            self.set_status("完成", "#98c379")

    def run(self):
        self.win.mainloop()


if __name__ == "__main__":
    load_config()
    gui = PortraitGUI()
    gui.run()
