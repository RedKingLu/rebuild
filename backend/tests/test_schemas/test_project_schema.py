"""Test Pydantic schema validation for Project schemas."""

import pytest
from pydantic import ValidationError
from app.schemas.project import ProjectCreate


def test_project_create_valid():
    p = ProjectCreate(name="Test", description="desc", source_type="local_dir")
    assert p.name == "Test"
    assert p.source_type == "local_dir"


def test_project_create_empty_name():
    with pytest.raises(ValidationError):
        ProjectCreate(name="")


def test_project_create_defaults():
    p = ProjectCreate(name="Test")
    assert p.description == ""
    assert p.source_type == "local_dir"
