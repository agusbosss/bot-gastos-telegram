from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from contextlib import contextmanager
import os

# En Railway apuntalo a un volumen (sqlite:////data/agent.db): el disco del
# contenedor se borra en cada deploy y con él el historial y las memorias.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./agent.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    from db import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _run_migrations()


# Columnas agregadas después de que la tabla ya existía. create_all() solo crea
# tablas nuevas, no agrega columnas, así que hay que hacerlo a mano.
_MIGRATIONS = [
    ("conversation_messages", "content_json", "TEXT"),
]


def _run_migrations():
    with engine.connect() as conn:
        for table, column, coltype in _MIGRATIONS:
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if existing and column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                conn.commit()


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
