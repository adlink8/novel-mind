"""Reader Chat image generation and owner-scoped file persistence."""

from __future__ import annotations

import base64
import binascii
import secrets
import struct
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.novel import Chapter, Novel
from app.models.reader_chat import GeneratedImage, ReaderConversation, ReaderMessage
from app.schemas.reader_chat import (
    ImageGenerationRequest,
    ImageGenerationResponse,
    MessageType,
)


def _prompt_parts(data: ImageGenerationRequest) -> tuple[str, str]:
    selected = (data.selected_text or "").strip()
    refine = (data.user_refine or "").strip()
    if not selected and not refine:
        raise HTTPException(status_code=400, detail="请先选择文本或输入画面描述")
    if selected and refine:
        prompt_cn = f"{selected}\n\n画面补充：{refine}"
    else:
        prompt_cn = selected or refine
    # Hunyuan accepts Chinese prompts. Translation is intentionally optional in
    # the wiki contract, so the local provider receives the user's exact prompt.
    return prompt_cn, prompt_cn


def _decode_data_url(value: str) -> bytes:
    if "," not in value:
        raise ValueError("invalid image data")
    encoded = value.split(",", 1)[1]
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid image base64") from exc


def _image_dimensions(data: bytes) -> tuple[int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in (0xD8, 0xD9):
                continue
            if index + 2 > len(data):
                break
            segment_size = struct.unpack(">H", data[index : index + 2])[0]
            if marker in range(0xC0, 0xC4) or marker in range(0xC5, 0xC8) or marker in range(0xC9, 0xCC) or marker in range(0xCD, 0xD0):
                if index + 7 <= len(data):
                    height, width = struct.unpack(">HH", data[index + 3 : index + 7])
                    return width, height
            index += max(segment_size, 2)
    return 1024, 1024


def _provider_error_detail(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()[:300]
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()[:300]
    return None


async def _request_image(prompt: str) -> tuple[bytes, str]:
    endpoint = settings.image_generation_base_url.rstrip("/") + "/v1/images/generations"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            response = await client.post(
                endpoint,
                json={"prompt": prompt, "size": "1024x1024", "response_format": "b64_json"},
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                provider_detail = _provider_error_detail(response)
                detail = f"生图服务返回 HTTP {response.status_code}"
                if provider_detail:
                    detail += f"：{provider_detail}"
                raise HTTPException(status_code=502, detail=detail) from exc
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise HTTPException(status_code=502, detail="生图服务返回格式无效：缺少 data 图片字段")
            items = payload["data"]
            if not items or not isinstance(items[0], dict):
                raise HTTPException(status_code=502, detail="生图服务返回格式无效：没有图片结果")
            item = items[0]
            if item.get("b64_json"):
                image_bytes = base64.b64decode(item["b64_json"], validate=True)
                return image_bytes, "jpg" if image_bytes.startswith(b"\xff\xd8") else "png"
            image_url = item.get("url")
            if image_url:
                parsed = urlparse(image_url)
                if parsed.scheme == "data":
                    image_bytes = _decode_data_url(image_url)
                    return image_bytes, "jpg" if image_bytes.startswith(b"\xff\xd8") else "png"
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("provider returned an invalid image URL")
                image_response = await client.get(image_url)
                image_response.raise_for_status()
                image_bytes = image_response.content
                return image_bytes, "jpg" if image_bytes.startswith(b"\xff\xd8") else "png"
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail="无法连接生图服务，请确认 ZCodeProxy 已启动（127.0.0.1:3001）") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="生图服务响应超时，请检查 ZCodeProxy 和混元服务状态") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"访问生图服务失败：{str(exc)[:300]}") from exc
    except HTTPException:
        raise
    except (ValueError, IndexError, KeyError, TypeError, binascii.Error) as exc:
        raise HTTPException(status_code=502, detail=f"生图服务返回图片数据无效：{str(exc)[:300]}") from exc
    raise HTTPException(status_code=502, detail="生图服务未返回图片")


