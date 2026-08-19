from pathlib import Path
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QIcon, QPixmap


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("About Dyedfox Radio"))
        self.setModal(True)
        self.setFixedWidth(320)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(24, 24, 24, 16)

        icon_path = Path(__file__).parent.parent / "assets" / "icons" / "dyedfox-radio.png"
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = QPixmap(str(icon_path))
        if not pix.isNull():
            icon_label.setPixmap(pix.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(icon_label)

        name = QLabel("Dyedfox Radio")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = name.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        name.setFont(f)
        layout.addWidget(name)

        version = QLabel("Version 0.7.2")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setEnabled(False)
        layout.addWidget(version)

        layout.addSpacing(4)

        desc = QLabel(self.tr("Desktop internet radio player.\nPowered by radio-browser.info."))
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(4)

        license_label = QLabel(self.tr("Released under the GPL-3.0 license."))
        license_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        license_label.setEnabled(False)
        layout.addWidget(license_label)

        repo = QLabel('<a href="https://github.com/dyedfox/dyedfox-radio">github.com/dyedfox/dyedfox-radio</a>')
        repo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        repo.setOpenExternalLinks(True)
        layout.addWidget(repo)

        layout.addSpacing(8)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.PaletteChange:
            for w in self.findChildren(QLabel):
                w.update()
        super().changeEvent(event)
