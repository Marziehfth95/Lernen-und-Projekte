"""Project metadata and scaffold smoke tests."""

import tomllib
from pathlib import Path

import process_optimizer

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_requires_python_312() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        configuration = tomllib.load(file)

    assert configuration["project"]["requires-python"] == ">=3.12,<3.13"


def test_package_exposes_version() -> None:
    assert process_optimizer.__version__ == "0.7.0"


def test_postgresql_bootstrap_is_parameterized_and_least_privilege() -> None:
    template = (
        PROJECT_ROOT / "deploy" / "postgresql" / "bootstrap.sql.template"
    ).read_text(encoding="utf-8")
    upper = template.upper()

    assert "pgaadauth_create_principal_with_oid" in template
    assert "runtime_object_id" in template
    assert "migration_object_id" in template
    assert "GRANT SELECT, INSERT ON TABLE" in template
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE" in template
    assert " ON ALL TABLES " not in upper
    assert " DELETE " not in upper
    for prohibited in ("GRANT ALL", "SUPERUSER", "CREATEDB", "CREATEROLE", "PASSWORD"):
        assert prohibited not in upper
