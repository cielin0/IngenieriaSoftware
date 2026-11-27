import math
from datetime import timedelta

from django.db import migrations

# (slug, nombre, lat, lng) de norte a sur. Se asigna cada incidencia a la comunidad
# cuyo centro esté más cerca; se pueden añadir comunidades más finas desde el admin.
COMMUNITIES = [
    ("arica-y-parinacota", "Arica y Parinacota", -18.4783, -70.3126),
    ("tarapaca", "Tarapacá", -20.2133, -70.1503),
    ("antofagasta", "Antofagasta", -23.6509, -70.3975),
    ("atacama", "Atacama", -27.3668, -70.3314),
    ("coquimbo", "Coquimbo", -29.9027, -71.2519),
    ("valparaiso", "Valparaíso", -33.0472, -71.6127),
    ("metropolitana", "Metropolitana de Santiago", -33.4489, -70.6693),
    ("ohiggins", "O'Higgins", -34.1708, -70.7444),
    ("maule", "Maule", -35.4264, -71.6554),
    ("nuble", "Ñuble", -36.6063, -72.1034),
    ("biobio", "Biobío", -36.8201, -73.0444),
    ("araucania", "La Araucanía", -38.7359, -72.5904),
    ("los-rios", "Los Ríos", -39.8142, -73.2459),
    ("los-lagos", "Los Lagos", -41.4689, -72.9411),
    ("aysen", "Aysén", -45.5712, -72.0685),
    ("magallanes", "Magallanes y la Antártica Chilena", -53.1638, -70.9171),
]
TTL_DAYS = {
    "Derrumbe en ruta": 14,
    "Accidente vial": 2,
    "Problema de conectividad": 7,
    "Emergencia climática": 5,
}


def haversine_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 12_742_000 * math.asin(math.sqrt(a))


def seed_and_backfill(apps, schema_editor):
    Community = apps.get_model("incidents", "Community")
    Incident = apps.get_model("incidents", "Incident")
    created = []
    for order, (slug, name, lat, lng) in enumerate(COMMUNITIES):
        community, _ = Community.objects.get_or_create(
            slug=slug, defaults={"name": name, "lat": lat, "lng": lng, "order": order}
        )
        created.append(community)
    for incident in Incident.objects.all():
        changed = []
        if incident.community_id is None and created:
            nearest = min(created, key=lambda c: haversine_m(incident.lat, incident.lng, c.lat, c.lng))
            incident.community = nearest
            changed.append("community")
        if incident.expires_at is None:
            incident.expires_at = incident.time + timedelta(days=TTL_DAYS.get(incident.type, 7))
            changed.append("expires_at")
        if changed:
            incident.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ('incidents', '0003_veracity_communities_reputation'),
    ]

    operations = [
        migrations.RunPython(seed_and_backfill, migrations.RunPython.noop),
    ]
