"""Logging utilities for the pipeline."""
import logging
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

from utils.config import config, LOGS_DIR

console = Console()


def setup_logger(
    name: str,
    log_file: Optional[str] = None,
    level: Optional[str] = None
) -> logging.Logger:
    """
    Set up a logger with both file and console handlers.
    
    Args:
        name: Logger name
        log_file: Optional log file path (relative to logs directory)
        level: Log level (DEBUG, INFO, WARNING, ERROR)
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Set level from config or parameter
    log_level = level or config.orchestrator.log_level
    logger.setLevel(getattr(logging, log_level))
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    # Console handler with rich formatting
    console_handler = RichHandler(
        rich_tracebacks=True,
        console=console,
        show_time=True,
        show_path=False
    )
    console_handler.setLevel(getattr(logging, log_level))
    logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        log_path = LOGS_DIR / log_file
        file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get or create a logger for the given name."""
    return setup_logger(name, log_file=config.orchestrator.log_file)
