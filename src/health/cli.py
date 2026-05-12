"""CLI: `health-digest run [--customer-id X] [--dry-run]`."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from .delivery import build_default_adapters, deliver
from .digest import render_all
from .pipeline import run_pipeline, scores_to_jsonable


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@click.group(name="health-digest")
def cli() -> None:
    """Customer health digest CLI."""


@cli.command()
@click.option(
    "--customer-id",
    "customer_id",
    default=None,
    help="Run for a single HubSpot company id instead of all active customers.",
)
@click.option(
    "--dry-run/--deliver",
    "dry_run",
    default=True,
    help="Default: print digests to stdout. Use --deliver to also invoke stub adapters.",
)
@click.option(
    "--lookback-days", default=90, show_default=True,
    help="Window for discovering customers with ticket activity.",
)
@click.option(
    "--limit", type=int, default=None,
    help="Cap on number of customers scored (smoke testing).",
)
@click.option(
    "--no-claude", is_flag=True, default=False,
    help="Skip Claude narrative — use templated fallback (cheaper / offline).",
)
@click.option(
    "--channels", default="", help="Comma-separated delivery stubs to invoke "
    "with --deliver: email,hubspot_note,teams. Disk is always on.",
)
@click.option(
    "--out-dir", default="./out", show_default=True, type=click.Path(),
    help="Directory for digest artifacts.",
)
@click.option(
    "--json-out", default=None, type=click.Path(),
    help="Also dump raw scores to this JSON path.",
)
@click.option("-v", "--verbose", is_flag=True, default=False)
def run(
    customer_id: str | None,
    dry_run: bool,
    lookback_days: int,
    limit: int | None,
    no_claude: bool,
    channels: str,
    out_dir: str,
    json_out: str | None,
    verbose: bool,
) -> None:
    """Run the full pipeline: discover -> score -> render -> deliver."""
    _configure_logging(verbose)

    scores = run_pipeline(
        customer_id=customer_id,
        lookback_days=lookback_days,
        use_claude=not no_claude,
        limit=limit,
    )
    if not scores:
        click.echo("No customers scored. Exiting.", err=True)
        sys.exit(0)

    now = datetime.now(timezone.utc)
    digests = render_all(scores, generated_at=now)
    date_str = now.strftime("%Y-%m-%d")

    if json_out:
        Path(json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(json_out).write_text(
            json.dumps(scores_to_jsonable(scores), indent=2, default=str),
            encoding="utf-8",
        )
        click.echo(f"wrote scores -> {json_out}", err=True)

    if dry_run:
        # Print all digests to stdout, separated.
        for am, md in digests.items():
            click.echo(f"\n\n===== {am} =====\n")
            click.echo(md)
        click.echo(
            f"\n[dry-run] {len(scores)} customers across {len(digests)} AMs. "
            "Use --deliver to also invoke stub adapters.",
            err=True,
        )
        return

    channel_list = [c.strip() for c in channels.split(",") if c.strip()]
    adapters = build_default_adapters(out_dir=out_dir, channels=channel_list)
    artifacts = deliver(digests, adapters, date=date_str)
    click.echo(
        f"Delivered {len(digests)} AM digests via {len(adapters)} adapters. "
        f"Disk artifacts: {len(artifacts)}",
        err=True,
    )
    for p in artifacts:
        click.echo(str(p))


if __name__ == "__main__":  # pragma: no cover
    cli()
