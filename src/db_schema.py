# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from sqlmodel import SQLModel, Field
from typing import Optional

class DatabaseFileRegistry(SQLModel, table=True):
    unique_file_id: Optional[int] = Field(default=None, primary_key=True)

    relative_file_path: str = Field(index=True)

    original_extension: str

    total_discovered_pages: int = Field(default=0)

    final_aggregate_status: str = Field(default="processing")

    final_aggregate_comment: str = Field(default="")

    applied_range_string: str = Field(default="")

    range_status_code: str = Field(default="")

    llm_network_answer: Optional[str] = Field(default="")

    llm_network_error: Optional[str] = Field(default="")

    llm_answer_json: str = Field(default="")


class DatabasePageLog(SQLModel, table=True):
    primary_database_id: Optional[int] = Field(default=None, primary_key=True)

    parent_file_id: int = Field(foreign_key="databasefileregistry.unique_file_id", index=True)

    page_or_frame_number: int

    saved_filename: str

    execution_status: str

    execution_comment: str

    capture_timestamp: str = Field(default="")

    llm_answer_json: str = Field(default="")
