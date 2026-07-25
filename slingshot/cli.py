"""Slingshot CLI entry point."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="slingshot",
        description="Agentic coding workflow driven by GitHub issues and PRs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- new ----
    p_new = subparsers.add_parser("new", help="File a new slingshot issue from a spec")
    p_new.add_argument("--spec", required=True, help="Path to the markdown spec file")
    p_new.add_argument("--repo", required=True, help="GitHub repo (owner/name)")
    p_new.add_argument("--title", help="Issue title (default: first H1 in spec)")
    p_new.set_defaults(func=_cmd_new)

    # ---- daemon ----
    from slingshot.daemon import register_parser
    register_parser(subparsers)

    args = parser.parse_args()
    args.func(args)


def _cmd_new(args: argparse.Namespace) -> None:
    from slingshot.new_cmd import cmd_new
    cmd_new(spec=args.spec, repo=args.repo, title=args.title)
