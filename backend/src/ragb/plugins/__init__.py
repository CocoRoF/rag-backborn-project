"""Importing this package registers every shipped plug-in.

Adding one is a single file plus a `register(...)` call — no wiring elsewhere, which is the
point of the registry. A deployment that needs a 특허 API connector writes a SOURCE plug-in
and drops it here.
"""
from ragb.plugins import enrich, exporters, matching, scoring, sources  # noqa: F401
from ragb.plugins.base import (
                               KIND_LABEL,
                               ConfigField,
                               Plugin,
                               PluginError,
                               PluginKind,
                               PluginSpec,
                               RunContext,
                               RunResult,
                               catalog,
                               get,
                               loaded,
                               register,
)

__all__ = ["Plugin", "PluginSpec", "PluginKind", "PluginError", "ConfigField", "RunContext",
           "RunResult", "register", "get", "catalog", "loaded", "KIND_LABEL"]
