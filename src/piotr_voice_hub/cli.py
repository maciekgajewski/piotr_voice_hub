"""`piotr-voice-hub` entry point: load the config and run the hub (T-012), and
`piotr-voice-hub-healthcheck`, the container's liveness probe."""

import argparse
import asyncio
import logging
import sys
import urllib.request
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

from piotr_voice_hub.config import LOG_LEVELS, ConfigError, HubConfig, load_config
from piotr_voice_hub.hub.main import EXIT_FAILED, quiet_library_loggers

EXIT_CONFIG_ERROR = 2
HEALTHCHECK_TIMEOUT_SECONDS = 3.0
WILDCARD_HOSTS = ("", "0.0.0.0", "::")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

logger = logging.getLogger("piotr_voice_hub")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="piotr-voice-hub", description="Piotr voice hub.")
    parser.add_argument("--config", required=True, type=Path, help="path to the hub YAML config")
    parser.add_argument("--log-level", choices=LOG_LEVELS, help="overrides log_level")
    parser.add_argument("--api-host", help="overrides api.host")
    parser.add_argument("--api-port", type=int, help="overrides api.port")
    parser.add_argument("--playback-http-port", type=int, help="overrides playback_http.port")
    return parser.parse_args(argv)


def overrides_from_args(args: argparse.Namespace) -> dict[str, object]:
    """Command-line values take precedence over the config file; unset options are omitted."""
    candidates = {
        "log_level": args.log_level,
        "api.host": args.api_host,
        "api.port": args.api_port,
        "playback_http.port": args.playback_http_port,
    }
    return {path: value for path, value in candidates.items() if value is not None}


HubRunner = Callable[[HubConfig], Coroutine[Any, Any, int]]


def _run_hub(config: HubConfig) -> Coroutine[Any, Any, int]:
    from piotr_voice_hub.hub.main import run_hub

    return run_hub(config)


def main(argv: Sequence[str] | None = None, run: HubRunner = _run_hub) -> int:
    args = parse_args(argv)
    logging.basicConfig(format=LOG_FORMAT, stream=sys.stderr)
    try:
        config = load_config(args.config, overrides_from_args(args))
    except ConfigError as error:
        logger.error("Invalid config: %s", error)
        return EXIT_CONFIG_ERROR
    logging.getLogger().setLevel(config.log_level)
    quiet_library_loggers()
    logger.info(
        "Loaded config %s: %d satellite(s) (%s), API %s:%d, playback HTTP :%d",
        args.config,
        len(config.satellites),
        ", ".join(s.id for s in config.satellites),
        config.api.host,
        config.api.port,
        config.playback_http.port,
    )
    logger.debug("Config: %s", config.redacted())
    try:
        return asyncio.run(run(config))
    except Exception:
        logger.critical("hub failed to start or run; exiting", exc_info=True)
        return EXIT_FAILED


def healthcheck_url(config: HubConfig) -> str:
    host = "127.0.0.1" if config.api.host in WILDCARD_HOSTS else config.api.host
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{config.api.port}/healthz"


def healthcheck(argv: Sequence[str] | None = None) -> int:
    """`piotr-voice-hub-healthcheck --config PATH`: 0 if /healthz answers 200 (liveness)."""
    parser = argparse.ArgumentParser(
        prog="piotr-voice-hub-healthcheck", description="Exit 0 if the hub's /healthz answers."
    )
    parser.add_argument("--config", required=True, type=Path, help="path to the hub YAML config")
    args = parser.parse_args(argv)
    try:
        url = healthcheck_url(load_config(args.config))
        with urllib.request.urlopen(url, timeout=HEALTHCHECK_TIMEOUT_SECONDS) as response:
            ok = response.status == 200
    except (ConfigError, OSError) as error:
        print(f"unhealthy: {error}", file=sys.stderr)
        return 1
    print("healthy" if ok else "unhealthy")
    return 0 if ok else 1
