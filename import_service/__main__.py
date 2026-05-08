"""Enables: python -m import_service <command>."""
import sys

from import_service.cli import main
from utils.logging import configure_logging

configure_logging()
sys.exit(main())
