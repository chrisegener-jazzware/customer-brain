"""Delivery adapters.

Real delivery channel TBD per Linear JAZ-94. For now every adapter is a stub:
it prints what it would do and (optionally) writes the digest to disk. No
external API calls. No emails sent. No Teams messages. No HubSpot notes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class DeliveryAdapter(Protocol):
    name: str

    def send(self, am_email: str, digest_markdown: str, *, date: str) -> Path | None:
        ...


# --------------------------------------------------------------------------- #
# Disk writer (always-on; produces an artifact we can inspect)
# --------------------------------------------------------------------------- #
class DiskAdapter:
    name = "disk"

    def __init__(self, out_dir: Path | str = "./out") -> None:
        self.out_dir = Path(out_dir)

    def send(self, am_email: str, digest_markdown: str, *, date: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        safe_am = am_email.replace("@", "_at_").replace("/", "_")
        path = self.out_dir / f"digest-{date}-{safe_am}.md"
        path.write_text(digest_markdown, encoding="utf-8")
        log.info("disk: wrote %s (%d bytes)", path, len(digest_markdown))
        return path


# --------------------------------------------------------------------------- #
# Stub adapters (print only, no external calls)
# --------------------------------------------------------------------------- #
class EmailAdapter:
    """STUB. Real SMTP/Mailgun/SES wiring TBD (JAZ-94)."""

    name = "email"

    def send(self, am_email: str, digest_markdown: str, *, date: str) -> None:
        log.info(
            "[STUB email] would send digest to %s on %s (%d chars). "
            "Real channel TBD per JAZ-94.",
            am_email,
            date,
            len(digest_markdown),
        )
        print(
            f"[STUB email] -> {am_email} subject='Customer Health Digest {date}' "
            f"({len(digest_markdown)} chars body)"
        )
        return None


class HubSpotNoteAdapter:
    """STUB. Real HubSpot Engagement note creation TBD (JAZ-94)."""

    name = "hubspot_note"

    def send(self, am_email: str, digest_markdown: str, *, date: str) -> None:
        log.info(
            "[STUB hubspot_note] would attach digest note for %s on %s. "
            "Real channel TBD per JAZ-94.",
            am_email,
            date,
        )
        print(
            f"[STUB hubspot_note] -> owner_email={am_email} engagement.type=NOTE "
            f"body={len(digest_markdown)} chars"
        )
        return None


class TeamsAdapter:
    """STUB. Real MS Teams webhook / Graph API wiring TBD (JAZ-94)."""

    name = "teams"

    def send(self, am_email: str, digest_markdown: str, *, date: str) -> None:
        log.info(
            "[STUB teams] would post digest card to %s on %s. "
            "Real channel TBD per JAZ-94.",
            am_email,
            date,
        )
        print(
            f"[STUB teams] -> chat={am_email} card.title='Health Digest {date}' "
            f"body={len(digest_markdown)} chars"
        )
        return None


# --------------------------------------------------------------------------- #
# Composite
# --------------------------------------------------------------------------- #
def build_default_adapters(
    out_dir: Path | str = "./out",
    channels: list[str] | None = None,
) -> list[DeliveryAdapter]:
    """Return the disk adapter plus any requested stub channels."""
    adapters: list[DeliveryAdapter] = [DiskAdapter(out_dir=out_dir)]
    channels = channels or []
    for ch in channels:
        ch = ch.lower()
        if ch == "email":
            adapters.append(EmailAdapter())
        elif ch == "hubspot_note":
            adapters.append(HubSpotNoteAdapter())
        elif ch == "teams":
            adapters.append(TeamsAdapter())
        else:
            log.warning("unknown delivery channel %r — ignoring", ch)
    return adapters


def deliver(
    digests: dict[str, str],
    adapters: list[DeliveryAdapter],
    date: str | None = None,
) -> list[Path]:
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    artifacts: list[Path] = []
    for am_email, md in digests.items():
        for ad in adapters:
            result = ad.send(am_email, md, date=date)
            if isinstance(result, Path):
                artifacts.append(result)
    return artifacts
