from __future__ import annotations

import unittest

from app.core.config import settings


class StripeSafetyTests(unittest.TestCase):
    def test_validate_runtime_blocks_live_key_in_development(self):
        original_env = settings.APP_ENV
        original_key = settings.STRIPE_SECRET_KEY
        try:
            settings.APP_ENV = "development"
            settings.STRIPE_SECRET_KEY = "sk_live_test_only"
            with self.assertRaises(RuntimeError) as ctx:
                settings.validate_runtime()
            self.assertIn("development", str(ctx.exception).lower())
            self.assertIn("sk_live", str(ctx.exception))
        finally:
            settings.APP_ENV = original_env
            settings.STRIPE_SECRET_KEY = original_key


if __name__ == "__main__":
    unittest.main()

