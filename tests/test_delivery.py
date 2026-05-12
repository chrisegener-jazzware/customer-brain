"""Delivery adapter tests (all stubs)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from health.delivery import (  # noqa: E402
    DiskAdapter,
    EmailAdapter,
    HubSpotNoteAdapter,
    TeamsAdapter,
    build_default_adapters,
    deliver,
)


def test_disk_adapter_writes_file(tmp_path):
    ad = DiskAdapter(out_dir=tmp_path)
    p = ad.send("alex@x.com", "# digest body", date="2026-05-11")
    assert p.exists()
    assert "digest body" in p.read_text()
    assert "2026-05-11" in p.name
    assert "alex_at_x.com" in p.name


def test_stub_adapters_do_not_raise(capsys):
    for ad_cls in (EmailAdapter, HubSpotNoteAdapter, TeamsAdapter):
        ad = ad_cls()
        out = ad.send("a@x", "# md", date="2026-05-11")
        assert out is None
    captured = capsys.readouterr().out
    assert "[STUB email]" in captured
    assert "[STUB hubspot_note]" in captured
    assert "[STUB teams]" in captured


def test_build_default_adapters_filters_unknown(tmp_path):
    ads = build_default_adapters(out_dir=tmp_path, channels=["email", "bogus"])
    names = [a.name for a in ads]
    assert names == ["disk", "email"]


def test_deliver_returns_artifacts(tmp_path):
    digests = {"a@x": "# A", "b@y": "# B"}
    ads = build_default_adapters(out_dir=tmp_path, channels=["email", "teams"])
    artifacts = deliver(digests, ads, date="2026-05-11")
    assert len(artifacts) == 2
    for p in artifacts:
        assert p.exists()
