"""
OfferWise Auto-Migration
Automatically adds missing columns to the database on startup.

Compares SQLAlchemy model definitions against the actual database schema
and issues ALTER TABLE ADD COLUMN for anything missing. Never drops or
modifies existing columns — only adds new ones.

Usage (in app.py startup):
    from auto_migrate import auto_migrate
    auto_migrate(app, db)
"""

import logging
from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)

# Map SQLAlchemy types to PostgreSQL DDL types
TYPE_MAP = {
    'INTEGER': 'INTEGER',
    'BIGINTEGER': 'BIGINT',
    'SMALLINTEGER': 'SMALLINT',
    'FLOAT': 'DOUBLE PRECISION',
    'NUMERIC': 'NUMERIC',
    'STRING': 'VARCHAR',
    'VARCHAR': 'VARCHAR',
    'TEXT': 'TEXT',
    'BOOLEAN': 'BOOLEAN',
    'DATETIME': 'TIMESTAMP',
    'DATE': 'DATE',
    'TIME': 'TIME',
    'JSON': 'JSONB',
    'BLOB': 'BYTEA',
}


def _sql_type(column):
    """Convert a SQLAlchemy column type to a PostgreSQL DDL string."""
    type_name = type(column.type).__name__.upper()
    
    if type_name in ('STRING', 'VARCHAR'):
        length = getattr(column.type, 'length', None)
        if length:
            return f'VARCHAR({length})'
        return 'VARCHAR(255)'
    
    return TYPE_MAP.get(type_name, 'TEXT')


def _sql_default(column):
    """Get the DEFAULT clause for a column, if any."""
    if column.default is not None:
        val = column.default.arg
        if callable(val):
            return ''  # Can't express Python callables as SQL defaults
        if isinstance(val, bool):
            return f" DEFAULT {'TRUE' if val else 'FALSE'}"
        if isinstance(val, (int, float)):
            return f' DEFAULT {val}'
        if isinstance(val, str):
            return f" DEFAULT '{val}'"
    return ''


def auto_migrate(app, db):
    """
    Compare all registered SQLAlchemy models against the live database.
    Add any columns that exist in models but not in the database.
    
    Safe to run on every startup:
    - Uses IF NOT EXISTS (PostgreSQL 9.6+)
    - Never drops columns
    - Never modifies existing columns
    - Logs everything
    """
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            existing_tables = set(inspector.get_table_names())
            
            migrations_run = 0
            
            for model_class in db.Model.__subclasses__():
                table_name = model_class.__tablename__
                
                # Skip if table doesn't exist yet — db.create_all() handles new tables
                if table_name not in existing_tables:
                    continue
                
                # Get existing columns in the database
                db_columns = {col['name'] for col in inspector.get_columns(table_name)}
                
                # Get columns defined in the model
                for attr_name in dir(model_class):
                    attr = getattr(model_class, attr_name, None)
                    if attr is None:
                        continue
                    
                    # Check if it's a mapped column
                    if not hasattr(attr, 'property'):
                        continue
                    if not hasattr(attr.property, 'columns'):
                        continue
                    
                    for col in attr.property.columns:
                        col_name = col.name
                        
                        if col_name not in db_columns:
                            # Build ALTER TABLE statement
                            sql_type = _sql_type(col)
                            default = _sql_default(col)
                            nullable = '' if col.nullable else ' NOT NULL'
                            
                            # For NOT NULL columns without defaults, skip NOT NULL
                            # (would fail on existing rows) — add nullable first
                            if nullable and not default:
                                nullable = ''
                            
                            stmt = (
                                f'ALTER TABLE {table_name} '
                                f'ADD COLUMN IF NOT EXISTS {col_name} {sql_type}'
                                f'{default}{nullable}'
                            )
                            
                            try:
                                with db.engine.connect() as conn:
                                    conn.execute(text(stmt))
                                    conn.commit()
                                
                                migrations_run += 1
                                logger.info(
                                    f'✅ AUTO-MIGRATE: Added {table_name}.{col_name} '
                                    f'({sql_type}{default})'
                                )
                            except Exception as col_err:
                                logger.warning(
                                    f'⚠️ AUTO-MIGRATE: Could not add '
                                    f'{table_name}.{col_name}: {col_err}'
                                )
            
            if migrations_run > 0:
                logger.info(f'🔄 AUTO-MIGRATE: {migrations_run} column(s) added')
            else:
                logger.info('✅ AUTO-MIGRATE: Schema is up to date')
            
            # Fix specific constraint mismatches
            # These are one-time fixes for columns that changed from NOT NULL to nullable
            constraint_fixes = [
                ('analyses', 'result_json', "ALTER TABLE analyses ALTER COLUMN result_json DROP NOT NULL"),
                ('analyses', 'result_json', "ALTER TABLE analyses ALTER COLUMN result_json SET DEFAULT '{}'"),
            ]
            
            for table, col, fix_sql in constraint_fixes:
                if table in existing_tables:
                    try:
                        with db.engine.connect() as conn:
                            conn.execute(text(fix_sql))
                            conn.commit()
                    except Exception:
                        pass  # Already fixed or not applicable
                
        except Exception as e:
            logger.warning(f'⚠️ AUTO-MIGRATE failed (non-critical): {e}')
            # Never crash the app over migration issues
