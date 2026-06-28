"""Invisible watermark desktop app and command line toolkit."""

import warnings

try:  # Anaconda may emit these through transitive document/PDF dependencies.
    from cryptography.utils import CryptographyDeprecationWarning

    warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)
except Exception:  # pragma: no cover - cryptography is not a direct app dependency.
    pass

__version__ = "0.2.3"
