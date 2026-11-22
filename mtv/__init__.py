"""mtv, the monkey televisor: a web console for 10km loops.

Watches loops whose files it can read (a local folder or a volume shared with
the loop's container). Standard library only.
"""

__version__ = '0.1.0'

# loop.db PRAGMA user_version values this release understands.
SCHEMA_VERSIONS = (1, 2)
