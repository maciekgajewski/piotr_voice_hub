"""`piotr-voice-hub` entry point. For now (T-004) it only loads the config and logs it."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from piotr_voice_hub.config import LOG_LEVELS, ConfigError, load_config

EXIT_CONFIG_ERROR = 2
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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(format=LOG_FORMAT, stream=sys.stderr)
    try:
        config = load_config(args.config, overrides_from_args(args))
    except ConfigError as error:
        logger.error("Invalid config: %s", error)
        return EXIT_CONFIG_ERROR
    logging.getLogger().setLevel(config.log_level)
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
    return 0
