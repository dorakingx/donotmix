"""Product search. ai_search swaps in a 'semantic' index."""

from .catalog import CATALOG
from .flags import Features


def search(query: str, features: Features) -> list[str]:
    q = query.lower()
    if features.on("ai_search"):
        # semantic index stores normalised ids
        return [sku.lower() for sku, p in CATALOG.items() if q in p.name.lower() or any(q in t for t in p.tags)]
    return [sku for sku, p in CATALOG.items() if q in p.name.lower() or q in p.tags]
