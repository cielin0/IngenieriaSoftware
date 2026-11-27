"""Utilidades geográficas sin dependencias de Django.

El límite de Chile es un polígono simplificado con ~0,1° (≈10 km) de holgura hacia el
lado argentino/boliviano; sirve para rechazar reportes claramente fuera del país. Si se
necesita precisión de frontera, reemplázalo por el GeoJSON oficial.
Coordenadas en (lat, lng).
"""
import math

# Anillo: costa norte -> frontera con Perú -> frontera este (norte a sur) -> sur -> océano.
CHILE_POLYGON = [
    (-18.35, -76.0), (-18.35, -70.40), (-17.45, -69.45),
    (-18.30, -69.05), (-19.00, -68.75), (-20.00, -68.40), (-21.20, -68.10),
    (-22.00, -67.95), (-22.95, -67.00), (-23.70, -67.15), (-24.30, -68.15),
    (-25.00, -68.45), (-26.00, -68.45), (-27.00, -68.25), (-28.00, -69.00),
    (-29.00, -69.85), (-30.20, -69.75), (-31.50, -70.10), (-32.80, -70.00),
    (-34.20, -69.85), (-35.00, -70.30), (-36.00, -70.40), (-37.00, -70.90),
    (-38.00, -71.00), (-38.65, -71.00), (-39.00, -71.40), (-40.00, -71.65),
    (-40.70, -71.80), (-41.50, -71.70), (-42.50, -71.80), (-43.50, -71.85),
    (-44.50, -71.70), (-45.50, -71.55), (-46.50, -71.55), (-47.00, -72.00),
    (-48.00, -72.70), (-49.00, -72.95), (-50.00, -72.90), (-51.00, -72.15),
    (-51.60, -72.15), (-52.05, -71.50), (-52.15, -69.40), (-52.45, -68.30),
    (-52.65, -68.50), (-54.87, -68.50), (-54.87, -66.20), (-56.70, -66.20),
    (-56.70, -76.00),
]
# Territorios insulares: (sur, oeste, norte, este)
CHILE_ISLAND_BOXES = [
    (-34.10, -80.90, -33.50, -78.50),   # Archipiélago Juan Fernández
    (-27.30, -109.60, -26.90, -109.10),  # Rapa Nui
    (-26.60, -105.50, -26.40, -105.30),  # Isla Salas y Gómez
    (-26.50, -80.30, -26.20, -79.70),    # Islas Desventuradas
]


def point_in_polygon(lat, lng, polygon):
    inside = False
    count = len(polygon)
    for index in range(count):
        lat1, lng1 = polygon[index]
        lat2, lng2 = polygon[(index + 1) % count]
        if (lat1 > lat) != (lat2 > lat):
            crossing = lng1 + (lat - lat1) * (lng2 - lng1) / (lat2 - lat1)
            if lng < crossing:
                inside = not inside
    return inside


def in_chile(lat, lng):
    if point_in_polygon(lat, lng, CHILE_POLYGON):
        return True
    return any(s <= lat <= n and w <= lng <= e for s, w, n, e in CHILE_ISLAND_BOXES)


def haversine_m(lat1, lng1, lat2, lng2):
    radius = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))
