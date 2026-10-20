from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

# One engine per process. It owns a pool of DB connections that get reused.
# pool_pre_ping checks a connection is alive before using it, so a Postgres
# restart doesn't cause "connection closed" errors on the next request.
engine = create_engine(settings.database_url, pool_pre_ping=True)

# Factory for sessions (a session = one unit of work / transaction).
SessionLocal = sessionmaker(bind=engine)
