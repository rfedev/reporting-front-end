"""Database configuration and session management for the persistence layer."""

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

Base = declarative_base()
LogBase = declarative_base()


class DatabaseManager:
    """Manages SQLite connection using SQLAlchemy."""

    def __init__(self, db_path: Path = Path(".reporting_app.db")):
        self.db_path = db_path
        self.engine = create_engine(
            f"sqlite:///{self.db_path.resolve()}",
            echo=False,
            future=True,
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
            future=True,
        )

    def initialize_schema(self) -> None:
        """Create all tables in SQLite."""
        Base.metadata.create_all(self.engine)
        LogBase.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        """Provide a new SQLAlchemy session."""
        return self.session_factory()
