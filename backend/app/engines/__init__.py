"""Adapters that compose the five engines into one control plane.

Each engine is imported and invoked through its real public interface; none of
its source is copied. The :class:`EngineHub` owns one instance of each and
exposes the high-level operations the service layer needs.
"""

from .hub import EngineHub

__all__ = ["EngineHub"]
