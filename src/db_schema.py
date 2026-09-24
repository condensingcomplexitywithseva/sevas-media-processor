# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from sqlmodel import SQLModel, Field
from sqlalchemy import Index

class DatabaseFileRegistry(SQLModel, table=True):
    __tablename__ = "file_registry"  # pyright: ignore[reportAssignmentType]

    file_id: int | None = Field(default=None, primary_key=True)

    processing_complete: bool = Field(default=False)
    ai_enabled: bool = Field(default=False)
    processing_error: str = Field(default="")

    file_path: str = Field(index=True)

    source_size: int | None = Field(default=None)
    source_mtime_ns: int | None = Field(default=None)
    source_sha256: str = Field(default="")

    file_ext: str

    total_pages: int = Field(default=0)

    file_to_jpegs_comment: str = Field(default="")

    page_range: str = Field(default="")

    range_status: str = Field(default="")


class DatabasePageLog(SQLModel, table=True):
    __tablename__ = "page_log"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (Index("ix_page_log_file_page", "file_id", "page_number"),)

    page_id: int | None = Field(default=None, primary_key=True)

    file_id: int = Field(foreign_key="file_registry.file_id", index=True)

    page_number: int

    output_file: str

    page_to_jpeg_status: str

    page_to_jpeg_comment: str

    video_frame_timestamp: str = Field(default="")


class DatabaseLLMRequest(SQLModel, table=True):
    __tablename__ = "llm_requests"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (Index("ix_llm_requests_file_number", "file_id", "request_number", "request_id"),
                      Index("ix_llm_requests_file_request", "file_id", "request_number", unique=True))

    request_id: int | None = Field(default=None, primary_key=True)

    file_id: int = Field(foreign_key="file_registry.file_id", index=True)

    request_number: int

    pages: str = Field(default="")

    request_status: str

    raw_llm_answer: str = Field(default="")

    llm_network_error: str = Field(default="")


class DatabaseRunConfiguration(SQLModel, table=True):
    __tablename__ = "run_configuration"  # pyright: ignore[reportAssignmentType]

    configuration_id: int | None = Field(default=None, primary_key=True)

    ai_enabled: bool
    source_root: str = Field(default="")
    processing_digest: str = Field(default="")

    output_mode: str

    declared_columns: str = Field(default="[]")


class DatabaseLLMAnswer(SQLModel, table=True):
    __tablename__ = "llm_answers"  # pyright: ignore[reportAssignmentType]

    llm_answer_id: int | None = Field(default=None, primary_key=True)

    request_id: int = Field(foreign_key="llm_requests.request_id", index=True)

    file_id: int = Field(foreign_key="file_registry.file_id", index=True)

    page_id: int | None = Field(default=None, foreign_key="page_log.page_id")

    raw_model_page_number: str = Field(default="")

    llm_error: str = Field(default="")

    values_json: str = Field(default="")