async def _get_or_create_conversation(
    db: AsyncSession,
    *,
    novel_id: int,
    owner_id: int,
    conversation_id: int | None,
) -> ReaderConversation:
    if conversation_id is not None:
        conversation = (
            await db.execute(
                select(ReaderConversation).where(
                    ReaderConversation.id == conversation_id,
                    ReaderConversation.novel_id == novel_id,
                    ReaderConversation.owner_id == owner_id,
                )
            )
        ).scalar_one_or_none()
        if conversation is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return conversation

    conversation = (
        await db.execute(
            select(ReaderConversation)
            .where(
                ReaderConversation.novel_id == novel_id,
                ReaderConversation.owner_id == owner_id,
                ReaderConversation.status == "active",
            )
            .order_by(ReaderConversation.last_opened_at.desc().nullslast(), ReaderConversation.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if conversation is not None:
        return conversation
    conversation = ReaderConversation(
        owner_id=owner_id,
        novel_id=novel_id,
        title="画图会话",
        status="active",
        next_sequence=1,
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def generate_image(
    db: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    data: ImageGenerationRequest,
) -> ImageGenerationResponse:
    prompt_cn, prompt = _prompt_parts(data)
    chapter_id = data.chapter_id
    if chapter_id is not None:
        chapter = (
            await db.execute(
                select(Chapter).where(Chapter.id == chapter_id, Chapter.novel_id == novel.id)
            )
        ).scalar_one_or_none()
        if chapter is None:
            raise HTTPException(status_code=404, detail="章节不存在")

    conversation = await _get_or_create_conversation(
        db,
        novel_id=novel.id,
        owner_id=owner_id,
        conversation_id=data.conversation_id,
    )
    if conversation.status != "active":
        raise HTTPException(status_code=409, detail="归档会话不能生成图片")

    image_bytes, extension = await _request_image(prompt)
    if not image_bytes or len(image_bytes) > max(settings.max_upload_size, 50 * 1024 * 1024):
        raise HTTPException(status_code=500, detail="图片生成失败：图片文件无效或过大")
    width, height = _image_dimensions(image_bytes)

    filename = f"{int(__import__('time').time())}_{secrets.token_hex(4)}.{extension}"
    relative_path = Path(str(novel.id)) / filename
    storage_root = Path(settings.image_storage_dir).resolve()
    novel_dir = storage_root / str(novel.id)
    novel_dir.mkdir(parents=True, exist_ok=True)
    file_path = novel_dir / filename
    file_path.write_bytes(image_bytes)

    try:
        locked_conversation = (
            await db.execute(
                select(ReaderConversation)
                .where(
                    ReaderConversation.id == conversation.id,
                    ReaderConversation.novel_id == novel.id,
                    ReaderConversation.owner_id == owner_id,
                )
                .with_for_update()
            )
        ).scalar_one()
        if locked_conversation.status != "active":
            raise HTTPException(status_code=409, detail="归档会话不能生成图片")
        sequence = int(locked_conversation.next_sequence)
        generated = GeneratedImage(
            novel_id=novel.id,
            chapter_id=chapter_id,
            conversation_id=locked_conversation.id,
            owner_id=owner_id,
            prompt_cn=prompt_cn,
            prompt_en=prompt,
            source_start=data.source_start,
            source_end=data.source_end,
            selected_text=data.selected_text,
            file_path=f"images/{relative_path.as_posix()}",
            file_size=len(image_bytes),
            width=width,
            height=height,
            model_used=settings.image_generation_model,
        )
        db.add(generated)
        await db.flush()
        message = ReaderMessage(
            conversation_id=locked_conversation.id,
            owner_id=owner_id,
            novel_id=novel.id,
            sequence=sequence,
            role="assistant",
            message_type=MessageType.IMAGE.value,
            body=prompt_cn,
            content_hash=None,
            image_generation_id=generated.id,
        )
        db.add(message)
        locked_conversation.next_sequence = sequence + 1
        await db.flush()
        await db.refresh(generated)
        await db.refresh(message)
    except Exception:
        file_path.unlink(missing_ok=True)
        raise

    return ImageGenerationResponse(
        id=generated.id,
        message_id=message.id,
        image_url=f"/storage/images/{novel.id}/{filename}",
        prompt=prompt,
        prompt_cn=prompt_cn,
        created_at=generated.created_at,
        width=width,
        height=height,
        file_size=len(image_bytes),
    )


class ImageGenerationService:
    async def generate_image(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        data: ImageGenerationRequest,
    ) -> ImageGenerationResponse:
        return await generate_image(db, novel=novel, owner_id=owner_id, data=data)


image_generation_service = ImageGenerationService()
