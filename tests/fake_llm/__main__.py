# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import threading

from . import available, get_provider_class, make_all
from .providers import DEFAULT_PORTS

_PRIMARY_PATH = {
    "openai": "/v1/chat/completions",
    "claude": "/v1/messages",
    "gemini": "/v1beta/openai/chat/completions",
    "deepseek": "/chat/completions",
    "mistral": "/v1/chat/completions",
    "ollama": "/v1/chat/completions",
    "lm-studio": "/v1/chat/completions",
}


def _print_table(running) -> None:
    width = max(len(rp.name) for rp in running)
    print("\n  Fake LLM servers running (127.0.0.1 only). Ctrl+C to stop.\n")
    for rp in running:
        path = _PRIMARY_PATH.get(rp.name, "")
        print(f"    {rp.name.ljust(width)}   {rp.base_url}{path}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fake_llm", description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="start every provider on its default port")
    parser.add_argument("--provider", choices=available(),
                        help="start a single provider")
    parser.add_argument("--port", type=int, default=None,
                        help="port for --provider (default: its fixed port)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind host (default 127.0.0.1; do not expose)")
    parser.add_argument("--list", action="store_true",
                        help="list available providers and their default ports")
    args = parser.parse_args(argv)

    if args.list:
        for name in available():
            print(f"  {name.ljust(10)} default port {DEFAULT_PORTS.get(name)}")
        return 0

    if not args.all and not args.provider:
        parser.error("choose --all, --provider NAME, or --list")

    if args.all:
        multi = make_all().start(host=args.host)
        running = multi.running
        stop = multi.stop
    else:
        port = args.port if args.port is not None else DEFAULT_PORTS.get(args.provider)
        srv = get_provider_class(args.provider)().start(port=port, host=args.host)
        from .core import RunningProvider
        running = [RunningProvider(args.provider, srv, srv.base_url)]
        stop = srv.stop

    _print_table(running)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\n  Stopping...")
    finally:
        stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
