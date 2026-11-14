"""Dark modern Qt stylesheet for VidEdit."""

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #111318;
    color: #e6e9ef;
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}

QFrame#Card, QFrame#SidebarCard {
    background-color: #181b22;
    border: 1px solid #262b36;
    border-radius: 10px;
}

QFrame#VideoFrame {
    background-color: #090a0d;
    border: 1px solid #262b36;
    border-radius: 10px;
}

QLabel#SectionTitle {
    font-size: 14px;
    font-weight: 600;
    color: #f3f5f8;
}

QLabel#MutedLabel {
    color: #8f98a8;
    font-size: 12px;
}

QLabel#BadgeLabel {
    background-color: #20242e;
    border: 1px solid #2e3442;
    border-radius: 6px;
    padding: 4px 10px;
    color: #dce1eb;
    font-family: "Consolas", "Cascadia Mono", monospace;
    font-size: 12px;
}

QLabel#SuccessBanner {
    background-color: #152b22;
    border: 1px solid #245c43;
    border-radius: 8px;
    padding: 8px 10px;
    color: #8ce9b5;
    font-size: 12px;
}

QPushButton {
    background-color: #232732;
    border: 1px solid #323846;
    border-radius: 7px;
    padding: 7px 13px;
    color: #e6e9ef;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #2c313f;
    border-color: #434b5e;
}

QPushButton:pressed {
    background-color: #1c2029;
}

QPushButton:disabled {
    background-color: #1a1d24;
    color: #5b6270;
    border-color: #232730;
}

QPushButton[primary="true"] {
    background-color: #5865f2;
    border-color: #6a75f5;
    color: #ffffff;
    font-weight: 600;
    padding: 10px 16px;
}

QPushButton[primary="true"]:hover {
    background-color: #6672f5;
}

QPushButton[primary="true"]:pressed {
    background-color: #4752c4;
}

QPushButton[primary="true"]:disabled {
    background-color: #2b3054;
    border-color: #323861;
    color: #7d85a8;
}

QPushButton[pill="true"] {
    padding: 6px 10px;
    border-radius: 7px;
    background-color: #1f232d;
    border: 1px solid #2e3442;
}

QPushButton[pill="true"]:checked {
    background-color: #5865f2;
    border-color: #707bf7;
    color: #ffffff;
    font-weight: 600;
}

QLineEdit, QComboBox, QDoubleSpinBox {
    background-color: #12151b;
    border: 1px solid #2d3340;
    border-radius: 6px;
    padding: 6px 9px;
    color: #e6e9ef;
}

QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
    border-color: #5865f2;
}

QComboBox::drop-down {
    border: none;
    width: 22px;
}

QComboBox QAbstractItemView {
    background-color: #181b22;
    border: 1px solid #2d3340;
    selection-background-color: #5865f2;
    color: #e6e9ef;
}

QCheckBox {
    spacing: 8px;
    color: #dce1eb;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #3c4354;
    background-color: #12151b;
}

QCheckBox::indicator:checked {
    background-color: #5865f2;
    border-color: #707bf7;
}

QProgressBar {
    background-color: #12151b;
    border: 1px solid #2a303c;
    border-radius: 6px;
    text-align: center;
    height: 18px;
    color: #ffffff;
    font-size: 11px;
    font-weight: 600;
}

QProgressBar::chunk {
    background-color: #5865f2;
    border-radius: 5px;
}

QSlider::groove:horizontal {
    height: 5px;
    background: #282d39;
    border-radius: 2px;
}

QSlider::sub-page:horizontal {
    background: #5865f2;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #e6e9ef;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}
"""
