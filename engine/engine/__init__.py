"""Synesis negotiation engine.

Agents advocate for their own user's requirements, argue, concede with reasons, and
emit a workplan. Nothing in here touches the filesystem or git — writes go through the
ToolHost the caller injects (see engine.tools), which the server implements as a
sandboxed workspace.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
