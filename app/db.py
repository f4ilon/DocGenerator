from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_session_factory(database_url: str):
    kwargs = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=kwargs, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
