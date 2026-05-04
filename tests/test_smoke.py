import super_db


def test_package_imports_with_version():
    assert super_db.__version__ == "0.1.0"
