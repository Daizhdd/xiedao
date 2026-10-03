"""Shared cancellation signal for task execution modules."""

class Stopped(Exception):
    """The author requested that the active task stop."""
