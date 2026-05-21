#!/usr/bin/env python3

import pathlib
import sys
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import server


class AccountRegisterTests(unittest.TestCase):
    def config(self):
        return server.Config(
            host="127.0.0.1",
            port=18080,
            soap_url="http://127.0.0.1:7879/",
            soap_user="SOAP_PANEL",
            soap_password="secret",
            invite_code="invite",
            require_invite=True,
            trust_proxy=False,
            rate_limit_per_hour=2,
            realm_name="Test",
            public_base_url="",
            soap_timeout=20,
        )

    def test_validation_accepts_safe_values(self):
        errors = server.validate_registration(
            {
                "username": "Wuya_2026",
                "password": "Good_123",
                "confirm": "Good_123",
                "email": "wuya@example.com",
                "invite": "invite",
            },
            self.config(),
        )
        self.assertEqual(errors, [])

    def test_validation_rejects_command_injection_shapes(self):
        errors = server.validate_registration(
            {
                "username": "bad name",
                "password": "abc def",
                "confirm": "abc def",
                "email": "not-email",
                "invite": "wrong",
            },
            self.config(),
        )
        self.assertGreaterEqual(len(errors), 4)

    def test_command_builder_has_no_dot(self):
        self.assertEqual(
            server.build_account_create_command("TEST", "Pass_123", "a@example.com"),
            "account create TEST Pass_123 a@example.com",
        )

    def test_account_create_result_interpretation(self):
        self.assertTrue(server.account_create_succeeded("Account created: TEST", "TEST"))
        self.assertTrue(server.account_create_succeeded("创建帐号: TEST", "TEST"))
        self.assertFalse(server.account_create_succeeded("Account with this name already exist!", "TEST"))
        self.assertFalse(server.account_create_succeeded("", "TEST"))

    def test_rate_limiter(self):
        limiter = server.RateLimiter(2)
        self.assertTrue(limiter.allow("ip", now=100))
        self.assertTrue(limiter.allow("ip", now=101))
        self.assertFalse(limiter.allow("ip", now=102))
        self.assertTrue(limiter.allow("ip", now=3700))


if __name__ == "__main__":
    unittest.main()
