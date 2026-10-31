"""test_db_driver.py — v5.89.348

2026-10-27: production refused to boot with "No module named 'psycopg'".
Flask-SQLAlchemy 3.0.5 does not pin SQLAlchemy; a fresh Docker build pulled
SQLAlchemy 2.1, whose default driver for postgresql:// is psycopg (v3). We ship
psycopg2-binary. These tests pin both halves of the fix: the URL names the
driver we ship, and requirements.txt keeps SQLAlchemy below 2.1.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class TestDatabaseUrl(unittest.TestCase):
    def test_render_url_names_psycopg2(self):
        from db_url import normalize_database_url as n
        self.assertEqual(n('postgres://u:p@h:5432/db'), 'postgresql+psycopg2://u:p@h:5432/db')
        self.assertEqual(n('postgresql://u:p@h/db'), 'postgresql+psycopg2://u:p@h/db')

    def test_explicit_driver_and_sqlite_untouched(self):
        from db_url import normalize_database_url as n
        self.assertEqual(n('postgresql+psycopg2://u@h/db'), 'postgresql+psycopg2://u@h/db')
        self.assertEqual(n('postgresql+psycopg://u@h/db'), 'postgresql+psycopg://u@h/db')
        self.assertEqual(n('sqlite:///offerwise.db'), 'sqlite:///offerwise.db')
        self.assertEqual(n(''), '')
        self.assertIsNone(n(None))

    def test_sqlalchemy_resolves_to_the_driver_we_ship(self):
        from sqlalchemy.engine import make_url
        from db_url import normalize_database_url as n
        d = make_url(n('postgres://u:p@h/db')).get_dialect()
        self.assertEqual(d.driver, 'psycopg2')

    def test_requirements_pin_sqlalchemy_below_2_1(self):
        with open(os.path.join(HERE, 'requirements.txt'), encoding='utf-8') as f:
            req = f.read()
        self.assertRegex(req, r"(?m)^SQLAlchemy>=2\.0[^\n]*,<2\.1\s*$")
        self.assertRegex(req, r"(?m)^psycopg2-binary==")

    def test_app_and_alembic_use_the_normalizer(self):
        for rel in ('app.py', os.path.join('alembic', 'env.py'), os.path.join('scripts', 'bootstrap_alembic.py')):
            with open(os.path.join(HERE, rel), encoding='utf-8') as f:
                src = f.read()
            self.assertIn('normalize_database_url', src, rel)
            self.assertNotIn(".replace('postgres://', 'postgresql://'", src, rel)
            self.assertNotIn(".replace('postgres://','postgresql://')", src, rel)


if __name__ == '__main__':
    unittest.main()
