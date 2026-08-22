from pathlib import Path
import sys

from PySide6.QtWidgets import QWidget


class MotorManagerAdapter:
    def __init__(self, manager_dir: str | Path, controller_factory=None):
        self.manager_dir = Path(manager_dir).expanduser().resolve()
        self._controller_factory = controller_factory
        self.page: QWidget | None = None
        self.controller = None
        self.is_shutdown = False
        self._validate_manager_dir()

    def _validate_manager_dir(self) -> None:
        required = (
            self.manager_dir / "main.py",
            self.manager_dir / "ui",
            self.manager_dir / "controllers",
            self.manager_dir / "config",
        )
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "openflex_manager is incomplete; missing: " + ", ".join(missing)
            )

    def create_page(self) -> QWidget:
        if self.is_shutdown:
            raise RuntimeError("motor manager adapter has been shut down")
        if self.page is not None:
            return self.page

        manager_path = str(self.manager_dir)
        if manager_path not in sys.path:
            sys.path.insert(0, manager_path)

        from ui import MotorManagementPage

        self.page = MotorManagementPage()
        return self.page

    def initialize_controller(self):
        """Create hardware-facing controllers only when maintenance is opened."""
        if self.is_shutdown:
            raise RuntimeError("motor manager adapter has been shut down")
        if self.controller is not None:
            return self.controller
        if self.page is None:
            self.create_page()

        controller_factory = self._controller_factory
        if controller_factory is None:
            from controllers import MainController

            controller_factory = MainController
        self.controller = controller_factory(self.page)
        self.page.controller = self.controller
        return self.controller

    def set_theme(self, theme: str) -> None:
        if theme not in {"day", "night"}:
            raise ValueError(f"unsupported control-center theme: {theme}")
        if self.page is not None:
            self.page.apply_theme("light" if theme == "day" else "dark")

    def has_active_connection(self) -> bool:
        if self.controller is None:
            return False
        for name in ("head_controller", "column_controller", "chassis_controller"):
            controller = getattr(self.controller, name, None)
            connected = getattr(controller, "is_connected", False)
            if callable(connected):
                connected = connected()
            if connected:
                return True
        return False

    def shutdown(self) -> None:
        if self.is_shutdown:
            return
        self.is_shutdown = True
        if self.controller is not None:
            self.controller.shutdown()
        if self.page is not None:
            self.page.controller = None
