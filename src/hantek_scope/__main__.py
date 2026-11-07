import sys

from PySide6.QtWidgets import QApplication

from hantek_scope.acquisition.simulator import SimulatedScope
from hantek_scope.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow(SimulatedScope())
    window.show()
    window.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
