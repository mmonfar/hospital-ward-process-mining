"""Design system: the token source of truth and the checks that keep it honest.

SPEC-005, node N14a. An adapter-layer package -- it emits static assets for
`web/` and imports nothing from the domain.
"""

from hwpm.design import colour, emit, tokens

__all__ = ["colour", "emit", "tokens"]
