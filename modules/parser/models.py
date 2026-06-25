"""
Pydantic models for Problem and Example data structures.
Used across parser, uploader, and API layers.
"""

from pydantic import BaseModel, field_validator
from typing import Optional


class Example(BaseModel):
    """A single example test case parsed from the problem statement."""
    input: str
    output: str


class Problem(BaseModel):
    """
    Complete problem representation for Polygon upload.
    All LaTeX fields use $..$  or $$..$$  notation.
    """

    # --- Meta (Polygon) ---
    polygon_name: str = ""
    time_limit: int = 1000       # ms, must be divisible by 50
    memory_limit: int = 256      # MB, 4-1024
    checker: str = "std::lcmp.cpp"

    # --- Statement (LaTeX) ---
    title: str = ""
    statement: str = ""
    input_format: str = ""
    output_format: str = ""
    notes: str = ""

    # --- Examples ---
    examples: list[Example] = []

    # --- Tags ---
    tags: list[str] = []

    # --- Optional file paths ---
    solution_path: str = ""
    tests_dir: str = ""

    @field_validator("time_limit")
    @classmethod
    def validate_time_limit(cls, v: int) -> int:
        if v % 50 != 0:
            raise ValueError("time_limit phải chia hết cho 50")
        if not (250 <= v <= 15000):
            raise ValueError("time_limit phải nằm trong khoảng 250–15000 ms")
        return v

    @field_validator("memory_limit")
    @classmethod
    def validate_memory_limit(cls, v: int) -> int:
        if not (4 <= v <= 1024):
            raise ValueError("memory_limit phải nằm trong khoảng 4–1024 MB")
        return v