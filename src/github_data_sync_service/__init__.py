from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("github-data-sync-service")
except PackageNotFoundError:
    __version__ = "0.1.0"
