"""Database engine and session."""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

engine = create_engine("sqlite:///shop.db")
Session = sessionmaker(bind=engine)
Base = declarative_base()


def get_session():
    return Session()
