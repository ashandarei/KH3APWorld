"""KH3-only pytest options, loaded by scripts/test-kh3.ps1."""
import os


def pytest_addoption(parser):
    parser.addoption("--native-integration", action="store_true",
                     help="Include KH3 contracts against KH3_RUNTIME_DIR")


def pytest_configure(config):
    if config.getoption("--native-integration"):
        os.environ["KH3_NATIVE_INTEGRATION"] = "1"
