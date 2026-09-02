"""Engine state and mode enumerations for UltraBot."""
from enum import Enum


class EngineState(str, Enum):
    """Lifecycle states of the trading engine."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    SCANNING = "scanning"
    ERROR = "error"


class EngineMode(str, Enum):
    """Trading mode of the engine."""
    PAPER = "paper"
    LIVE = "live"
