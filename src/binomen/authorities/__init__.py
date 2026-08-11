"""Authority plugins.

Adding a source is meant to be a small, well-defined job -- see docs/EXTENDING.md.
Implement `Authority`, register it in `REGISTRY`, and the resolver will consult
it for the codes it declares. Nothing else in the package needs to change.
"""

from __future__ import annotations

from . import gbif, hgnc, ictv, lpsn, mycobank  # noqa: F401  (registration side effects)
from .base import REGISTRY, Authority, AuthorityResult, authorities_for, register  # noqa: F401
