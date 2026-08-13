"""Safe single-process launcher for the packaged local dashboard."""
from __future__ import annotations

import argparse
from importlib import resources
from pathlib import Path
import socket
import sys
import webbrowser
from uuid import uuid4

from .api.app import DEFAULT_HOST, create_app
from .projects import ProjectStore, default_data_root
from .application import ControlTranslatorService
from .runs.errors import ProjectRunConflictError
from .runs.lock import DataRootInstanceLock


def packaged_assets() -> Path:
    """Return the installed dashboard assets or raise a deterministic error."""
    try:
        assets = Path(str(resources.files("control_translator.web_assets")))
    except (ModuleNotFoundError, TypeError) as exc:
        raise RuntimeError("Dashboard assets are unavailable; reinstall the control-translator web package.") from exc
    if not (assets / "index.html").is_file():
        raise RuntimeError("Dashboard assets are unavailable; reinstall the control-translator web package.")
    return assets


def validate_data_root(value: str | None) -> Path | None:
    """Validate an explicitly selected root without exposing it in diagnostics."""
    if value is None:
        return None
    try:
        if not value.strip():
            raise OSError
        root = Path(value).expanduser()
        if root.exists() and (not root.is_dir() or root.is_symlink()):
            raise OSError
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise RuntimeError("The selected data root is unavailable.") from exc
    return root


def reserve_loopback_port(port: int) -> tuple[socket.socket, int]:
    """Bind once and pass this socket to Uvicorn, avoiding a probe/bind race."""
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        listener.bind((DEFAULT_HOST, port))
        listener.listen(socket.SOMAXCONN)
        return listener, int(listener.getsockname()[1])
    except OSError as exc:
        raise RuntimeError("The requested loopback port is unavailable.") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ct-web", description="Launch the local control-translator dashboard")
    parser.add_argument("--port", type=int, default=0, help="Loopback port (default: choose an available port)")
    parser.add_argument("--data-root", help="Directory for local project data")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    parser.add_argument("--print-token", action="store_true", help="Print the session token for explicit manual connection")
    return parser


def main() -> None:
    """Launch the authenticated API and packaged dashboard on one loopback port."""
    try:
        import uvicorn
    except ModuleNotFoundError:
        print("The 'web' extra is required: pip install 'control-translator[web]'", file=sys.stderr)
        raise SystemExit(1) from None

    args = build_parser().parse_args()
    if not 0 <= args.port <= 65535:
        print("Port must be between 0 and 65535.", file=sys.stderr)
        raise SystemExit(2)
    try:
        assets = packaged_assets()
        root = validate_data_root(args.data_root if args.data_root is not None else str(default_data_root()))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None

    instance_lock = DataRootInstanceLock(root)
    try:
        instance_lock.acquire(uuid4().hex)
    except ProjectRunConflictError:
        print("Another local dashboard is already using the selected data root.", file=sys.stderr)
        raise SystemExit(1) from None

    try:
        listener, port = reserve_loopback_port(args.port)
    except RuntimeError as exc:
        instance_lock.release()
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None

    try:
        service = ControlTranslatorService(project_store=ProjectStore(root))
        app = create_app(service=service, static_assets=assets)
    except Exception:  # noqa: BLE001 - startup diagnostics must not disclose local details
        listener.close()
        instance_lock.release()
        print("The local dashboard could not start.", file=sys.stderr)
        raise SystemExit(1) from None
    url = f"http://{DEFAULT_HOST}:{port}/"
    bootstrap_url = f"{url}#ct-session-token={app.state.session_token}"
    if args.print_token:
        print(f"Local session token: {app.state.session_token}", file=sys.stderr)
    print(f"Local dashboard: {url}", file=sys.stderr)
    if not args.no_browser and sys.stdin.isatty():
        webbrowser.open(bootstrap_url)

    config = uvicorn.Config(app, host=DEFAULT_HOST, port=port, log_level="warning", access_log=False)
    try:
        try:
            uvicorn.Server(config).run(sockets=[listener])
        except KeyboardInterrupt:
            pass
    finally:
        listener.close()
        instance_lock.release()
