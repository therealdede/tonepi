"""Standard Motorola QCII tone tables (Hz), FDMA and TDMA sets."""

# Sources: Motorola paging tone references ("FDMA" / "TDMA" tables commonly cited
# in programming guides). Many deployments use identical tables; we keep both
# named sets so operators can select explicitly.

FDMA_TONES_HZ = [
    306.9,
    330.5,
    349.0,
    368.5,
    389.0,
    412.1,
    433.7,
    457.9,
    483.5,
    510.5,
    539.0,
    569.1,
    600.9,
    634.5,
    669.9,
    707.3,
    746.8,
    788.5,
    832.5,
    879.0,
    928.1,
    979.9,
    1034.7,
    1092.4,
    1153.2,
    1217.8,
    1285.8,
    1357.6,
    1433.4,
    1513.1,
    1597.0,
    1685.3,
    1778.2,
    1875.6,
]

# TDMA tone set (often matches FDMA; defined separately for clarity/selection).
TDMA_TONES_HZ = [
    306.9,
    330.5,
    349.0,
    368.5,
    389.0,
    412.1,
    433.7,
    457.9,
    483.5,
    510.5,
    539.0,
    569.1,
    600.9,
    634.5,
    669.9,
    707.3,
    746.8,
    788.5,
    832.5,
    879.0,
    928.1,
    979.9,
    1034.7,
    1092.4,
    1153.2,
    1217.8,
    1285.8,
    1357.6,
    1433.4,
    1513.1,
    1597.0,
    1685.3,
    1778.2,
    1875.6,
]

QCII_TONE_SETS = {
    "fdma": FDMA_TONES_HZ,
    "tdma": TDMA_TONES_HZ,
}


def get_tone_set(name: str = "fdma") -> list[float]:
    """Return the requested tone set or raise for unknown names."""
    key = name.lower()
    if key not in QCII_TONE_SETS:
        raise ValueError(f"Unknown tone set: {name}. Valid sets: {', '.join(sorted(QCII_TONE_SETS))}")
    return QCII_TONE_SETS[key]


def nearest_standard(freq_hz: float, tolerance_pct: float = 1.5, tone_set: str = "fdma") -> float | None:
    """Return nearest standard QCII frequency within tolerance for the given set, else None."""
    table = get_tone_set(tone_set)
    closest = min(table, key=lambda f: abs(f - freq_hz))
    if abs(closest - freq_hz) / closest * 100 <= tolerance_pct:
        return closest
    return None
