"""数据库模型定义"""

from sqlalchemy import (
    create_engine, Column, String, Text, ForeignKey, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from config import DB_PATH

Base = declarative_base()


class Group(Base):
    """群组表 - group_id 与 group_name 一对一绑定"""
    __tablename__ = "groups"

    group_id = Column(String, primary_key=True)
    group_name = Column(String, nullable=True)

    members_info = relationship("MemberGroupInfo", back_populates="group")

    def __repr__(self):
        return f"<Group {self.group_id} - {self.group_name}>"


class Member(Base):
    """成员表 - user_id 作为唯一标识"""
    __tablename__ = "members"

    user_id = Column(String, primary_key=True)

    groups_info = relationship("MemberGroupInfo", back_populates="member")

    def __repr__(self):
        return f"<Member {self.user_id}>"


class MemberGroupInfo(Base):
    """
    成员-群组绑定信息表
    同一 user_id 在不同 group_id 下有独立的绑定组:
    nickname, card, join_time, last_sent_time, title
    """
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

    # 索引：支持按各字段快速搜索
    __table_args__ = (
        Index("idx_nickname", "nickname"),
        Index("idx_card", "card"),
        Index("idx_title", "title"),
        Index("idx_join_time", "join_time"),
        Index("idx_last_sent_time", "last_sent_time"),
    )

    def __repr__(self):
        return f"<MemberGroupInfo {self.user_id}@{self.group_id}>"


def init_db(db_path=DB_PATH):
    """初始化数据库，返回 engine 和 Session"""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return engine, Session
