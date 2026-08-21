"""Parse GitHub PR URLs out of Slack message text."""

from __future__ import annotations

import re
from typing import Optional

from prreviewer.models import PRRef


class PRLinkParser:
    """Extract GitHub pull-request references from arbitrary text."""

    PATTERN = re.compile(
        r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/pull/(?P<number>\d+)"
    )

    def parse(self, text: str) -> Optional[PRRef]:
        """Return the first GitHub PR reference found in *text*, or None."""
        refs = self.parse_all(text)
        return refs[0] if refs else None

    def parse_all(self, text: str) -> list[PRRef]:
        """Return all unique GitHub PR references found in *text*."""
        seen: set[str] = set()
        results: list[PRRef] = []
        for m in self.PATTERN.finditer(text):
            ref = PRRef(
                owner=m.group("owner"),
                repo=m.group("repo"),
                number=int(m.group("number")),
            )
            if ref.id not in seen:
                seen.add(ref.id)
                results.append(ref)
        return results
