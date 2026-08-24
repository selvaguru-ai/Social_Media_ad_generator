#!/usr/bin/env python3
"""
Dashboard server CLI.

Usage:
    python run_dashboard.py
    python run_dashboard.py --port 9000 --reload
    python run_dashboard.py --host 0.0.0.0

Then open http://127.0.0.1:8000. A run started from the dashboard pauses after
Prospect so you can verify Ad Library pages before people / video / outreach:

    python run_pipeline.py "sports shoes" --regions US
"""
import sys

import click


@click.command()
@click.option("--host", default="127.0.0.1", help="Interface to bind (default: 127.0.0.1).")
@click.option("--port", default=8000, type=int, help="Port to bind (default: 8000).")
@click.option("--reload", is_flag=True, help="Restart on code changes (development).")
def run_dashboard(host: str, port: int, reload: bool) -> None:
    """Serve the pipeline dashboard and its JSON API."""
    try:
        import uvicorn
    except ImportError:
        click.echo(
            "uvicorn is not installed. Install the dashboard extras:\n"
            "    pip install -r requirements-dashboard.txt",
            err=True,
        )
        sys.exit(1)

    click.echo(f"Dashboard → http://{host}:{port}")
    click.echo(f"API docs  → http://{host}:{port}/docs")
    uvicorn.run("api.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    run_dashboard()
