"""
Listing photo upload — presign endpoint + attach/delete media + storage helpers.

LLM-free AND storage-free: presigning is pure local HMAC (boto3 makes no network
call), and the one networked op (delete_object) is monkeypatched. The suite must
stay green with MinIO stopped.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from catalog import services, storage

User = get_user_model()

PRESIGN = "/api/uploads/presign"
LISTINGS = "/api/listings/"


@pytest.fixture
def owner(db):
    return User.objects.create_user(
        email="owner@x.com", password="pw-12345678", is_email_verified=True, full_name="Owner"
    )


@pytest.fixture
def client(owner):
    c = APIClient()
    c.force_authenticate(user=owner)
    return c


@pytest.fixture
def other_client(db):
    other = User.objects.create_user(
        email="other@x.com", password="pw-12345678", is_email_verified=True, full_name="Other"
    )
    c = APIClient()
    c.force_authenticate(user=other)
    return c


@pytest.fixture
def listing(owner):
    return services.create_listing(
        owner,
        {
            "title": "Photo test house",
            "properties": [{"address": "9 Birch Ln, Seattle WA", "beds": 3}],
            "media": [{"kind": "photo", "url": storage.public_url("listings/1/aaa.jpg")}],
        },
    )


# --- presign ------------------------------------------------------------------


def test_presign_requires_auth(db):
    resp = APIClient().post(PRESIGN, {"content_type": "image/jpeg"}, format="json")
    assert resp.status_code in (401, 403)


def test_presign_happy_path(client, owner):
    resp = client.post(
        PRESIGN,
        {"filename": "kitchen.jpg", "content_type": "image/jpeg", "size": 1024},
        format="json",
    )
    assert resp.status_code == 200, resp.data
    data = resp.data
    assert re.fullmatch(rf"listings/{owner.id}/[0-9a-f]{{32}}\.jpg", data["key"])
    assert data["public_url"] == f"{storage.public_url_base()}/{data['key']}"
    assert data["headers"] == {"Content-Type": "image/jpeg"}
    assert data["expires_in"] == settings.STORAGE_PRESIGN_EXPIRY
    assert data["max_bytes"] == settings.STORAGE_MAX_UPLOAD_MB * 1024 * 1024

    # The PUT target is the browser-reachable endpoint, SigV4-signed, with the
    # Content-Type header inside the signature.
    assert data["upload_url"].startswith(settings.STORAGE_ENDPOINT_PUBLIC)
    q = parse_qs(urlparse(data["upload_url"]).query)
    assert "X-Amz-Signature" in q
    assert q["X-Amz-Expires"] == [str(settings.STORAGE_PRESIGN_EXPIRY)]
    assert "content-type" in q["X-Amz-SignedHeaders"][0]


@pytest.mark.parametrize("content_type", ["application/pdf", "text/html", "image/svg+xml"])
def test_presign_rejects_bad_content_type(client, content_type):
    resp = client.post(PRESIGN, {"content_type": content_type}, format="json")
    assert resp.status_code == 400


def test_presign_rejects_oversize(client):
    too_big = settings.STORAGE_MAX_UPLOAD_MB * 1024 * 1024 + 1
    resp = client.post(PRESIGN, {"content_type": "image/png", "size": too_big}, format="json")
    assert resp.status_code == 400


def test_presign_missing_content_type(client):
    resp = client.post(PRESIGN, {"filename": "x.jpg"}, format="json")
    assert resp.status_code == 400


# --- attach -------------------------------------------------------------------


def test_attach_media_appends_after_existing(client, listing):
    resp = client.post(
        f"{LISTINGS}{listing.id}/media/",
        {"media": [{"kind": "photo", "url": storage.public_url("listings/1/bbb.jpg")}]},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    media = resp.data["media"]
    assert len(media) == 2
    assert media[-1]["sort_order"] == 1  # appended after the create-time photo


def test_attach_media_cross_user_404(other_client, listing):
    resp = other_client.post(
        f"{LISTINGS}{listing.id}/media/",
        {"media": [{"url": "https://example.com/x.jpg"}]},
        format="json",
    )
    assert resp.status_code == 404
    assert listing.media.count() == 1


def test_attach_media_empty_list_400(client, listing):
    resp = client.post(f"{LISTINGS}{listing.id}/media/", {"media": []}, format="json")
    assert resp.status_code == 400


# --- delete -------------------------------------------------------------------


@pytest.fixture
def delete_recorder(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("catalog.storage.delete_object_for_url", calls.append)
    return calls


def test_delete_media_owner(client, listing, delete_recorder):
    row = listing.media.get()
    resp = client.delete(f"{LISTINGS}{listing.id}/media/{row.id}/")
    assert resp.status_code == 200, resp.data
    assert resp.data["media"] == []
    assert listing.media.count() == 0
    assert delete_recorder == [row.url]


def test_delete_media_cross_user_404(other_client, listing, delete_recorder):
    row = listing.media.get()
    resp = other_client.delete(f"{LISTINGS}{listing.id}/media/{row.id}/")
    assert resp.status_code == 404
    assert listing.media.count() == 1
    assert delete_recorder == []


def test_delete_media_unknown_id_404(client, listing, delete_recorder):
    resp = client.delete(f"{LISTINGS}{listing.id}/media/999999/")
    assert resp.status_code == 404
    assert delete_recorder == []


def test_delete_media_skips_foreign_urls(client, owner, monkeypatch):
    """A media row with a foreign URL (seed Unsplash, legacy paste) deletes its DB
    row without ever touching the storage client."""
    lst = services.create_listing(
        owner,
        {
            "properties": [{"address": "11 Birch Ln, Seattle WA"}],
            "media": [{"url": "https://images.unsplash.com/photo-abc"}],
        },
    )
    monkeypatch.setattr(
        "catalog.storage._client",
        lambda endpoint: pytest.fail("storage client must not be used for foreign URLs"),
    )
    row = lst.media.get()
    resp = client.delete(f"{LISTINGS}{lst.id}/media/{row.id}/")
    assert resp.status_code == 200
    assert lst.media.count() == 0


# --- storage / service units --------------------------------------------------


def test_key_for_url_roundtrip(db):
    key = "listings/7/deadbeef.jpg"
    assert storage.key_for_url(storage.public_url(key)) == key
    assert storage.key_for_url("https://images.unsplash.com/photo-abc") is None
    assert storage.key_for_url(storage.public_url_base() + "/") is None


def test_delete_object_swallows_storage_errors(db, monkeypatch):
    class _Raising:
        def delete_object(self, **kw):
            raise RuntimeError("storage down")

    monkeypatch.setattr("catalog.storage._client", lambda endpoint: _Raising())
    storage.delete_object_for_url(storage.public_url("listings/7/deadbeef.jpg"))  # no raise


def test_add_listing_media_sort_order(owner):
    lst = services.create_listing(owner, {"properties": [{"address": "5 Oak St, Seattle WA"}]})
    rows = services.add_listing_media(
        lst, [{"url": "u1"}, {"url": "u2"}, {"url": "u3", "sort_order": 9}]
    )
    assert [r.sort_order for r in rows] == [0, 1, 9]
    more = services.add_listing_media(lst, [{"url": "u4"}])
    assert more[0].sort_order == 10  # continues after the current max
