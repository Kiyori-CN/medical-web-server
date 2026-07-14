"""Structured local data and search core for medical-web-server v2."""

# Keep the package import side-effect free. Offline catalog builders depend on
# openpyxl/python-docx, which are intentionally development-only dependencies;
# runtime API imports must not load them on the production server.
__all__: list[str] = []
