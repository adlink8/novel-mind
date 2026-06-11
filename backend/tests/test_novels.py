"""
小说 API 测试

覆盖范围:
- 空列表查询
- 小说上传（含章节分割验证）
- 小说详情查询
- 小说删除
- 章节列表查询
- 章节详情查询
- 搜索功能
- 非法上传格式
- 404 边界

注意:
- 使用内存 SQLite，每个测试独立数据库
- 上传测试需要构造 UploadFile（使用 io.BytesIO 模拟）
- 上传后清理 uploads 目录的测试文件
"""

import io
import os

import pytest
from fastapi import UploadFile
from httpx import AsyncClient


# ─────────── 查询测试 ───────────


@pytest.mark.asyncio
async def test_novels_list_empty(auth_client: AsyncClient):
    """空数据库下小说列表返回空数组"""
    response = await auth_client.get("/api/novels")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["skip"] == 0
    assert data["limit"] == 20


@pytest.mark.asyncio
async def test_novels_list_pagination(auth_client: AsyncClient):
    """分页参数正确传递"""
    response = await auth_client.get("/api/novels?skip=10&limit=5")
    assert response.status_code == 200
    data = response.json()
    assert data["skip"] == 10
    assert data["limit"] == 5


@pytest.mark.asyncio
async def test_novel_detail_not_found(auth_client: AsyncClient):
    """查询不存在的小说返回 404"""
    response = await auth_client.get("/api/novels/9999")
    assert response.status_code == 404
    assert "不存在" in response.json()["detail"]


