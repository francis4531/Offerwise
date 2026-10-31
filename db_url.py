"""db_url.py — v5.89.348. One place that turns DATABASE_URL into a SQLAlchemy URL.

Render hands out ``postgres://``. SQLAlchemy wants ``postgresql://``, and since
SQLAlchemy 2.1 a bare ``postgresql://`` means the psycopg (v3) driver, which is
not installed; ``psycopg2-binary`` is. The 2026-10-27 production boot failure
("No module named 'psycopg'") was exactly that: Flask-SQLAlchemy pulled an
unpinned SQLAlchemy 2.1 on a fresh Docker build. Naming the driver in the URL
makes the choice explicit regardless of SQLAlchemy's default.
"""


def normalize_database_url(url):
    """postgres:// and postgresql:// -> postgresql+psycopg2://; others unchanged."""
    if not url:
        return url
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    if url.startswith('postgresql://'):
        url = 'postgresql+psycopg2://' + url[len('postgresql://'):]
    return url
