from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    list_price: int  # cents
    high_demand: bool = False
    tags: tuple = ()


CATALOG = {
    p.sku: p
    for p in [
        Product("MM-101", "Lab Goggles", 2400, tags=("safety", "eyewear")),
        Product("MM-102", "Nitrile Gloves (100)", 1800, high_demand=True, tags=("safety", "gloves")),
        Product("MM-103", "Beaker Set", 3200, tags=("glassware",)),
        Product("MM-104", "Hazard Label Pack", 900, high_demand=True, tags=("safety", "labels")),
        Product("MM-105", "Fume Hood Filter", 5600, tags=("ventilation",)),
    ]
}


def get(sku: str) -> Product | None:
    return CATALOG.get(sku)
