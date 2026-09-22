"""前端契约测试：保证页面与后端接口、离线可用性不脱节。"""

from __future__ import annotations

import re

from app.config import PROJECT_ROOT

FRONTEND = PROJECT_ROOT / "frontend"


def _html() -> str:
    return (FRONTEND / "index.html").read_text(encoding="utf-8")


def _js() -> str:
    return (FRONTEND / "app.js").read_text(encoding="utf-8")


def _css() -> str:
    return (FRONTEND / "style.css").read_text(encoding="utf-8")


def test_frontend_files_exist():
    for name in ("index.html", "app.js", "style.css"):
        assert (FRONTEND / name).exists(), f"缺少前端文件 {name}"


def test_navigation_views_are_wired():
    """原来四个导航是死链（没有 href 也没有 JS 绑定），这里断言每个导航都有对应视图。"""
    html = _html()
    views = re.findall(r'data-view="([a-z]+)"', html)
    assert views, "导航项缺少 data-view 属性"
    for view in views:
        assert f'id="view-{view}"' in html, f"导航 {view} 没有对应的视图容器"
    for view in views:
        assert f"'{view}'" in _js(), f"app.js 没有处理视图 {view}"


def test_frontend_uses_relative_api_path():
    """前端由 FastAPI 同源托管，不应再硬编码 127.0.0.1:8000。"""
    assert "const API = '/api'" in _js()
    assert "127.0.0.1:8000" not in _js()
    assert "127.0.0.1:8000" not in _html()


def test_frontend_has_no_external_assets():
    """离线演示不能依赖外网字体/CDN。"""
    for name, content in (("index.html", _html()), ("style.css", _css()), ("app.js", _js())):
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"', content) + re.findall(
            r'@import\s+url\(["\']?(https?://[^"\')]+)', content
        )
        assert not external, f"{name} 引用了外部资源：{external}"


def test_frontend_references_real_endpoints(client):
    """页面里出现的 /api 路径必须真实存在。"""
    # 先把 ${...} 模板表达式归一化，避免把 JS 表达式当成路径片段
    normalized_js = re.sub(r"\$\{[^}]*\}", "{id}", _js())
    paths = set(re.findall(r"['\"`](/api/[a-zA-Z0-9/_{}.-]+)", normalized_js))
    assert paths, "app.js 没有调用任何接口"

    known = client.get("/openapi.json").json()["paths"]
    for path in paths:
        candidate_pattern = re.sub(r"\{[^}]+\}", "[^/]+", path).rstrip("/") + "/?$"
        matched = any(re.fullmatch(candidate_pattern, route) for route in known)
        assert matched, f"前端调用了不存在的接口：{path}"


def test_all_referenced_element_ids_exist():
    """app.js 里 $('xxx') 引用的 id 必须都在 index.html 中定义。

    这是最容易出错、又最难在浏览器里第一时间发现的一类问题（拼写错误只会静默失效）。
    """
    html_ids = set(re.findall(r'id="([^"]+)"', _html()))
    js_ids = set(re.findall(r"\$\('([^']+)'\)", _js()))
    assert js_ids, "app.js 没有通过 $() 取任何元素"
    missing = sorted(js_ids - html_ids)
    assert not missing, f"app.js 引用了 index.html 中不存在的元素 id：{missing}"


def test_static_assets_are_served(client):
    """FastAPI 需要在根路径托管静态资源，页面里的 ./style.css 才能解析。"""
    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert "个人理财助手" in index.text

    for asset in ("/app.js", "/style.css"):
        response = client.get(asset)
        assert response.status_code == 200, f"{asset} 无法访问"
