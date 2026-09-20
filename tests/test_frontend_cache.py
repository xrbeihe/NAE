"""前端资源缓存策略测试。

背景：StaticFiles 不带 Cache-Control 时浏览器会按启发式缓存直接用旧副本，导致
「改了 app.html/theme.css 但页面没变」（开发期反复踩到）。所以 HTML/CSS/JS/SVG
一律 `no-cache, must-revalidate`（仍靠 ETag 走 304），图片保持各自的长缓存。

Run: python -m pytest tests/test_frontend_cache.py -q
"""

import httpx
import pytest

from ane.main import app

NO_CACHE = "no-cache, must-revalidate"


@pytest.mark.asyncio
async def test_html_revalidates():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/app.html")
        assert r.status_code == 200
        assert r.headers.get("cache-control") == NO_CACHE


@pytest.mark.asyncio
async def test_css_and_js_revalidate():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        for path in ["/public/theme.css", "/public/common.js"]:
            r = await c.get(path)
            assert r.status_code == 200, path
            assert r.headers.get("cache-control") == NO_CACHE, path


@pytest.mark.asyncio
async def test_pack_background_image_keeps_long_cache():
    """包内背景图不受影响：仍是 assets 路由给的长缓存（1 天）。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/worldviews/naruto_shippuden/asset/chat_bg.jpg")
        assert r.status_code == 200
        assert r.headers.get("cache-control") == "public, max-age=86400"
