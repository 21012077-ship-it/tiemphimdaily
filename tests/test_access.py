import io
import os
import secrets
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from unittest.mock import patch


class AccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_urlsafe(32)
        settings = {
            "DATABASE_URL": "sqlite:///:memory:",
            "SECRET_KEY": secrets.token_hex(32),
            "ADMIN_USERNAME": "test-admin",
            "ADMIN_PASSWORD": cls.password,
        }
        env_patch = patch.dict(os.environ, settings)
        env_patch.start()
        cls.addClassCleanup(env_patch.stop)
        cls.startup_log = io.StringIO()
        with redirect_stdout(cls.startup_log):
            import app

        cls.module = app
        cls.web = app.app
        cls.web.config["TESTING"] = True
        with cls.web.app_context():
            alice = app.User(username="test-alice", role="ctv")
            bob = app.User(username="test-bob", role="ctv")
            for user in (alice, bob):
                user.set_password(cls.password)
            shared = app.PremiumAccount(email="shared@example.invalid", notes="private-admin-note")
            full = app.PremiumAccount(email="full@example.invalid")
            app.db.session.add_all([alice, bob, shared, full])
            app.db.session.flush()
            slots = [
                (shared, alice.id, "Alice's customer"),
                (shared, bob.id, "BobsCustomer"),
                (shared, None, "LegacyCustomer"),
            ]
            slots.extend((full, bob.id, f"PrivateFull{i}") for i in range(5))
            for account, owner_id, marker in slots:
                account.slots.append(app.PremiumSlot(
                    customer_name=marker, profile_name=f"Profile-{marker}", source="Zalo",
                    contact_link=f"Contact-{marker}", created_by_user_id=owner_id,
                    expiry_date=date.today() + timedelta(days=2),
                ))
            app.db.session.commit()

    def dashboard(self, username):
        client = self.web.test_client()
        response = client.post("/login", data={"username": username, "password": self.password})
        self.assertEqual(response.status_code, 302)
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        return page.get_data(as_text=True)

    def test_ctv_dashboard_filters_customers_and_preserves_capacity(self):
        page = self.dashboard("test-alice")
        self.assertIn("shared@example.invalid", page)
        self.assertIn("3/5", page)
        self.assertIn("Alice&#39;s customer", page)
        for hidden in ("BobsCustomer", "LegacyCustomer", "full@example.invalid", "private-admin-note"):
            self.assertNotIn(hidden, page)

        bob_page = self.dashboard("test-bob")
        self.assertIn("BobsCustomer", bob_page)
        self.assertIn("full@example.invalid", bob_page)
        self.assertNotIn("Alice&#39;s customer", bob_page)

    def test_admin_can_read_shared_and_legacy_records(self):
        page = self.dashboard("test-admin")
        for visible in ("BobsCustomer", "LegacyCustomer", "full@example.invalid", "private-admin-note"):
            self.assertIn(visible, page)

    def test_reminder_treats_quoted_names_as_data(self):
        page = self.dashboard("test-alice")
        self.assertIn('data-reminder-name="Alice&#39;s customer"', page)
        self.assertNotIn('onclick="copyAndStatus(', page)

    def test_contact_links_support_social_urls_and_phone_numbers(self):
        contact_url = self.module.contact_url
        self.assertEqual(contact_url("0123456789", "Zalo"), "https://zalo.me/0123456789")
        self.assertEqual(contact_url("m.me/test", "Facebook"), "https://m.me/test")
        self.assertEqual(contact_url("https://example.invalid/p", "Khác"), "https://example.invalid/p")
        self.assertIsNone(contact_url("file:///local-example", "Khác"))

    def test_existing_admin_password_survives_startup(self):
        self.assertNotIn(self.password, self.startup_log.getvalue())
        with self.web.app_context(), patch.dict(os.environ, ADMIN_PASSWORD=secrets.token_hex(24)):
            self.module.create_default_admin()
            user = self.module.User.query.filter_by(username="test-admin").one()
            self.assertTrue(user.check_password(self.password))

    def test_standard_fetch_stops_on_outage_without_reclassifying_inventory(self):
        m = self.module
        client = self.web.test_client()
        client.post("/login", data={"username": "test-admin", "password": self.password})
        with self.web.app_context():
            rows = [
                m.AccountVault(email=f"outage-{n}@example.invalid", plan="Standard", status="Offline")
                for n in range(3)
            ]
            m.db.session.add_all(rows)
            m.db.session.commit()
            ids = [row.id for row in rows]

        with patch.object(m, "convert_cookies_for_playwright", return_value=[object()]), patch.object(
            m, "sync_playwright", side_effect=OSError("simulated outage")
        ) as offline:
            result = client.post("/fetch_account", json={"plan": "Standard"})
            self.assertEqual(result.status_code, 503)
            self.assertFalse(result.get_json()["success"])
            offline.assert_called_once()
            self.assertIn("inventory;dur=", result.headers["Server-Timing"])
            self.assertIn("check;dur=", result.headers["Server-Timing"])
            self.assertNotIn("link;dur=", result.headers["Server-Timing"])
            single = client.post(f"/check_account/{ids[0]}")
            self.assertEqual(single.status_code, 503)

        with self.web.app_context():
            for item_id in ids:
                row = m.db.session.get(m.AccountVault, item_id)
                self.assertEqual(row.status, "Offline")
                self.assertIsNone(row.last_checked_at)
                self.assertIsNone(row.assigned_to_user_id)
                m.db.session.delete(row)
            m.db.session.commit()

    def test_household_uses_official_verification(self):
        client = self.web.test_client()
        guide = client.get("/tools/household")
        self.assertEqual(guide.status_code, 200)
        self.assertIn("https://help.netflix.com/vi/node/128339", guide.get_data(as_text=True))
        self.assertNotIn("<form", guide.get_data(as_text=True))
        for result in (
            client.post("/tools/household/start", json={"email": "test@example.invalid"}),
            client.get("/tools/household/status/old-task"),
        ):
            self.assertEqual(result.status_code, 410)
            self.assertFalse(result.get_json()["success"])


if __name__ == "__main__":
    unittest.main()
