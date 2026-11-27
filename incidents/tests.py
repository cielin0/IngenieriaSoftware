import hashlib
import json
import tempfile
from pathlib import Path

from datetime import timedelta

from django.contrib.staticfiles.finders import find
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import geo, rules
from .models import Community, Flag, Incident, Notification, User, Vote


class IncidentAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(
            email="admin@example.test",
            name="Admin",
            password="admin-password-strong",
            role=User.Role.ADMINISTRATOR,
        )
        self.citizen = User.objects.create_user(
            email="citizen@example.test",
            name="Ciudadana",
            password="citizen-password-strong",
        )
        self.operator = User.objects.create_user(
            email="operator@example.test",
            name="Operador",
            password="operator-password-strong",
            role=User.Role.OPERATOR,
        )

    def request_json(self, path, method="get", data=None, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return getattr(self.client, method)(
            path,
            data=json.dumps(data) if data is not None else None,
            content_type="application/json",
            **headers,
        )

    def login(self, email, password):
        response = self.request_json(
            "/api/auth/login",
            "post",
            {"email": email, "password": password},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def test_homepage_renders_django_static_assets(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/static/map.js?v=7")
        self.assertContains(response, "/static/app.js?v=9")
        self.assertIsNotNone(find("map.js"))
        self.assertIsNotNone(find("app.js"))
        self.assertIsNotNone(find("styles.css"))

    def test_admin_panel_and_admin_incident_endpoint_are_restricted(self):
        response = self.client.get("/administracion/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Panel administrativo")

        operator_token = self.login("operator@example.test", "operator-password-strong")
        denied = self.request_json("/api/admin/incidents", token=operator_token)
        self.assertEqual(denied.status_code, 403)

        admin_token = self.login("admin@example.test", "admin-password-strong")
        allowed = self.request_json("/api/admin/incidents", token=admin_token)
        self.assertEqual(allowed.status_code, 200)

    def test_authentication_permissions_and_incident_lifecycle(self):
        registration = self.request_json(
            "/api/auth/register",
            "post",
            {
                "name": "Nueva Persona",
                "email": "new@example.test",
                "password": "new-password-strong",
                "role": "administrador",
            },
        )
        self.assertEqual(registration.status_code, 201)
        self.assertTrue(User.objects.filter(email="new@example.test").exists())
        self.assertEqual(registration.json()["user"]["role"], "ciudadano")
        self.assertEqual(
            self.request_json("/api/auth/me", token=registration.json()["token"]).status_code,
            200,
        )
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            self.request_json(
                "/api/incidents",
                "post",
                {"type": "Accidente vial", "description": "Sin sesión", "lat": -41.5, "lng": -72.9},
            ).status_code,
            401,
        )

        citizen_token = self.login("citizen@example.test", "citizen-password-strong")
        created = self.request_json(
            "/api/incidents",
            "post",
            {
                "type": "Accidente vial",
                "description": "Reporte de prueba",
                "lat": -41.5,
                "lng": -72.9,
            },
            citizen_token,
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["status"], "abierto")

        escalation = self.request_json(
            f"/api/incidents/{created.json()['id']}",
            "put",
            {"status": "resuelto"},
            citizen_token,
        )
        self.assertEqual(escalation.status_code, 403)

        operator_token = self.login("operator@example.test", "operator-password-strong")
        status_update = self.request_json(
            f"/api/incidents/{created.json()['id']}",
            "put",
            {"status": "resuelto", "priority": "alta"},
            operator_token,
        )
        self.assertEqual(status_update.status_code, 200)
        self.assertIsNotNone(status_update.json()["resolvedAt"])
        self.assertGreaterEqual(len(status_update.json()["history"]), 3)

        comment = self.request_json(
            f"/api/incidents/{created.json()['id']}",
            "put",
            {"comment": "Equipo enviado al lugar."},
            operator_token,
        )
        self.assertEqual(len(comment.json()["comments"]), 1)
        self.assertEqual(
            self.request_json("/api/admin/stats", token=operator_token).json()["resolved"],
            1,
        )
        self.assertEqual(
            len(self.request_json("/api/notifications", token=citizen_token).json()),
            2,
        )
        self.assertEqual(self.request_json("/api/docs").status_code, 200)

    def test_role_changes_and_administrator_deletion(self):
        operator_token = self.login("operator@example.test", "operator-password-strong")
        denied = self.request_json(
            f"/api/users/{self.citizen.pk}/role",
            "put",
            {"role": "operador"},
            operator_token,
        )
        self.assertEqual(denied.status_code, 403)

        self.request_json(
            "/api/incidents",
            "post",
            {
                "type": "Accidente vial",
                "description": "Reporte de prueba",
                "lat": -41.5,
                "lng": -72.9,
            },
            self.login("citizen@example.test", "citizen-password-strong"),
        )
        incident = Incident.objects.get()
        admin_token = self.login("admin@example.test", "admin-password-strong")
        deleted = self.request_json(
            f"/api/incidents/{incident.pk}",
            "delete",
            token=admin_token,
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Incident.objects.exists())

    def test_invalid_json_and_coordinates_return_client_errors(self):
        invalid_json = self.client.post(
            "/api/auth/register",
            data="{",
            content_type="application/json",
        )
        self.assertEqual(invalid_json.status_code, 400)
        token = self.login("citizen@example.test", "citizen-password-strong")
        invalid_coordinates = self.request_json(
            "/api/incidents",
            "post",
            {
                "type": "Accidente vial",
                "description": "Reporte de prueba",
                "lat": float("nan"),
                "lng": -72.9,
            },
            token,
        )
        self.assertEqual(invalid_coordinates.status_code, 400)

    def test_notification_read_endpoint_marks_only_current_users_records(self):
        incident = Incident.objects.create(
            id="incident-test",
            type="Accidente vial",
            description="Prueba",
            lat=-41.5,
            lng=-72.9,
        )
        Notification.objects.create(user=self.citizen, incident=incident, message="Actualización")
        token = self.login("citizen@example.test", "citizen-password-strong")
        response = self.request_json("/api/notifications/read", "put", token=token)
        self.assertEqual(response.json(), {"updated": True})
        self.assertTrue(Notification.objects.get().read)

    def test_legacy_json_import_preserves_accounts_and_is_repeatable(self):
        password = "legacy-password-strong"
        salt = "0123456789abcdef0123456789abcdef"
        password_hash = hashlib.scrypt(
            password.encode(),
            salt=salt.encode(),
            n=16384,
            r=8,
            p=1,
            dklen=64,
        ).hex()
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "users.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "legacy-user",
                            "name": "Cuenta heredada",
                            "email": "legacy@example.test",
                            "role": "ciudadano",
                            "passwordHash": f"{salt}:{password_hash}",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (data_dir / "incidents.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "legacy-incident",
                            "type": "Otra",
                            "description": "Incidencia conservada",
                            "lat": -41.5,
                            "lng": -72.9,
                            "time": "2026-09-29T09:10:00-03:00",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            call_command("import_legacy_data", data_dir=directory, verbosity=0)
            call_command("import_legacy_data", data_dir=directory, verbosity=0)

        self.assertEqual(Incident.objects.filter(pk="legacy-incident").count(), 1)
        token = self.login("legacy@example.test", password)
        self.assertTrue(token)
        imported_user = User.objects.get(pk="legacy-user")
        self.assertTrue(imported_user.password.startswith("pbkdf2_sha256$"))


class MapTilesReferrerPolicyTests(TestCase):
    def test_home_sends_referer_origin_to_tile_servers(self):
        """OpenStreetMap devuelve 403 si el navegador no envía Referer."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")


class ApiTestCase(TestCase):
    """Utilidades comunes para las pruebas de las funciones nuevas."""

    PASSWORD = "contraseña-muy-segura-1"

    def setUp(self):
        cache.clear()
        self.admin = self.make_user("admin@example.test", "Admin", User.Role.ADMINISTRATOR)
        self.operator = self.make_user("op@example.test", "Operadora", User.Role.OPERATOR)
        self.author = self.make_user("autor@example.test", "Autor")

    def make_user(self, email, name, role=User.Role.CITIZEN):
        return User.objects.create_user(email=email, name=name, password=self.PASSWORD, role=role)

    def call(self, path, method="get", data=None, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return getattr(self.client, method)(
            path,
            data=json.dumps(data) if data is not None else None,
            content_type="application/json",
            **headers,
        )

    def token_for(self, user):
        response = self.call("/api/auth/login", "post", {"email": user.email, "password": self.PASSWORD})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["token"]

    def create_incident(self, user=None, **overrides):
        data = {
            "type": "Derrumbe en ruta",
            "description": "Caída de tierra en la ruta",
            "lat": -41.47,
            "lng": -72.94,
            "confirmDistinct": True,
        }
        data.update(overrides)
        response = self.call("/api/incidents", "post", data, self.token_for(user or self.author))
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def voters(self, count):
        return [self.make_user(f"votante{i}@example.test", f"Votante {i}") for i in range(count)]


class OrderingAndVisibilityTests(ApiTestCase):
    def test_list_is_newest_first(self):
        old = self.create_incident(description="vieja", lat=-41.5, lng=-72.9)
        Incident.objects.filter(pk=old["id"]).update(time=timezone.now() - timedelta(hours=5))
        self.create_incident(description="nueva", lat=-41.6, lng=-72.8)
        listing = self.call("/api/incidents").json()
        self.assertEqual([item["description"] for item in listing], ["nueva", "vieja"])

    def test_two_incidents_with_same_coordinates_are_both_returned_with_distinct_ids(self):
        first = self.create_incident(description="uno")
        second = self.create_incident(description="dos")
        listing = self.call("/api/incidents").json()
        self.assertEqual({item["id"] for item in listing}, {first["id"], second["id"]})
        self.assertEqual(len({(i["lat"], i["lng"]) for i in listing}), 1)


class ExpiryTests(ApiTestCase):
    def test_new_incident_gets_expiry_from_its_type(self):
        created = self.create_incident(type="Corte de luz")
        incident = Incident.objects.get(pk=created["id"])
        delta = incident.expires_at - incident.time
        self.assertAlmostEqual(delta.total_seconds(), 3 * 86400, delta=5)

    def test_expired_incidents_are_hidden_but_operators_can_include_them(self):
        created = self.create_incident()
        Incident.objects.filter(pk=created["id"]).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.call("/api/incidents").json(), [])
        operator_token = self.token_for(self.operator)
        self.assertEqual(self.call("/api/incidents", token=operator_token).json(), [])
        everything = self.call("/api/incidents?include=all", token=operator_token).json()
        self.assertEqual(len(everything), 1)
        self.assertTrue(everything[0]["expired"])

    def test_citizens_cannot_use_include_all(self):
        created = self.create_incident()
        Incident.objects.filter(pk=created["id"]).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.call("/api/incidents?include=all", token=self.token_for(self.author)).json(), [])

    def test_confirmation_extends_expiry(self):
        created = self.create_incident()
        soon = timezone.now() + timedelta(hours=1)
        Incident.objects.filter(pk=created["id"]).update(expires_at=soon)
        voter = self.voters(1)[0]
        self.call(f"/api/incidents/{created['id']}/vote", "post", {"value": "confirmar"}, self.token_for(voter))
        self.assertGreater(Incident.objects.get(pk=created["id"]).expires_at, soon + timedelta(days=1))

    def test_purge_command_removes_long_expired_incidents(self):
        created = self.create_incident()
        Incident.objects.filter(pk=created["id"]).update(expires_at=timezone.now() - timedelta(days=40))
        call_command("purge_expired_incidents", verbosity=0)
        self.assertFalse(Incident.objects.exists())


class OwnerDeletionTests(ApiTestCase):
    def test_author_can_delete_own_incident_others_cannot(self):
        created = self.create_incident()
        stranger = self.make_user("otro@example.test", "Otro")
        self.assertEqual(
            self.call(f"/api/incidents/{created['id']}", "delete", token=self.token_for(stranger)).status_code, 403
        )
        self.assertEqual(
            self.call(f"/api/incidents/{created['id']}", "delete", token=self.token_for(self.operator)).status_code, 403
        )
        self.assertEqual(
            self.call(f"/api/incidents/{created['id']}", "delete", token=self.token_for(self.author)).status_code, 200
        )
        self.assertFalse(Incident.objects.exists())

    def test_administrator_can_still_delete_any_incident(self):
        created = self.create_incident()
        response = self.call(f"/api/incidents/{created['id']}", "delete", token=self.token_for(self.admin))
        self.assertEqual(response.status_code, 200)

    def test_author_cannot_change_type_or_location_after_receiving_votes(self):
        created = self.create_incident()
        voter = self.voters(1)[0]
        self.call(f"/api/incidents/{created['id']}/vote", "post", {"value": "confirmar"}, self.token_for(voter))
        author_token = self.token_for(self.author)
        blocked = self.call(f"/api/incidents/{created['id']}", "put", {"lat": -33.4, "lng": -70.6}, author_token)
        self.assertEqual(blocked.status_code, 403)
        allowed = self.call(f"/api/incidents/{created['id']}", "put", {"description": "Más detalle"}, author_token)
        self.assertEqual(allowed.status_code, 200)


class EditTests(ApiTestCase):
    def test_changing_type_recalculates_expiry(self):
        created = self.create_incident(type="Derrumbe en ruta")
        response = self.call(
            f"/api/incidents/{created['id']}", "put", {"type": "Corte de luz"}, self.token_for(self.author)
        )
        self.assertEqual(response.status_code, 200, response.content)
        incident = Incident.objects.get(pk=created["id"])
        self.assertAlmostEqual((incident.expires_at - incident.time).total_seconds(), 3 * 86400, delta=5)


class PasswordRecoveryTests(ApiTestCase):
    def request_reset(self, email):
        return self.call("/api/auth/password-reset/request", "post", {"email": email})

    def token_from_mail(self):
        body = mail.outbox[-1].body
        return body.split("#reset=")[1].split()[0]

    def test_request_response_is_identical_for_known_and_unknown_emails(self):
        known = self.request_reset(self.author.email)
        unknown = self.request_reset("nadie@example.test")
        self.assertEqual(known.status_code, 202)
        self.assertEqual(unknown.status_code, 202)
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.author.email])

    def test_reset_flow_changes_password_once_and_closes_sessions(self):
        old_token = self.token_for(self.author)
        self.request_reset(self.author.email)
        token = self.token_from_mail()
        empty = self.call("/api/auth/password-reset/confirm", "post", {"token": token, "password": ""})
        self.assertEqual(empty.status_code, 400)
        done = self.call("/api/auth/password-reset/confirm", "post", {"token": token, "password": "123"})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(self.call("/api/auth/me", token=old_token).status_code, 401)
        reused = self.call("/api/auth/password-reset/confirm", "post", {"token": token, "password": "tercera-clave-larga-1"})
        self.assertEqual(reused.status_code, 400)
        login = self.call("/api/auth/login", "post", {"email": self.author.email, "password": "123"})
        self.assertEqual(login.status_code, 200)

    def test_expired_or_invalid_token_is_rejected(self):
        self.request_reset(self.author.email)
        token = self.token_from_mail()
        from .models import PasswordResetToken

        PasswordResetToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        expired = self.call("/api/auth/password-reset/confirm", "post", {"token": token, "password": "otra-clave-larga-99"})
        self.assertEqual(expired.status_code, 400)
        garbage = self.call("/api/auth/password-reset/confirm", "post", {"token": "x" * 40, "password": "otra-clave-larga-99"})
        self.assertEqual(garbage.status_code, 400)

    def test_registration_creates_account_and_existing_email_is_rejected(self):
        payload = {"name": "Copia", "password": "clave-larga-de-prueba"}
        existing = self.call("/api/auth/register", "post", {**payload, "email": self.author.email})
        fresh = self.call("/api/auth/register", "post", {**payload, "email": "nuevo@example.test"})
        self.assertEqual(existing.status_code, 409)
        self.assertEqual(fresh.status_code, 201)
        self.assertEqual(User.objects.filter(email=self.author.email).count(), 1)
        self.assertEqual(User.objects.filter(email="nuevo@example.test").count(), 1)
        self.assertEqual(mail.outbox, [])

    def test_login_error_is_the_requested_generic_message(self):
        response = self.call(
            "/api/auth/login",
            "post",
            {"email": "todavia-no-activada@example.test", "password": "clave-larga-de-prueba"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["message"], "Correo o contraseña incorrectos.")

    def test_registration_accepts_short_numeric_password_and_starts_session(self):
        empty = self.call(
            "/api/auth/register",
            "post",
            {"name": "Prueba", "email": "vacio@example.test", "password": ""},
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

        registration = self.call(
            "/api/auth/register",
            "post",
            {"name": "Prueba", "email": "simple@example.test", "password": "123"},
        )
        self.assertEqual(registration.status_code, 201)
        self.assertTrue(User.objects.filter(email="simple@example.test").exists())
        self.assertEqual(self.call("/api/auth/me", token=registration.json()["token"]).status_code, 200)
        self.assertEqual(mail.outbox, [])
        login = self.call("/api/auth/login", "post", {"email": "simple@example.test", "password": "123"})
        self.assertEqual(login.status_code, 200)

    def test_login_is_throttled_after_repeated_attempts(self):
        for _ in range(10):
            self.call("/api/auth/login", "post", {"email": self.author.email, "password": "incorrecta-123"})
        blocked = self.call("/api/auth/login", "post", {"email": self.author.email, "password": self.PASSWORD})
        self.assertEqual(blocked.status_code, 429)


class VeracityAndReputationTests(ApiTestCase):
    def vote(self, incident_id, user, value):
        return self.call(f"/api/incidents/{incident_id}/vote", "post", {"value": value}, self.token_for(user))

    def test_new_incident_is_unverified(self):
        self.assertEqual(self.create_incident()["veracity"], rules.UNVERIFIED)

    def test_five_confirmations_confirm_it_notify_author_and_raise_reputation(self):
        created = self.create_incident()
        voters = self.voters(5)
        for voter in voters[:4]:
            self.assertEqual(self.vote(created["id"], voter, "confirmar").json()["veracity"], rules.UNVERIFIED)
        response = self.vote(created["id"], voters[4], "confirmar")
        self.assertEqual(response.json()["veracity"], rules.COMMUNITY_CONFIRMED)
        self.author.refresh_from_db()
        self.assertEqual(self.author.reputation, rules.REPUTATION_BY_VERACITY[rules.COMMUNITY_CONFIRMED])
        messages = [n.message for n in Notification.objects.filter(user=self.author)]
        self.assertTrue(any("Confirmado por la comunidad" in message for message in messages))

    def test_denials_mark_it_probably_false_and_lower_reputation(self):
        created = self.create_incident()
        for voter in self.voters(3):
            self.vote(created["id"], voter, "negar")
        self.assertEqual(Incident.objects.get(pk=created["id"]).veracity, rules.PROBABLY_FALSE)
        self.author.refresh_from_db()
        self.assertEqual(self.author.reputation, -5)

    def test_changing_or_retracting_votes_can_revert_the_automatic_state(self):
        created = self.create_incident()
        voters = self.voters(5)
        for voter in voters:
            self.vote(created["id"], voter, "confirmar")
        self.assertEqual(Incident.objects.get(pk=created["id"]).veracity, rules.COMMUNITY_CONFIRMED)
        response = self.call(f"/api/incidents/{created['id']}/vote", "delete", token=self.token_for(voters[0]))
        self.assertEqual(response.json()["veracity"], rules.UNVERIFIED)
        self.author.refresh_from_db()
        self.assertEqual(self.author.reputation, 0)

    def test_cannot_vote_own_incident_and_votes_are_one_per_user(self):
        created = self.create_incident()
        self.assertEqual(self.vote(created["id"], self.author, "confirmar").status_code, 403)
        voter = self.voters(1)[0]
        self.vote(created["id"], voter, "confirmar")
        self.vote(created["id"], voter, "negar")
        self.assertEqual(Vote.objects.filter(incident_id=created["id"]).count(), 1)
        self.assertEqual(Vote.objects.get().value, rules.VOTE_DENY)
        self.assertEqual(self.vote(created["id"], voter, "tal vez").status_code, 400)

    def test_high_reputation_votes_weigh_more(self):
        created = self.create_incident()
        trusted = self.voters(3)
        for person in trusted:
            person.reputation = 50  # peso 2.0
            person.save(update_fields=["reputation"])
        for person in trusted:
            self.vote(created["id"], person, "confirmar")
        # 3 personas x peso 2.0 = 6.0 >= 5.0 y 3 votantes distintos
        self.assertEqual(Incident.objects.get(pk=created["id"]).veracity, rules.COMMUNITY_CONFIRMED)

    def test_low_reputation_votes_weigh_less(self):
        created = self.create_incident()
        doubtful = self.voters(5)
        for person in doubtful:
            person.reputation = -25  # peso 0.5
            person.save(update_fields=["reputation"])
        for person in doubtful:
            self.vote(created["id"], person, "confirmar")
        # 5 x 0.5 = 2.5 < 5.0: no alcanza
        self.assertEqual(Incident.objects.get(pk=created["id"]).veracity, rules.UNVERIFIED)

    def test_operator_can_verify_or_discard_and_votes_no_longer_change_it(self):
        created = self.create_incident()
        operator_token = self.token_for(self.operator)
        response = self.call(f"/api/incidents/{created['id']}", "put", {"veracity": "falso"}, operator_token)
        self.assertEqual(response.json()["veracity"], rules.FALSE)
        self.author.refresh_from_db()
        self.assertEqual(self.author.reputation, -15)
        self.assertEqual(self.call("/api/incidents").json(), [])  # el público ya no la ve
        self.assertEqual(len(self.call("/api/incidents", token=self.token_for(self.author)).json()), 1)
        for voter in self.voters(5):
            self.vote(created["id"], voter, "confirmar")
        self.assertEqual(Incident.objects.get(pk=created["id"]).veracity, rules.FALSE)
        invalid = self.call(f"/api/incidents/{created['id']}", "put", {"veracity": "confirmado_comunidad"}, operator_token)
        self.assertEqual(invalid.status_code, 400)

    def test_operator_veracity_change_notifies_the_author(self):
        created = self.create_incident()
        self.call(f"/api/incidents/{created['id']}", "put", {"veracity": "verificado"}, self.token_for(self.operator))
        notifications = self.call("/api/notifications", token=self.token_for(self.author)).json()
        self.assertTrue(any("Verificado por un operador" in item["message"] for item in notifications))

    def test_veracity_filter_endpoint(self):
        first = self.create_incident(description="a", lat=-41.5, lng=-72.9)
        self.create_incident(description="b", lat=-41.9, lng=-72.5)
        self.call(f"/api/incidents/{first['id']}", "put", {"veracity": "verificado"}, self.token_for(self.operator))
        verified = self.call("/api/incidents?veracity=verificado").json()
        self.assertEqual([item["description"] for item in verified], ["a"])


class FlagTests(ApiTestCase):
    def flag(self, incident_id, user, reason="broma"):
        return self.call(f"/api/incidents/{incident_id}/flag", "post", {"reason": reason}, self.token_for(user))

    def test_three_flags_hide_the_incident_and_penalize_the_author(self):
        created = self.create_incident()
        flaggers = self.voters(3)
        self.assertEqual(self.flag(created["id"], flaggers[0]).status_code, 200)
        self.flag(created["id"], flaggers[1])
        response = self.flag(created["id"], flaggers[2])
        self.assertTrue(response.json()["hidden"])
        self.assertEqual(self.call("/api/incidents").json(), [])
        self.author.refresh_from_db()
        self.assertEqual(self.author.reputation, -rules.HIDE_PENALTY)
        self.assertTrue(Notification.objects.filter(user=self.author, message__contains="ocultada").exists())

    def test_one_flag_per_person_and_not_on_own_incident(self):
        created = self.create_incident()
        flagger = self.voters(1)[0]
        for _ in range(4):
            self.flag(created["id"], flagger)
        self.assertEqual(Flag.objects.count(), 1)
        self.assertTrue(self.call("/api/incidents").json())  # sigue visible
        self.assertEqual(self.flag(created["id"], self.author).status_code, 403)
        self.assertEqual(self.flag(created["id"], flagger, reason="otra").status_code, 400)

    def test_operator_can_restore_and_reputation_is_refunded(self):
        created = self.create_incident()
        for flagger in self.voters(3):
            self.flag(created["id"], flagger)
        operator_token = self.token_for(self.operator)
        hidden = self.call("/api/incidents?include=all", token=operator_token).json()
        self.assertTrue(hidden[0]["hidden"])
        restored = self.call(f"/api/incidents/{created['id']}", "put", {"hidden": False}, operator_token)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(len(self.call("/api/incidents").json()), 1)
        self.author.refresh_from_db()
        self.assertEqual(self.author.reputation, 0)
        self.assertEqual(Flag.objects.count(), 0)


class DuplicateDetectionTests(ApiTestCase):
    def post_incident(self, **overrides):
        data = {"type": "Derrumbe en ruta", "description": "Derrumbe", "lat": -41.4700, "lng": -72.9400}
        data.update(overrides)
        return self.call("/api/incidents", "post", data, self.token_for(self.make_or_get("rep@example.test")))

    def make_or_get(self, email):
        return User.objects.filter(email=email).first() or self.make_user(email, "Reportante")

    def test_similar_incident_within_radius_asks_for_confirmation(self):
        self.create_incident()  # en -41.47, -72.94
        response = self.post_incident(lat=-41.4735, lng=-72.9400)  # ~390 m al sur
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["code"], "possible_duplicate")
        self.assertLessEqual(body["duplicates"][0]["distanceM"], 400)
        self.assertEqual(Incident.objects.count(), 1)

    def test_confirm_distinct_creates_it_anyway(self):
        self.create_incident()
        response = self.post_incident(lat=-41.4735, lng=-72.9400, confirmDistinct=True)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Incident.objects.count(), 2)

    def test_far_or_different_type_is_not_a_duplicate(self):
        self.create_incident()
        self.assertEqual(self.post_incident(lat=-41.4800, lng=-72.9400).status_code, 201)  # ~1,1 km
        self.assertEqual(self.post_incident(type="Accidente vial").status_code, 201)  # otro tipo

    def test_resolved_incidents_are_not_suggested_as_duplicates(self):
        created = self.create_incident()
        self.call(f"/api/incidents/{created['id']}", "put", {"status": "resuelto"}, self.token_for(self.operator))
        self.assertEqual(self.post_incident().status_code, 201)


class TypesChileAndCommunitiesTests(ApiTestCase):
    def test_new_incident_types_are_accepted(self):
        for index, name in enumerate(
            ["Corte de luz", "Corte de agua", "Incendio forestal", "Inundación", "Actividad volcánica", "Sismo"]
        ):
            created = self.create_incident(type=name, lat=-41.0 - index * 0.5, lng=-72.5)
            self.assertEqual(created["type"], name)
        self.assertEqual(self.call("/api/incidents", "post", {"type": "Invasión alienígena", "description": "x", "lat": -41.5, "lng": -72.9, "confirmDistinct": True}, self.token_for(self.author)).status_code, 400)

    def test_meta_lists_types_communities_and_rules(self):
        body = self.call("/api/meta").json()
        self.assertIn("Corte de luz", [item["name"] for item in body["types"]])
        self.assertEqual(len(body["communities"]), 16)
        self.assertTrue(body["chile"]["polygon"])

    def test_reports_outside_chile_are_rejected(self):
        token = self.token_for(self.author)
        for lat, lng in [(-34.60, -58.38), (-41.13, -71.31), (-12.05, -77.04), (40.4, -3.7)]:  # BA, Bariloche, Lima, Madrid
            response = self.call(
                "/api/incidents", "post",
                {"type": "Otra", "description": "fuera", "lat": lat, "lng": lng, "confirmDistinct": True}, token,
            )
            self.assertEqual(response.status_code, 400, (lat, lng))
        self.assertFalse(Incident.objects.exists())

    def test_editing_location_outside_chile_is_rejected(self):
        created = self.create_incident()
        response = self.call(
            f"/api/incidents/{created['id']}", "put", {"lat": -34.6, "lng": -58.38}, self.token_for(self.author)
        )
        self.assertEqual(response.status_code, 400)

    def test_geo_helper_on_known_places(self):
        inside = [(-41.47, -72.94), (-33.45, -70.67), (-53.16, -70.92), (-18.48, -70.31), (-27.12, -109.35), (-54.93, -67.62)]
        outside = [(-34.6, -58.4), (-41.13, -71.31), (-12.05, -77.04), (-54.8, -68.3), (-18.01, -70.25)]
        for point in inside:
            self.assertTrue(geo.in_chile(*point), point)
        for point in outside:
            self.assertFalse(geo.in_chile(*point), point)

    def test_incidents_are_assigned_to_the_nearest_community(self):
        south = self.create_incident(lat=-41.47, lng=-72.94)
        capital = self.create_incident(lat=-33.45, lng=-70.67, description="Santiago")
        self.assertEqual(south["community"]["slug"], "los-lagos")
        self.assertEqual(capital["community"]["slug"], "metropolitana")

    def test_list_can_be_filtered_by_community(self):
        self.create_incident(lat=-41.47, lng=-72.94)
        self.create_incident(lat=-33.45, lng=-70.67, description="Santiago")
        south = self.call("/api/incidents?community=los-lagos").json()
        self.assertEqual([item["community"]["slug"] for item in south], ["los-lagos"])
        self.assertEqual(len(self.call("/api/incidents").json()), 2)

    def test_user_can_choose_community(self):
        token = self.token_for(self.author)
        response = self.call("/api/auth/me", "put", {"community": "aysen"}, token)
        self.assertEqual(response.json()["user"]["community"]["slug"], "aysen")
        self.assertEqual(self.call("/api/auth/me", "put", {"community": "narnia"}, token).status_code, 400)
        self.assertIsNone(self.call("/api/auth/me", "put", {"community": None}, token).json()["user"]["community"])

    def test_seeded_communities_exist(self):
        self.assertEqual(Community.objects.filter(is_active=True).count(), 16)


class RulesUnitTests(TestCase):
    def test_vote_weight_bounds(self):
        self.assertEqual(rules.vote_weight(0), 1.0)
        self.assertEqual(rules.vote_weight(1000), rules.MAX_WEIGHT)
        self.assertEqual(rules.vote_weight(-1000), rules.MIN_WEIGHT)

    def test_evaluate_veracity_requires_minimum_distinct_voters(self):
        self.assertEqual(rules.evaluate_veracity([(1, 2.0), (1, 2.0)])[0], rules.UNVERIFIED)  # 4.0 y 2 votantes
        self.assertEqual(rules.evaluate_veracity([(1, 2.0)] * 3)[0], rules.COMMUNITY_CONFIRMED)
        self.assertEqual(rules.evaluate_veracity([(-1, 1.0)] * 3)[0], rules.PROBABLY_FALSE)
