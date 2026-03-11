"""Standard Motorola QCII tone table (Hz)."""

# Frequencies in Hz commonly used in Motorola Quick Call II tone paging.
# Source: public QCII documentation and widely published tone tables.
QCII_FREQUENCIES_HZ = [
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

def nearest_standard(freq_hz: float, tolerance_pct: float = 1.5) -> float | None:
    """Return the nearest standard QCII frequency within tolerance, else None."""
    closest = min(QCII_FREQUENCIES_HZ, key=lambda f: abs(f - freq_hz))
    if abs(closest - freq_hz) / closest * 100 <= tolerance_pct:
        return closest
    return None
