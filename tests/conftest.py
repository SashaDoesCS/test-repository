"""conftest.py -- pytest configuration for los_gatos_transit_cba tests."""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: tests that require real OSM/GTFS data files and may be slow",
    )
