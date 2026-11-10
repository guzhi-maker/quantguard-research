"""Start the QuantGuard data collection workbench.

Usage:
    python run_server.py --port 8789
"""

from __future__ import annotations

import argparse

from data_agent.server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="QuantGuard 数据采集工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
