"""Command line entry point: ``support-admin {run-once|backfill|dry-run|run}``."""

from __future__ import annotations

import json
import logging

import click

from pathlib import Path

from . import runner, ticket_report
from .config import get_settings


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-v", "--verbose", is_flag=True, help="Enable DEBUG logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Jazzware HubSpot support-admin runner."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)


@cli.command("run-once")
@click.option("--limit", default=100, show_default=True, help="Max tickets to process.")
@click.option("--json-out", is_flag=True, help="Emit outcomes as JSON.")
def run_once_cmd(limit: int, json_out: bool) -> None:
    """Fetch latest tickets, run rules, write annotations, then exit."""
    outcomes = runner.run_once(dry_run=False, limit=limit)
    _emit(outcomes, json_out)


@cli.command("dry-run")
@click.option("--limit", default=100, show_default=True)
@click.option("--json-out", is_flag=True)
def dry_run_cmd(limit: int, json_out: bool) -> None:
    """Like run-once but never writes back to HubSpot."""
    outcomes = runner.run_once(dry_run=True, limit=limit)
    _emit(outcomes, json_out)


@cli.command("backfill")
@click.option("--limit", default=1000, show_default=True)
@click.option("--dry-run/--write", default=False, show_default=True)
@click.option("--json-out", is_flag=True)
def backfill_cmd(limit: int, dry_run: bool, json_out: bool) -> None:
    """Pull a larger batch of recent tickets and run all rules."""
    outcomes = runner.backfill(dry_run=dry_run, limit=limit)
    _emit(outcomes, json_out)


@cli.command("run")
@click.option("--interval", type=int, default=None, help="Override POLL_INTERVAL_SECONDS.")
@click.option("--dry-run/--write", default=False)
def run_cmd(interval: int | None, dry_run: bool) -> None:
    """Long-running poll loop. Ctrl-C to stop."""
    runner.run_loop(dry_run=dry_run, interval_seconds=interval)


@cli.command("report")
@click.option("--days", default=90, show_default=True, help="How far back to pull tickets.")
@click.option("--limit", default=500, show_default=True, help="Max tickets to pull.")
@click.option(
    "--snapshot",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("data/tickets_snapshot.json"),
    show_default=True,
    help="Where to save (or load) the local JSON snapshot.",
)
@click.option(
    "--use-snapshot/--refresh",
    default=False,
    show_default=True,
    help="Reuse the existing snapshot file instead of calling HubSpot.",
)
@click.option(
    "--skip-semantic",
    is_flag=True,
    help="Skip the Claude semantic-clustering pass (exact-match + repeats only).",
)
@click.option(
    "--anthropic-model",
    default="claude-sonnet-4-5",
    show_default=True,
    help="Claude model id for semantic clustering.",
)
@click.option("--top-n", default=10, show_default=True, help="Top N results per section.")
@click.option("--json-out", is_flag=True, help="Emit full result as JSON instead of text.")
def report_cmd(
    days: int,
    limit: int,
    snapshot: Path,
    use_snapshot: bool,
    skip_semantic: bool,
    anthropic_model: str,
    top_n: int,
    json_out: bool,
) -> None:
    """JAZ-68: Dedup + repeat-issue detector against real HubSpot tickets."""
    result = ticket_report.run_report(
        days=days,
        limit=limit,
        snapshot_path=snapshot,
        use_snapshot=use_snapshot,
        skip_semantic=skip_semantic,
        anthropic_model=anthropic_model,
        top_n=top_n,
    )
    if json_out:
        from dataclasses import asdict

        payload = {
            "snapshot_path": str(result.snapshot_path),
            "ticket_count": result.ticket_count,
            "exact_clusters": [asdict(c) for c in result.exact_clusters],
            "semantic_clusters": [asdict(c) for c in result.semantic_clusters],
            "repeat_customers": [asdict(r) for r in result.repeat_customers],
        }
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo(result.text)


@cli.command("config")
def config_cmd() -> None:
    """Print effective config (token redacted)."""
    s = get_settings()
    click.echo(
        json.dumps(
            {
                "hubspot_mock_mode": s.hubspot_mock_mode,
                "use_openai_embeddings": s.use_openai_embeddings,
                "vip_list_path": str(s.vip_list_path) if s.vip_list_path else None,
                "db_path": str(s.db_path),
                "repeat_sim_threshold": s.repeat_sim_threshold,
                "repeat_window_days": s.repeat_window_days,
                "poll_interval_seconds": s.poll_interval_seconds,
            },
            indent=2,
        )
    )


def _emit(outcomes, json_out: bool) -> None:
    if json_out:
        click.echo(json.dumps([o.to_dict() for o in outcomes], indent=2, default=str))
        return
    if not outcomes:
        click.echo("No tickets processed.")
        return
    for o in outcomes:
        flags: list[str] = []
        if o.dedup.is_repeat:
            flags.append(f"repeat({o.dedup.link_kind},{o.dedup.score:.2f}->{o.dedup.repeat_of})")
        if o.vip.is_vip:
            flags.append(f"VIP({o.vip.entry.tier if o.vip.entry else '?'})")
        flags.append(f"contract={o.contract_status}")
        click.echo(f"{o.ticket_id} [{o.company_id or '-'}] {'  '.join(flags)} written={o.written}")


if __name__ == "__main__":  # pragma: no cover
    cli()
