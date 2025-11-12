"""Phase cache system for storing and reusing phase execution results."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from cafe.core.status_codes import PhaseStatusCode


class CacheEntry(BaseModel):
    """Represents a cached phase execution result."""

    phase_name: str
    status_code: PhaseStatusCode
    response: str
    content_hash: str
    timestamp: datetime

    def to_dict(self) -> dict:
        """Convert CacheEntry to dictionary for JSON serialization.

        Returns:
            Dictionary representation of the cache entry
        """
        return {
            "phase_name": self.phase_name,
            "status_code": self.status_code.value,
            "response": self.response,
            "content_hash": self.content_hash,
            "timestamp": self.timestamp.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CacheEntry":
        """Create CacheEntry from dictionary.

        Args:
            data: Dictionary containing cache entry data

        Returns:
            CacheEntry instance
        """
        return cls(
            phase_name=data["phase_name"],
            status_code=PhaseStatusCode(data["status_code"]),
            response=data["response"],
            content_hash=data["content_hash"],
            timestamp=datetime.fromisoformat(data["timestamp"])
        )


class PhaseCache:
    """Manages caching of phase execution results.

    Cache files are stored in: .cafe/cache/session_{id}/phase_{num}_{name}.json

    This allows workflows to:
    - Skip already-completed phases on re-run
    - Resume from breakpoints
    - Save time and API costs
    """

    # Phase name to number mapping
    PHASE_NAMES = {
        "requirements": 0,
        "analysis": 1,
        "implementation": 2,
        "review": 3,
        "pr": 4,
    }

    def __init__(self, session_id: str, cache_dir: str = ".cafe/cache") -> None:
        """Initialize phase cache.

        Args:
            session_id: Session identifier
            cache_dir: Base directory for cache files
        """
        self.session_id = session_id
        self.cache_dir = Path(cache_dir) / f"session_{session_id}"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_phase_number(self, phase_name: str) -> int:
        """Get phase number from phase name.

        Args:
            phase_name: Name of the phase

        Returns:
            Phase number (0-indexed), or -1 if unknown
        """
        return self.PHASE_NAMES.get(phase_name, -1)

    def get_cache_file(self, phase_name: str) -> Path:
        """Get the cache file path for a phase.

        Args:
            phase_name: Name of the phase

        Returns:
            Path to the cache file
        """
        phase_num = self.get_phase_number(phase_name)
        return self.cache_dir / f"phase_{phase_num}_{phase_name}.json"

    def save(
        self,
        phase_name: str,
        status_code: PhaseStatusCode,
        response: str,
        content_hash: str
    ) -> None:
        """Save phase execution result to cache.

        Args:
            phase_name: Name of the phase
            status_code: Status code returned by the phase
            response: Agent response text
            content_hash: Hash of input content for validation
        """
        entry = CacheEntry(
            phase_name=phase_name,
            status_code=status_code,
            response=response,
            content_hash=content_hash,
            timestamp=datetime.now()
        )

        cache_file = self.get_cache_file(phase_name)
        with cache_file.open("w", encoding="utf-8") as f:
            json.dump(entry.to_dict(), f, indent=2, ensure_ascii=False)

    def load(self, phase_name: str) -> Optional[CacheEntry]:
        """Load cached phase result.

        Args:
            phase_name: Name of the phase

        Returns:
            CacheEntry if exists, None otherwise
        """
        cache_file = self.get_cache_file(phase_name)
        if not cache_file.exists():
            return None

        with cache_file.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return CacheEntry.from_dict(data)

    def is_valid(self, phase_name: str, current_hash: str) -> bool:
        """Check if cached result is still valid.

        A cache is valid if:
        - Cache file exists
        - Content hash matches current hash

        Args:
            phase_name: Name of the phase
            current_hash: Current hash of input content

        Returns:
            True if cache is valid, False otherwise
        """
        entry = self.load(phase_name)
        if entry is None:
            return False

        return entry.content_hash == current_hash

    def clear(self, phase_name: str) -> None:
        """Clear cache for a specific phase.

        Args:
            phase_name: Name of the phase to clear
        """
        cache_file = self.get_cache_file(phase_name)
        if cache_file.exists():
            cache_file.unlink()

    def clear_all(self) -> None:
        """Clear all cached results for this session."""
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()
