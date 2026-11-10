# -*- coding: utf-8 -*-
"""Internal skill marketplace for self-hosted QwenPaw Hub.

The marketplace keeps uploaded skill archives out of the shared skill pool
until an administrator approves them. Approved archives live in a global
published directory and can be installed into any user's personal runtime
skill pool through the control plane's existing runtime proxy.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..agents.skill_system.store import (
    extract_zip_skills,
    normalize_skill_dir_name,
    read_skill_from_dir,
    safe_skill_dir,
    scan_skill_dir_or_raise,
)
from ..exceptions import SkillsError
from ..security.skill_scanner import SkillScanError
from ..utils.io_utils import read_json, write_json_atomic
from .auth import HubUser

MAX_UPLOAD_COUNT = 100
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _zip_skill_dir(skill_dir: Path, skill_name: str, target: Path) -> None:
    """Package one validated skill directory as a standard QwenPaw skill zip."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for path in sorted(skill_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(skill_dir).as_posix()
            archive.write(path, f"{skill_name}/{relative}")


class MarketplaceStore:
    """Filesystem-backed catalog for pending and published marketplace items."""

    def __init__(self, root: Path) -> None:
        self.root = root / "marketplace"
        self.catalog_path = self.root / "catalog.json"
        self.submissions_dir = self.root / "submissions"
        self.published_dir = self.root / "published"
        for path in (self.submissions_dir, self.published_dir):
            path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.catalog_path.exists():
            write_json_atomic(
                self.catalog_path,
                {"schema_version": 1, "items": {}},
            )

    def _read_catalog(self) -> dict[str, Any]:
        try:
            payload = read_json(self.catalog_path)
        except (OSError, ValueError):
            payload = {"schema_version": 1, "items": {}}
        if not isinstance(payload, dict):
            payload = {"schema_version": 1, "items": {}}
        payload.setdefault("items", {})
        return payload

    def _write_catalog(self, payload: dict[str, Any]) -> None:
        write_json_atomic(self.catalog_path, payload)

    def add_submissions(
        self,
        uploads: list[tuple[str, bytes]],
        submitter: HubUser,
    ) -> list[dict[str, Any]]:
        """Validate one or more zip uploads and stage each skill as pending."""
        created: list[dict[str, Any]] = []
        for original_name, data in uploads:
            try:
                tmp_dir, found = extract_zip_skills(data)
            except SkillsError as exc:
                raise ValueError(f"{original_name}: {exc}") from exc
            try:
                for skill_dir, raw_name in found:
                    skill_name = normalize_skill_dir_name(raw_name)
                    scan_skill_dir_or_raise(skill_dir, skill_name)
                    info = read_skill_from_dir(
                        Path(skill_dir),
                        "internal_market",
                    )
                    item_id = _new_id()
                    item_dir = self.submissions_dir / item_id
                    package_dir = item_dir / "package"
                    package_dir.mkdir(parents=True, exist_ok=True)
                    target_dir = safe_skill_dir(package_dir, skill_name)
                    shutil.copytree(
                        Path(skill_dir),
                        target_dir,
                        dirs_exist_ok=True,
                    )
                    source_zip = item_dir / "source.zip"
                    _zip_skill_dir(target_dir, skill_name, source_zip)
                    sha256 = hashlib.sha256(
                        source_zip.read_bytes(),
                    ).hexdigest()
                    now = _utc_now()
                    record = {
                        "id": item_id,
                        "name": skill_name,
                        "display_name": skill_name,
                        "version": info.version_text if info else "",
                        "description": info.description if info else "",
                        "emoji": info.emoji if info else "",
                        "tags": [],
                        "status": "pending",
                        "submitter_user_id": submitter.user_id,
                        "submitter_username": submitter.username,
                        "source_filename": original_name or "skill.zip",
                        "sha256": sha256,
                        "scan_passed": True,
                        "scan_report": "skill scanner passed",
                        "review_note": "",
                        "reviewed_by": "",
                        "reviewed_at": "",
                        "published_at": "",
                        "created_at": now,
                        "updated_at": now,
                    }
                    with self._lock:
                        catalog = self._read_catalog()
                        catalog["items"][item_id] = record
                        self._write_catalog(catalog)
                    created.append(record)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        return created

    def get_visible(
        self,
        item_id: str,
        *,
        actor_user_id: str,
        is_admin: bool,
    ) -> dict[str, Any] | None:
        with self._lock:
            record = self._read_catalog()["items"].get(item_id)
        if record is None:
            return None
        if (
            is_admin
            or record["status"] == "published"
            or record["submitter_user_id"] == actor_user_id
        ):
            return record
        return None

    def list_items(
        self,
        *,
        status: str | None,
        is_admin: bool,
        actor_user_id: str | None = None,
        query: str = "",
    ) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._read_catalog()["items"].values())

        visible: list[dict[str, Any]] = []
        for record in records:
            if status and record["status"] != status:
                continue
            if not is_admin:
                if record["status"] != "published":
                    continue
            elif status is None:
                # Admin sees all statuses unless a filter was provided.
                pass
            if query:
                needle = query.casefold()
                haystack = " ".join(
                    [
                        record.get("name", ""),
                        record.get("display_name", ""),
                        record.get("description", ""),
                        record.get("submitter_username", ""),
                        " ".join(record.get("tags", [])),
                    ],
                ).casefold()
                if needle not in haystack:
                    continue
            visible.append(record)

        return sorted(
            visible,
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )

    def review(
        self,
        item_ids: list[str],
        *,
        action: Literal["approve", "reject"],
        reviewer: HubUser,
        note: str,
    ) -> list[dict[str, Any]]:
        updated: list[dict[str, Any]] = []
        with self._lock:
            catalog = self._read_catalog()
            for item_id in item_ids:
                record = catalog["items"].get(item_id)
                if record is None:
                    continue
                if action == "approve" and record["status"] == "published":
                    updated.append(record)
                    continue

                source_dir = self.submissions_dir / item_id
                if not source_dir.exists():
                    raise ValueError(f"Missing staged files for {item_id}")

                if action == "approve":
                    target_dir = self.published_dir / item_id
                    if target_dir.exists():
                        shutil.rmtree(target_dir, ignore_errors=True)
                    shutil.copytree(source_dir, target_dir)
                    record["status"] = "published"
                    record["published_at"] = _utc_now()
                else:
                    record["status"] = "rejected"

                record["review_note"] = note
                record["reviewed_by"] = reviewer.username
                record["reviewed_at"] = _utc_now()
                record["updated_at"] = _utc_now()
                catalog["items"][item_id] = record
                updated.append(record)
            self._write_catalog(catalog)
        return updated

    def read_zip(
        self,
        item_id: str,
        *,
        actor_user_id: str,
        is_admin: bool,
    ) -> tuple[dict[str, Any], bytes]:
        record = self.get_visible(
            item_id,
            actor_user_id=actor_user_id,
            is_admin=is_admin,
        )
        if record is None:
            raise KeyError(item_id)
        source_dir = (
            self.published_dir
            if record["status"] == "published"
            else self.submissions_dir
        )
        zip_path = source_dir / item_id / "source.zip"
        if not zip_path.exists():
            raise FileNotFoundError(item_id)
        return record, zip_path.read_bytes()


