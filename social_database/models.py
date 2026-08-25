"""数据库模型与连接管理。"""

from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from .config import DB_PATH

Base = declarative_base()


class ImportBatch(Base):
    """记录一次成功的数据源导入。"""

    __tablename__ = "import_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String, nullable=False)
    source_name = Column(String, nullable=True)
    source_hash = Column(String(64), nullable=True)
    source_format_version = Column(Integer, nullable=True)
    producer = Column(String, nullable=True)
    external_batch_id = Column(String(128), nullable=True)
    observed_at_utc = Column(DateTime, nullable=True)
    imported_at_utc = Column(DateTime, nullable=False)
    forced = Column(Boolean, nullable=False, default=False)
    duplicate_of_id = Column(
        Integer,
        ForeignKey("import_batches.id"),
        nullable=True,
    )

    source_rows = Column(Integer, nullable=False, default=0)
    valid_rows = Column(Integer, nullable=False, default=0)
    skipped_rows = Column(Integer, nullable=False, default=0)
    missing_user_id_rows = Column(Integer, nullable=False, default=0)
    missing_group_id_rows = Column(Integer, nullable=False, default=0)

    unique_groups = Column(Integer, nullable=False, default=0)
    unique_members = Column(Integer, nullable=False, default=0)
    unique_relations = Column(Integer, nullable=False, default=0)
    new_groups = Column(Integer, nullable=False, default=0)
    updated_groups = Column(Integer, nullable=False, default=0)
    new_members = Column(Integer, nullable=False, default=0)
    new_relations = Column(Integer, nullable=False, default=0)
    updated_relations = Column(Integer, nullable=False, default=0)
    unchanged_relations = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index(
            "idx_import_batches_source",
            "source_type",
            "source_hash",
        ),
        Index(
            "ux_import_batches_producer_external_batch_id",
            "producer",
            "external_batch_id",
            unique=True,
        ),
    )

    def __repr__(self):
        return f"<ImportBatch {self.id} {self.source_type}:{self.source_name}>"


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
    sex = Column(String, nullable=True)
    age = Column(String, nullable=True)
    area = Column(String, nullable=True)
    level = Column(String, nullable=True)
    qq_level = Column(String, nullable=True)
    join_time = Column(String, nullable=True)
    last_sent_time = Column(String, nullable=True)
    title_expire_time = Column(String, nullable=True)
    unfriendly = Column(String, nullable=True)
    card_changeable = Column(String, nullable=True)
    is_robot = Column(String, nullable=True)
    shut_up_timestamp = Column(String, nullable=True)
    role = Column(String, nullable=True)
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


class RelationObservation(Base):
    """记录成员-群组关系首次和最近出现的导入批次。"""

    __tablename__ = "relation_observations"

    user_id = Column(String, primary_key=True)
    group_id = Column(String, primary_key=True)
    first_seen_batch_id = Column(
        Integer,
        ForeignKey("import_batches.id"),
        nullable=False,
    )
    last_seen_batch_id = Column(
        Integer,
        ForeignKey("import_batches.id"),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "group_id"],
            [
                "member_group_info.user_id",
                "member_group_info.group_id",
            ],
            ondelete="CASCADE",
        ),
        Index("idx_relation_observations_last_batch", "last_seen_batch_id"),
    )


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
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def init_db(db_path: str | Path = DB_PATH, *, create: bool = True):
    """创建会话工厂；导入时建库，搜索时可要求数据库必须已存在。"""

    database_url, _ = _resolve_database(db_path, create=create)
    engine = create_engine(
        database_url,
        echo=False,
        connect_args={"timeout": 30.0},
    )
    _enable_foreign_keys(engine)

    from .migrations import upgrade_database, validate_database_version

    try:
        # 必须先拒绝未来 schema，避免 create_all 在报错前修改数据库。
        validate_database_version(engine)
        Base.metadata.create_all(engine)
        upgrade_database(engine)
    except Exception:
        engine.dispose()
        raise

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, Session
