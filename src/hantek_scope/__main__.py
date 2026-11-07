import sys

from PySide6.QtWidgets import QApplication

from hantek_scope.acquisition.hantek6022 import Hantek6022Backend
from hantek_scope.acquisition.simulator import SimulatedScope
from hantek_scope.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    hantek = Hantek6022Backend()
    simulator = SimulatedScope()
    initial = hantek if hantek.is_connected() else simulator
    window = MainWindow([hantek, simulator], initial)
    window.show()
    window.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
