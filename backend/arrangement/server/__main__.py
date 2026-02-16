"""Entry point: ``python -m arrangement.server``.

Also usable as the ``arrangement-server`` console script.
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(prog="arrangement-server",
                                 description="Arrangement Analyzer server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    uvicorn.run(
        "arrangement.server.app:app",
        host=args.host, port=args.port, reload=args.reload,
        log_level=args.log_level, app_dir="backend",
    )


if __name__ == "__main__":
    main()
