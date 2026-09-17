from pathlib import Path
import logging
import os

from ui.main_window import MainWindow


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    if os.name == "nt":
        import ctypes
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\CatVideoGenerator")
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(None, "程序已经打开，请使用现有窗口。", "提示", 0)
            raise SystemExit(0)
    (root / "logs").mkdir(exist_ok=True)
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
        root / "logs" / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")])
    app = MainWindow(root)
    app.mainloop()
