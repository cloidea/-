from pathlib import Path

from ui.main_window import MainWindow


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    app = MainWindow(root)
    app.mainloop()
