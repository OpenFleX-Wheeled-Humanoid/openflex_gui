from PySide6.QtCore import QSettings


class ThemeManager:
    THEMES = {"day", "night"}

    def __init__(self, settings: QSettings | None = None):
        self.settings = settings or QSettings("OpenFlex", "ControlCenter")
        saved = self.settings.value("appearance/theme", "day", type=str)
        self.current_theme = saved if saved in self.THEMES else "day"

    def set_theme(self, theme: str) -> None:
        if theme not in self.THEMES:
            raise ValueError(f"unsupported theme: {theme}")
        self.current_theme = theme
        self.settings.setValue("appearance/theme", theme)
        self.settings.sync()

    def toggle(self) -> str:
        next_theme = "night" if self.current_theme == "day" else "day"
        self.set_theme(next_theme)
        return next_theme
