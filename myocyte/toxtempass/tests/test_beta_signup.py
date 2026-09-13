from django.test import TestCase

from toxtempass import utilities
from toxtempass.tests.fixtures.factories import PersonFactory


class BetaSignupTests(TestCase):
    def test_generate_and_verify_token(self):
        # Use factory (email-based signon; username is None)
        p = PersonFactory.create()
        token = utilities.generate_beta_token(p.id)
        payload = utilities.verify_beta_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(int(payload["person_id"]), p.id)

    def test_set_requested_and_admit_helpers(self):
        p = PersonFactory.create()
        utilities.set_beta_requested(p, comment="please")
        # Refresh from DB to ensure preferences persisted
        p.refresh_from_db()
        self.assertTrue(p.preferences.get("beta_signup"))
        self.assertFalse(p.preferences.get("beta_admitted", False))

        utilities.set_beta_admitted(p, True, comment="ok")
        p.refresh_from_db()
        self.assertTrue(p.preferences.get("beta_admitted"))
        self.assertIsNotNone(p.preferences.get("beta_admitted_at"))