@pytest.mark.asyncio
async def test_chapters_list_not_found(auth_client: AsyncClient):
    """查询不存在的小说章节返回 404"""
    response = await auth_client.get("/api/novels/9999/chapters")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_chapter_detail_not_found(auth_client: AsyncClient):
    """查询不存在的章节返回 404"""
    response = await auth_client.get("/api/novels/1/chapters/9999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_novel_not_found(auth_client: AsyncClient):
    """删除不存在的小说返回 404"""
    response = await auth_client.delete("/api/novels/9999")
    assert response.status_code == 404


# ─────────── 上传测试 ───────────


@pytest.mark.asyncio
async def test_upload_novel_with_chapters(auth_client: AsyncClient):
    """上传含章节标记的 TXT，验证章节正确分割"""
    content = (
        "第一章 初入江湖\n\n"
        "这是第一章的内容，讲述了主角的出身和初次踏入江湖的经历。\n\n"
        "第二章 拜师学艺\n\n"
        "这是第二章的内容，主角在深山遇到一位高人，拜其为师。\n\n"
        "第三章 下山历练\n\n"
        "这是第三章的内容，学成之后主角告别师父，踏上江湖之路。\n"
    )
    file = UploadFile(
        filename="test_novel.txt",
        file=io.BytesIO(content.encode("utf-8")),
    )

    response = await auth_client.post(
        "/api/novels/upload",
        files={"file": (file.filename, file.file, "text/plain")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "test_novel"
    assert data["chapter_count"] == 3
    assert data["word_count"] > 0
    assert data["status"] == "ready"
    assert "导入完成" in data["message"]

    # 验证章节列表
    novel_id = data["id"]
    chapters_resp = await auth_client.get(f"/api/novels/{novel_id}/chapters")
    assert chapters_resp.status_code == 200
    chapters = chapters_resp.json()
    assert len(chapters) == 3
    assert chapters[0]["title"] == "第一章 初入江湖"
    assert chapters[1]["title"] == "第二章 拜师学艺"
    assert chapters[2]["title"] == "第三章 下山历练"

    # 清理上传的测试文件
    from app.config import settings

    test_path = os.path.join(settings.upload_dir, "test_novel.txt")
    if os.path.exists(test_path):
        os.remove(test_path)


@pytest.mark.asyncio
async def test_upload_novel_without_chapters(auth_client: AsyncClient):
    """上传不含章节标记的 TXT，应作为单章处理"""
    content = "这是一段没有章节标记的纯文本内容，应该被当作全文处理。"
    response = await auth_client.post(
        "/api/novels/upload",
        files={
            "file": ("plain.txt", io.BytesIO(content.encode("utf-8")), "text/plain")
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapter_count"] == 1
    assert data["status"] == "ready"

    from app.config import settings

    test_path = os.path.join(settings.upload_dir, "plain.txt")
    if os.path.exists(test_path):
        os.remove(test_path)


@pytest.mark.asyncio
async def test_upload_invalid_format(auth_client: AsyncClient):
    """上传非 TXT 文件返回 400"""
    response = await auth_client.post(
        "/api/novels/upload",
        files={"file": ("test.pdf", io.BytesIO(b"fake pdf"), "application/pdf")},
    )
    assert response.status_code == 400
    assert "仅支持 .txt" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_gbk_encoding(auth_client: AsyncClient):
    """上传 GBK 编码文件，验证编码自动检测和转换"""
    content = "第一章 测试\n\n这是 GBK 编码的测试内容。"
    response = await auth_client.post(
        "/api/novels/upload",
        files={
            "file": ("gbk_test.txt", io.BytesIO(content.encode("gbk")), "text/plain")
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapter_count"] == 1
    assert data["status"] == "ready"

    from app.config import settings

    test_path = os.path.join(settings.upload_dir, "gbk_test.txt")
    if os.path.exists(test_path):
        os.remove(test_path)


@pytest.mark.asyncio
async def test_upload_gb18030_encoding(auth_client: AsyncClient):
    """上传 GB18030 编码文件（含生僻字），验证扩展编码支持"""
    content = "第一章 测试\n\n这是 GB18030 编码的测试内容，包含生僻字：龍、鳳。"
    response = await auth_client.post(
        "/api/novels/upload",
        files={
            "file": (
                "gb18030_test.txt",
                io.BytesIO(content.encode("gb18030")),
                "text/plain",
            )
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapter_count"] == 1
    assert data["status"] == "ready"

    from app.config import settings

    test_path = os.path.join(settings.upload_dir, "gb18030_test.txt")
    if os.path.exists(test_path):
        os.remove(test_path)


@pytest.mark.asyncio
async def test_upload_big5_encoding(auth_client: AsyncClient):
    """上传 Big5 编码文件（繁体中文），验证繁体编码支持"""
    content = "第一章 測試\n\n這是 Big5 編碼的繁體中文測試內容。"
    response = await auth_client.post(
        "/api/novels/upload",
        files={
            "file": ("big5_test.txt", io.BytesIO(content.encode("big5")), "text/plain")
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["chapter_count"] == 1
    assert data["status"] == "ready"

    from app.config import settings

    test_path = os.path.join(settings.upload_dir, "big5_test.txt")
    if os.path.exists(test_path):
        os.remove(test_path)


@pytest.mark.asyncio
async def test_upload_import_status(auth_client: AsyncClient):
    """验证导入状态跟踪 API"""
    content = "第一章 测试\n\n内容。"
    response = await auth_client.post(
        "/api/novels/upload",
        files={
            "file": (
                "status_test.txt",
                io.BytesIO(content.encode("utf-8")),
                "text/plain",
            )
        },
    )

    assert response.status_code == 200
    novel_id = response.json()["id"]

    # 查询导入状态
    status_resp = await auth_client.get(f"/api/novels/{novel_id}/import-status")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["novel_id"] == novel_id
    assert status["stage"] == "ready"
    assert status["percent"] == 100
    assert "导入完成" in status["message"]

    from app.config import settings

    test_path = os.path.join(settings.upload_dir, "status_test.txt")
    if os.path.exists(test_path):
        os.remove(test_path)


@pytest.mark.asyncio
async def test_upload_import_status_not_found(auth_client: AsyncClient):
    """查询不存在小说的导入状态返回 404"""
    resp = await auth_client.get("/api/novels/9999/import-status")
    assert resp.status_code == 404


# ─────────── CRUD 完整流程 ───────────


@pytest.mark.asyncio
async def test_novel_full_lifecycle(auth_client: AsyncClient):
    """完整生命周期：上传 → 查询详情 → 查询章节 → 删除 → 再次查询 404"""
    content = "第一章 测试\n\n测试内容。\n\n第二章 结束\n\n结束了。"
    upload_resp = await auth_client.post(
        "/api/novels/upload",
        files={
            "file": ("lifecycle.txt", io.BytesIO(content.encode("utf-8")), "text/plain")
        },
    )
    assert upload_resp.status_code == 200
    novel_id = upload_resp.json()["id"]

    # 查询详情
    detail_resp = await auth_client.get(f"/api/novels/{novel_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["title"] == "lifecycle"
    assert detail["status"] == "ready"

    # 查询章节
    chapters_resp = await auth_client.get(f"/api/novels/{novel_id}/chapters")
    assert chapters_resp.status_code == 200
    chapters = chapters_resp.json()
    assert len(chapters) == 2
    chapter_id = chapters[0]["id"]

    # 查询单个章节
    chapter_resp = await auth_client.get(
        f"/api/novels/{novel_id}/chapters/{chapter_id}"
    )
    assert chapter_resp.status_code == 200
    chapter = chapter_resp.json()
    assert chapter["novel_id"] == novel_id
    assert chapter["chapter_number"] == 1

    # 删除
    delete_resp = await auth_client.delete(f"/api/novels/{novel_id}")
    assert delete_resp.status_code == 200
    assert "已删除" in delete_resp.json()["message"]

    # 再次查询应 404
    not_found = await auth_client.get(f"/api/novels/{novel_id}")
    assert not_found.status_code == 404

    from app.config import settings

    test_path = os.path.join(settings.upload_dir, "lifecycle.txt")
    if os.path.exists(test_path):
        os.remove(test_path)


# ─────────── 搜索测试 ───────────


@pytest.mark.asyncio
async def test_novels_search(auth_client: AsyncClient):
    """搜索功能验证"""
    # 先上传两本小说
    for title, author in [("search_test_a.txt", None), ("search_test_b.txt", None)]:
        content = f"第一章 {title}\n\n内容。"
        await auth_client.post(
            "/api/novels/upload",
            files={"file": (title, io.BytesIO(content.encode("utf-8")), "text/plain")},
        )

    # 搜索
    resp = await auth_client.get("/api/novels?search=search_test_a")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any("search_test_a" in item["title"] for item in data["items"])

    # 清理
    from app.config import settings

    for fname in ["search_test_a.txt", "search_test_b.txt"]:
        path = os.path.join(settings.upload_dir, fname)
        if os.path.exists(path):
            os.remove(path)


# ─────────── 阅读进度测试 ───────────


@pytest.mark.asyncio
async def test_update_reading_progress_success(auth_client: AsyncClient):
    """更新阅读进度成功"""
    content = "第一章 测试\n\n内容。\n\n第二章 结束\n\n结束。"
    upload_resp = await auth_client.post(
        "/api/novels/upload",
        files={
            "file": (
                "progress_test.txt",
                io.BytesIO(content.encode("utf-8")),
                "text/plain",
            )
        },
    )
    assert upload_resp.status_code == 200
    novel_id = upload_resp.json()["id"]

    # 获取章节ID
    chapters_resp = await auth_client.get(f"/api/novels/{novel_id}/chapters")
    chapters = chapters_resp.json()
    chapter_id = chapters[1]["id"]  # 第二章

    # 更新进度
    resp = await auth_client.patch(
        f"/api/novels/{novel_id}/progress",
        json={"chapter_id": chapter_id, "progress_percent": 65.5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["novel_id"] == novel_id
    assert data["chapter_id"] == chapter_id
    assert data["progress_percent"] == 65.5
    assert data["chapter_title"] == "第二章 结束"

    # 验证小说详情中进度已更新
    detail = await auth_client.get(f"/api/novels/{novel_id}")
    assert detail.json()["reading_progress"]["chapter_id"] == chapter_id

    # 清理
    from app.config import settings

    test_path = os.path.join(settings.upload_dir, "progress_test.txt")
    if os.path.exists(test_path):
        os.remove(test_path)


@pytest.mark.asyncio
async def test_update_reading_progress_novel_not_found(auth_client: AsyncClient):
    """更新不存在小说的进度返回 404"""
    resp = await auth_client.patch(
        "/api/novels/9999/progress",
        json={"chapter_id": 1, "progress_percent": 50},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_reading_progress_chapter_not_belong(auth_client: AsyncClient):
    """更新不属于该小说的章节进度返回 404"""
    # 上传两本小说
    for fname in ["prog_a.txt", "prog_b.txt"]:
        content = "第一章 测试\n\n内容。"
        await auth_client.post(
            "/api/novels/upload",
            files={"file": (fname, io.BytesIO(content.encode("utf-8")), "text/plain")},
        )

    # 获取小说A的ID和小说B的章节ID
    novels_resp = await auth_client.get("/api/novels")
    novels = novels_resp.json()["items"]
    novel_a_id = novels[0]["id"]
    novel_b_id = novels[1]["id"]

    chapters_b = await auth_client.get(f"/api/novels/{novel_b_id}/chapters")
    chapter_b_id = chapters_b.json()[0]["id"]

    # 尝试用小说A的ID更新小说B的章节
    resp = await auth_client.patch(
        f"/api/novels/{novel_a_id}/progress",
        json={"chapter_id": chapter_b_id, "progress_percent": 50},
    )
    assert resp.status_code == 404

    # 清理
    from app.config import settings

    for fname in ["prog_a.txt", "prog_b.txt"]:
        path = os.path.join(settings.upload_dir, fname)
        if os.path.exists(path):
            os.remove(path)