class MarketplaceReviewRequest(BaseModel):
    ids: list[str] = Field(min_length=1)
    action: Literal["approve", "reject"] = "approve"
    note: str = ""


InstallSkill = Callable[[HubUser, bytes, str], Awaitable[dict[str, Any]]]


def build_marketplace_router(
    store: MarketplaceStore,
    *,
    require_user: Callable[..., HubUser],
    require_admin: Callable[..., HubUser],
    record_audit: Callable[..., Awaitable[None]],
    install_skill: InstallSkill,
    broadcast_skill: InstallSkill,
) -> APIRouter:
    """Build the Hub control-plane marketplace routes."""
    router = APIRouter(prefix="/api/hub/marketplace", tags=["hub-marketplace"])

    @router.post("/items", status_code=201)
    async def upload_items(
        user: HubUser = Depends(require_user),
        files: list[UploadFile] = File(...),
    ) -> dict[str, Any]:
        if not files:
            raise HTTPException(status_code=400, detail="No files uploaded")
        if len(files) > MAX_UPLOAD_COUNT:
            raise HTTPException(
                status_code=413,
                detail=f"At most {MAX_UPLOAD_COUNT} files per request",
            )

        uploads: list[tuple[str, bytes]] = []
        for upload in files:
            data = await upload.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"{upload.filename or 'file'} exceeds 200MB",
                )
            uploads.append((upload.filename or "skill.zip", data))

        try:
            created = await asyncio.to_thread(
                store.add_submissions,
                uploads,
                user,
            )
        except (SkillsError, SkillScanError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        await record_audit(
            user,
            "marketplace.upload",
            "skill_market",
            ",".join(item["id"] for item in created),
            {"count": len(created)},
        )
        return {"items": created}

    @router.get("/items")
    async def list_items(
        user: HubUser = Depends(require_user),
        status: str | None = Query(default=None),
        query: str = Query(default="", alias="q", max_length=128),
    ) -> dict[str, Any]:
        effective_status = status if user.is_admin else "published"
        items = await asyncio.to_thread(
            store.list_items,
            status=effective_status,
            is_admin=user.is_admin,
            actor_user_id=user.user_id,
            query=query,
        )
        return {"items": items}

    @router.get("/items/{item_id}")
    async def get_item(
        item_id: str,
        user: HubUser = Depends(require_user),
    ) -> dict[str, Any]:
        record = await asyncio.to_thread(
            store.get_visible,
            item_id,
            actor_user_id=user.user_id,
            is_admin=user.is_admin,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Item not found")
        return record

    @router.post("/review")
    async def review_items(
        body: MarketplaceReviewRequest,
        admin: HubUser = Depends(require_admin),
    ) -> dict[str, Any]:
        try:
            updated = await asyncio.to_thread(
                store.review,
                body.ids,
                action=body.action,
                reviewer=admin,
                note=body.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        await record_audit(
            admin,
            "marketplace.review",
            "skill_market",
            ",".join(body.ids),
            {
                "action": body.action,
                "updated": [item["name"] for item in updated],
            },
        )
        return {"items": updated}

    @router.get("/items/{item_id}/download")
    async def download_item(
        item_id: str,
        user: HubUser = Depends(require_user),
    ) -> Response:
        try:
            record, data = await asyncio.to_thread(
                store.read_zip,
                item_id,
                actor_user_id=user.user_id,
                is_admin=user.is_admin,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Item not found") from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=409,
                detail="Marketplace item files are missing",
            ) from exc
        filename = f"{record['name']}.zip"
        return Response(
            content=data,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f"attachment; filename*=UTF-8''{quote(filename)}"
                ),
            },
        )

    @router.post("/items/{item_id}/install")
    async def install_item(
        item_id: str,
        user: HubUser = Depends(require_user),
    ) -> dict[str, Any]:
        record = await asyncio.to_thread(
            store.get_visible,
            item_id,
            actor_user_id=user.user_id,
            is_admin=user.is_admin,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Item not found")
        if record["status"] != "published" and not user.is_admin:
            raise HTTPException(status_code=403, detail="Item is not published")
        try:
            _, data = await asyncio.to_thread(
                store.read_zip,
                item_id,
                actor_user_id=user.user_id,
                is_admin=user.is_admin,
            )
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        result = await install_skill(
            user,
            data,
            f"{record['name']}.zip",
        )
        await record_audit(
            user,
            "marketplace.install",
            "skill_market",
            item_id,
            {"skill": record["name"]},
        )
        return result

    @router.post("/items/{item_id}/broadcast")
    async def broadcast_item(
        item_id: str,
        admin: HubUser = Depends(require_admin),
    ) -> dict[str, Any]:
        record = await asyncio.to_thread(
            store.get_visible,
            item_id,
            actor_user_id=admin.user_id,
            is_admin=True,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Item not found")
        if record["status"] != "published":
            raise HTTPException(status_code=400, detail="Item is not published")
        try:
            _, data = await asyncio.to_thread(
                store.read_zip,
                item_id,
                actor_user_id=admin.user_id,
                is_admin=True,
            )
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        result = await broadcast_skill(
            admin,
            data,
            f"{record['name']}.zip",
        )
        await record_audit(
            admin,
            "marketplace.broadcast",
            "skill_market",
            item_id,
            {"skill": record["name"], "result": result},
        )
        return result

    return router
