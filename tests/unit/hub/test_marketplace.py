# -*- coding: utf-8 -*-
"""Tests for the self-hosted Hub skill marketplace store."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from qwenpaw.hub.auth import HubUser
from qwenpaw.hub.marketplace import MarketplaceStore


def _user(user_id: str = "user-1", role: str = "user") -> HubUser:
    return HubUser(
        user_id=user_id,
        username="alice" if user_id == "user-1" else "admin",
        role=role,
        disabled=False,
        token_version=1,
        profile={},
        preferences={},
        metadata={},
        revision=1,
        last_login_at=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _skill_zip(name: str = "demo") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"{name}/SKILL.md",
            "---\n"
            f"name: {name}\n"
            "description: A demo skill\n"
            "---\n\n"
            "# Demo\n",
        )
    return buffer.getvalue()


def test_marketplace_publish_roundtrip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("QWENPAW_SKILL_SCAN_MODE", "off")
    store = MarketplaceStore(tmp_path)
    created = store.add_submissions(
        [("demo.zip", _skill_zip())],
        _user(),
    )
    assert len(created) == 1
    item = created[0]
    assert item["status"] == "pending"
    assert item["name"] == "demo"

    pending = store.list_items(
        status="pending",
        is_admin=True,
    )
    assert [entry["id"] for entry in pending] == [item["id"]]

    reviewed = store.review(
        [item["id"]],
        action="approve",
        reviewer=_user("admin-1", role="admin"),
        note="looks good",
    )
    assert reviewed[0]["status"] == "published"

    record, payload = store.read_zip(
        item["id"],
        actor_user_id="user-2",
        is_admin=False,
    )
    assert record["status"] == "published"
    assert zipfile.is_zipfile(io.BytesIO(payload))
