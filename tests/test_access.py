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
            with self.assertLogs(m.app.logger, level="WARNING") as logs:
                result = client.post("/fetch_account", json={"plan": "Standard"})
            self.assertEqual(result.status_code, 503)
            self.assertFalse(result.get_json()["success"])
            message = result.get_json()["message"]
            self.assertIn("chưa cấp tài khoản", message)
            self.assertIn("Không khởi động được trình duyệt", message)
            reference = logs.records[0].args[0]
            self.assertRegex(reference, r"^[a-f0-9]{12}$")
            self.assertIn(reference, message)
            self.assertIn("stage=startup error_type=OSError", logs.output[0])
            self.assertNotIn("simulated outage", logs.output[0] + message)
            self.assertEqual(result.get_json()["subscription_label"], "Chưa xác minh được")
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
                self.assertEqual(row.allocation_label, "Chưa cấp")
                if item_id == ids[0]:
                    self.assertEqual(row.subscription_label, "Chưa xác minh được")
                else:
                    self.assertEqual(row.subscription_label, "Chưa có kết quả")
                self.assertIsNone(row.last_checked_at)
                self.assertIsNone(row.assigned_to_user_id)
                m.db.session.delete(row)
            m.db.session.commit()

    def test_inventory_filter_matches_entire_plan_label(self):
        m = self.module
        with self.web.app_context():
            rows = [m.AccountVault(plan=label, status='Chưa Check') for label in
                    ('Standard', ' Premium ', 'standard plus', 'Standard_Premium')]
            assigned = m.AccountVault(plan='Standard', status='Đã Cấp')
            m.db.session.add_all(rows + [assigned])
            m.db.session.flush()
            ids = [row.id for row in rows]
            for plan, expected in [('', ids), ('Standard', ids[:1]), ('Premium', ids[1:2]), ('%', [])]:
                with self.subTest(plan=plan):
                    result = m.AccountVault.unassigned_candidates(plan).filter(
                        m.AccountVault.id.in_(ids + [assigned.id])
                    ).order_by(m.AccountVault.id).all()
                    self.assertEqual([row.id for row in result], expected)
            m.db.session.rollback()

    def test_invalid_plan_type_is_not_treated_as_all_plans(self):
        client = self.web.test_client()
        client.post('/login', data={'username': 'test-admin', 'password': self.password})
        self.assertEqual(client.post('/fetch_account', json={'plan': []}).status_code, 400)

    def test_subscription_timeout_reports_reference_without_request_data(self):
        m = self.module
        error = m.PlaywrightTimeoutError("sensitive-test-data")
        with self.assertLogs(m.app.logger, level="WARNING") as logs:
            result, message = m.report_check_error("subscription", error)
        self.assertEqual(result, "UNKNOWN")
        self.assertIn("trạng thái gói", message)
        self.assertIn("hết thời gian chờ", message)
        self.assertIn(logs.records[0].args[0], message)
        self.assertNotIn("sensitive-test-data", logs.output[0] + message)

    def test_cookie_check_requires_evidence_of_account_and_plan_status(self):
        m = self.module
        cases = [
            ("/vn/login?next=/account", True, "Sign in", False, "DEAD"),
            ("/account", True, "Next payment\nDecember 5\nUpdate payment method", False, "LIVE"),
            ("/account", True, "Premium", True, "LIVE"),
            ("/account", True, "Next payment\nYour account is on hold", True, "EXPIRED"),
            ("/account", True, "Manage payment method\nRestart your membership", False, "UNKNOWN"),
            ("/account", False, "Next payment", True, "UNKNOWN"),
            ("/", True, "Netflix", False, "UNKNOWN"),
        ]
        with patch.object(m, "sync_playwright") as playwright, patch.object(m.time, "sleep"):
            browser = playwright.return_value.__enter__.return_value.chromium.launch.return_value
            page = browser.new_context.return_value.new_page.return_value
            plan_link = page.locator.return_value.first
            page.locator.return_value.all.return_value = [plan_link]
            for path, http_ok, text, has_plan_link, expected in cases:
                with self.subTest(path=path, text=text):
                    page.url = f"https://www.netflix.com{path}"
                    page.goto.return_value.ok = http_ok
                    page.inner_text.return_value = text
                    plan_link.is_visible.return_value = has_plan_link
                    result, _ = m.check_netflix_cookie_live("NetflixId=test-value")
                    self.assertEqual(result, expected)
            playwright.reset_mock()
            self.assertEqual(m.check_netflix_cookie_live("[]")[0], "DEAD")
            playwright.assert_not_called()

    def test_legacy_allocation_is_independent_of_subscription(self):
        m = self.module
        with self.web.app_context():
            allocated = m.AccountVault(status="Đã Live", assigned_to="khách cũ")
            unchecked = m.AccountVault(status="Chưa Check")
            m.db.session.add_all([allocated, unchecked])
            m.db.session.commit()
            candidates = m.AccountVault.unassigned_candidates().all()
            self.assertNotIn(allocated, candidates)
            self.assertIn(unchecked, candidates)
            self.assertEqual(allocated.allocation_label, "Đã cấp")
            self.assertEqual(allocated.subscription_label, "Hoạt động")
            self.assertEqual(unchecked.subscription_label, "Chưa có kết quả")
            allocated.record_check("UNKNOWN")
            self.assertEqual(allocated.allocation_label, "Đã cấp")
            self.assertEqual(allocated.subscription_label, "Chưa xác minh được")
            m.db.session.delete(allocated)
            m.db.session.delete(unchecked)
            m.db.session.commit()

    def test_manual_check_persists_result_without_reopening_assigned_stock(self):
        m = self.module
        client = self.web.test_client()
        client.post("/login", data={"username": "test-admin", "password": self.password})
        with self.web.app_context():
            row = m.AccountVault(email="vault-check@example.invalid", plan="Standard",
                                 status="Đã Cấp", assigned_to="người nhận")
            m.db.session.add(row)
            m.db.session.commit()
            row_id = row.id
            self.assertEqual(row.subscription_label, "Chưa có kết quả")
        response = client.post(f"/check_account/{row_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["account"]["allocation_label"], "Death")
        self.assertEqual(response.json["account"]["subscription_result"], "DEAD")
        self.assertTrue(response.json["account"]["is_allocated"])
        self.assertIn(f'id="vault-account-{row_id}"', client.get("/").get_data(as_text=True))

        with patch.object(m, "check_netflix_cookie_live", return_value=("UNKNOWN", "Đã hết thời gian chờ")):
            response = client.post(f"/check_account/{row_id}")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["account"]["allocation_label"], "Death")

        with patch.object(m, "check_netflix_cookie_live", return_value=("EXPIRED", "Gói tạm ngưng")):
            response = client.post(f"/check_account/{row_id}")
        self.assertEqual(response.json["account"]["allocation_label"], "Death")
        with self.web.app_context():
            row = m.db.session.get(m.AccountVault, row_id)
            self.assertEqual(row.status, "Đã Cấp")
            self.assertEqual(row.assigned_to, "người nhận")
            self.assertEqual(row.subscription_label, "Hết hạn hoặc tạm ngưng")
            row.release_allocation()
            self.assertEqual(row.allocation_label, "Death")
            self.assertEqual(row.subscription_label, "Hết hạn hoặc tạm ngưng")
            self.assertNotIn(row, m.AccountVault.unassigned_candidates().all())
            m.db.session.commit()

        self.assertEqual(client.post(f"/assign_account/{row_id}", json={}).status_code, 409)
        with patch.object(m, "check_netflix_cookie_live", return_value=("LIVE", "Gói còn hiệu lực")):
            response = client.post(f"/check_account/{row_id}")
        self.assertEqual(response.json["account"]["allocation_label"], "Chưa cấp")
        self.assertEqual(response.json["account"]["subscription_label"], "Hoạt động")
        self.assertIn('data-allocated="false" data-result="LIVE"', client.get("/").get_data(as_text=True))
        with self.web.app_context():
            row = m.db.session.get(m.AccountVault, row_id)
            m.db.session.delete(row)
            m.db.session.commit()

    def test_legacy_tv_death_overrides_stale_live_check(self):
        m = self.module
        row = m.AccountVault(status="Dead", subscription_check="LIVE")
        self.assertEqual(row.last_subscription_result, "DEAD")
        self.assertEqual(row.allocation_label, "Death")
        row.record_check("LIVE")
        self.assertEqual(row.allocation_label, "Chưa cấp")
        self.assertEqual(row.last_subscription_result, "LIVE")

    def test_allocation_change_during_check_preserves_new_owner(self):
        m = self.module
        client = self.web.test_client()
        client.post("/login", data={"username": "test-alice", "password": self.password})
        with self.web.app_context():
            row = m.AccountVault(plan="Standard", status="Chưa Check")
            m.db.session.add(row)
            m.db.session.commit()
            row_id = row.id
            bob_id = m.User.query.filter_by(username="test-bob").one().id

        def another_owner_during_check(_):
            with self.web.app_context():
                row = m.db.session.get(m.AccountVault, row_id)
                row.assigned_to_user_id = bob_id
                m.db.session.commit()
            return "UNKNOWN", "Chưa xác minh được trạng thái gói."

        self.assertEqual(client.post(f"/assign_account/{row_id}", json={}).status_code, 403)
        with patch.object(m, "check_netflix_cookie_live", side_effect=another_owner_during_check):
            response = client.post("/fetch_account", json={"plan": "Standard"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("account", response.json)
        with self.web.app_context():
            row = m.db.session.get(m.AccountVault, row_id)
            self.assertEqual(row.assigned_to_user_id, bob_id)
            self.assertEqual(row.subscription_label, "Chưa có kết quả")
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
