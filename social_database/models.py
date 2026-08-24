"""数据库模型与连接管理。"""

from pathlib import Path

from sqlalchemy import Column, ForeignKey, Index, String, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from .config import DB_PATH

Base = declarative_base()


class Group(Base):
    """群组表，使用 ``group_id`` 唯一标识群组。"""

    __tablename__ = "groups"

    group_id = Column(String, primary_key=True)
    group_name = Column(String, nullable=True)

    members_info = relationship("MemberGroupInfo", back_populates="group")

    def __repr__(self):
        return f"<Group {self.group_id} - {self.group_name}>"


class Member(Base):
    """成员表，使用 ``user_id`` 唯一标识成员。"""

    __tablename__ = "members"

    user_id = Column(String, primary_key=True)

    groups_info = relationship("MemberGroupInfo", back_populates="member")

    def __repr__(self):
        return f"<Member {self.user_id}>"


class MemberGroupInfo(Base):
    """保存成员在特定群组中的资料。"""

    __tablename__ = "member_group_info"

    user_id = Column(String, ForeignKey("members.user_id"), primary_key=True)
    group_id = Column(String, ForeignKey("groups.group_id"), primary_key=True)
    nickname = Column(String, nullable=True)
    card = Column(String, nullable=True)
    join_time = Column(String, nullable=True)
    last_sent_time = Column(String, nullable=True)
    title = Column(String, nullable=True)

    member = relationship("Member", back_populates="groups_info")
    group = relationship("Group", back_populates="members_info")

    __table_args__ = (
        Index("idx_nickname", "nickname"),
        Index("idx_card", "card"),
        Index("idx_title", "title"),
        Index("idx_join_time", "join_time"),
        Index("idx_last_sent_time", "last_sent_time"),
    )

    def __repr__(self):
        return f"<MemberGroupInfo {self.user_id}@{self.group_id}>"


def _resolve_database(db_path: str | Path, create: bool) -> tuple[str, Path | None]:
    """返回 SQLAlchemy URL，并按需准备数据库目录。"""

    if str(db_path) == ":memory:":
        return "sqlite+pysqlite:///:memory:", None

    path = Path(db_path).expanduser().resolve()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.is_file():
        raise FileNotFoundError(f"数据库不存在: {path}")

    return f"sqlite+pysqlite:///{path.as_posix()}", path


def _enable_foreign_keys(engine: Engine) -> None:
    """为每个 SQLite 连接启用外键约束。"""

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db(db_path: str | Path = DB_PATH, *, create: bool = True):
    """创建会话工厂；导入时建库，搜索时可要求数据库必须已存在。"""

    database_url, _ = _resolve_database(db_path, create=create)
    engine = create_engine(database_url, echo=False)
    _enable_foreign_keys(engine)

    if create:
        Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, Session
