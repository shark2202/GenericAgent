#!/usr/bin/env python3
"""ga mcp - MCP Server management CLI"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description="MCP Server management")
    sub = parser.add_subparsers(dest="action")

    sub.add_parser("list", help="List all MCP servers and their status")

    start_p = sub.add_parser("start", help="Start a specific MCP server")
    start_p.add_argument("name", help="Server name")

    stop_p = sub.add_parser("stop", help="Stop a specific MCP server")
    stop_p.add_argument("name", help="Server name")

    restart_p = sub.add_parser("restart", help="Restart a specific MCP server")
    restart_p.add_argument("name", help="Server name")

    args = parser.parse_args()

    try:
        from mcp_client import MCPClientManager
    except Exception as e:
        print(f"[Error] MCP Client not available: {e}")
        print("        Install mcp dependencies or check mcp_client.py exists.")
        sys.exit(1)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        mgr = MCPClientManager(project_root)
        mgr.start()  # connect all
    except Exception as e:
        print(f"[Error] Failed to initialize MCP Client: {e}")
        sys.exit(1)

    try:
        if args.action == "list":
            status = mgr.get_status()
            if not status:
                print("No MCP servers configured.")
            for name, info in status.items():
                state = info.get('state', 'unknown')
                transport = info.get('transport', '?')
                tools = info.get('tools_count', 0)
                print(f"  {name:20s} [{transport:5s}] {state:15s} tools={tools}")
        elif args.action == "start":
            mgr.start_server(args.name)
            print(f"Started: {args.name}")
        elif args.action == "stop":
            mgr.stop_server(args.name)
            print(f"Stopped: {args.name}")
        elif args.action == "restart":
            mgr.restart_server(args.name)
            print(f"Restarted: {args.name}")
        else:
            parser.print_help()
    finally:
        mgr.stop()


if __name__ == "__main__":
    main()
