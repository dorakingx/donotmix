"""'Customers also bought' widget."""

from .catalog import get
from .flags import Features
from .search import search


def also_bought(sku: str, features: Features, limit: int = 3) -> list[str]:
    if not features.on("recommendation"):
        return []
    product = get(sku)
    if product is None:
        return []
    hits = []
    for tag in product.tags:
        for other in search(tag, features):
            p = get(other)
            if p and p.sku != sku and p.sku not in hits:
                hits.append(p.sku)
    return hits[:limit]
