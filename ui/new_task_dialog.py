from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLineEdit, QPushButton, QLabel, 
                             QComboBox, QFormLayout, QHBoxLayout, QListWidget, QListWidgetItem,
                             QSpinBox, QStackedWidget, QWidget, QFileDialog, QFrame, QMessageBox)
from PySide6.QtCore import Qt, Signal
import os

class NewTaskDialog(QDialog):
    add_requested = Signal(dict)

    def __init__(self, auth_manager, parent=None):
        super().__init__(parent)
        self.auth = auth_manager
        self.setWindowTitle("Create New Task")
        self.setMinimumWidth(450)
        self.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: #BBBBBB; font-size: 13px; font-family: "Segoe UI"; }
            QLineEdit, QComboBox, QListWidget, QSpinBox {
                background-color: #2D2D2D;
                border: 1px solid #3D3D3D;
                color: white;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton {
                background-color: #0078D4;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #1086E0; }
            #CategoryBtn {
                background-color: #2D2D2D;
                color: #888;
                border: 1px solid #3D3D3D;
                padding: 12px;
                font-size: 14px;
            }
            #CategoryBtn[selected="true"] {
                background-color: #0078D4;
                color: white;
                border: 1px solid #0078D4;
            }
            #SectionTitle {
                color: #555;
                font-weight: bold;
                font-size: 10px;
                text-transform: uppercase;
                margin-top: 10px;
            }
            #NextBtn { background-color: #0078D4; }
            #NextBtn:hover { background-color: #1086E0; }
            #DoneBtn { background-color: #28A745; }
            #DoneBtn:hover { background-color: #218838; }
        """)
        self.category = "Assets"
        self.asset_map = {
            "Character": "Char",
            "Prop": "Props",
            "Set": "Sets",
            "Vehicle": "Vehicles"
        }
        self.thumbnail_path = ""
        self.asset_types = ["Model", "Texture", "Lookdev", "Rig"]
        self.shot_types = ["Blocking", "Animation", "Layout", "Lighting", "FX", "CFX", "Comp", "Assembly", "Setdress"]

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(25, 25, 25, 25)

        title1 = QLabel("1. Select Category")
        title1.setObjectName("SectionTitle")
        layout.addWidget(title1)
        
        cat_layout = QHBoxLayout()
        self.asset_btn = QPushButton("ASSETS")
        self.asset_btn.setObjectName("CategoryBtn")
        self.asset_btn.setProperty("selected", True)
        self.asset_btn.clicked.connect(lambda: self.set_category("Assets"))

        self.shot_btn = QPushButton("SHOTS")
        self.shot_btn.setObjectName("CategoryBtn")
        self.shot_btn.setProperty("selected", False)
        self.shot_btn.clicked.connect(lambda: self.set_category("Shots"))

        cat_layout.addWidget(self.asset_btn)
        cat_layout.addWidget(self.shot_btn)
        layout.addLayout(cat_layout)

        title2 = QLabel("2. Entity Details")
        title2.setObjectName("SectionTitle")
        layout.addWidget(title2)
        
        self.details_container = QFrame()
        self.details_container.setStyleSheet("background-color: #252525; border-radius: 8px;")
        self.details_layout = QFormLayout(self.details_container)
        self.details_layout.setContentsMargins(15, 15, 15, 15)
        self.details_layout.setSpacing(12)

        self.type_label = QLabel("Type:")
        self.asset_group_combo = QComboBox()
        self.asset_group_combo.addItems(list(self.asset_map.keys()))
        self.details_layout.addRow(self.type_label, self.asset_group_combo)

        self.name_label = QLabel("Name:")
        self.name_stack = QStackedWidget()
        
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Hero_Robot")
        self.name_stack.addWidget(self.name_edit)
        
        shot_name_widget = QWidget()
        shot_name_layout = QHBoxLayout(shot_name_widget)
        shot_name_layout.setContentsMargins(0,0,0,0)
        shot_name_layout.setSpacing(5)
        
        label_sh = QLabel("SH")
        label_sh.setStyleSheet("color: #666; font-weight: bold;")
        shot_name_layout.addWidget(label_sh)
        self.shot_num = QSpinBox()
        self.shot_num.setRange(1, 999)
        self.shot_num.setFixedWidth(70)
        shot_name_layout.addWidget(self.shot_num)
        
        label_sq = QLabel("SQ")
        label_sq.setStyleSheet("color: #666; font-weight: bold; margin-left: 10px;")
        shot_name_layout.addWidget(label_sq)
        self.seq_num = QSpinBox()
        self.seq_num.setRange(10, 9999)
        self.seq_num.setSingleStep(10)
        self.seq_num.setValue(10)
        shot_name_layout.addWidget(self.seq_num)
        shot_name_layout.addStretch()
        
        self.name_stack.addWidget(shot_name_widget)
        self.details_layout.addRow(self.name_label, self.name_stack)

        self.frame_label = QLabel("Frame Range:")
        self.frame_widget = QWidget()
        frame_layout = QHBoxLayout(self.frame_widget)
        frame_layout.setContentsMargins(0,0,0,0)
        frame_layout.setSpacing(5)
        
        self.frame_start = QSpinBox()
        self.frame_start.setRange(0, 99999)
        self.frame_start.setValue(1001)
        self.frame_start.setFixedWidth(80)
        
        self.frame_end = QSpinBox()
        self.frame_end.setRange(0, 99999)
        self.frame_end.setValue(1100)
        self.frame_end.setFixedWidth(80)
        
        frame_layout.addWidget(QLabel("Start:"))
        frame_layout.addWidget(self.frame_start)
        frame_layout.addSpacing(10)
        frame_layout.addWidget(QLabel("End:"))
        frame_layout.addWidget(self.frame_end)
        frame_layout.addStretch()
        
        self.details_layout.addRow(self.frame_label, self.frame_widget)
        self.frame_label.setVisible(False)
        self.frame_widget.setVisible(False)

        self.assign_combo = QComboBox()
        self.assign_combo.addItems([u["username"] for u in self.auth.users])
        self.details_layout.addRow("Assign To:", self.assign_combo)

        self.thumb_btn = QPushButton("Browse...")
        self.thumb_btn.setStyleSheet("background-color: #333; color: #AAA; text-align: left; padding-left: 10px;")
        self.thumb_btn.clicked.connect(self.on_pick_thumbnail)
        self.details_layout.addRow("Thumbnail:", self.thumb_btn)

        layout.addWidget(self.details_container)

        tasks_header = QHBoxLayout()
        title3 = QLabel("3. Select Task Types")
        title3.setObjectName("SectionTitle")
        tasks_header.addWidget(title3)
        tasks_header.addStretch()
        
        self.select_all_btn = QPushButton("SELECT ALL")
        self.select_all_btn.setStyleSheet("background: transparent; color: #0078D4; font-size: 10px; font-weight: bold; padding: 0;")
        self.select_all_btn.setFixedWidth(80)
        self.select_all_btn.clicked.connect(self.toggle_all_tasks)
        tasks_header.addWidget(self.select_all_btn)
        
        layout.addLayout(tasks_header)
        
        self.type_list = QListWidget()
        self.type_list.setStyleSheet("""
            QListWidget { background-color: #252525; border: none; border-radius: 8px; outline: none; }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #2D2D2D; color: #CCC; }
            QListWidget::item:hover { background-color: #2A2A2A; }
            QListWidget::item:selected { background-color: #333; color: white; }
            QListWidget::indicator { width: 18px; height: 18px; background-color: #1A1A1A; border: 1px solid #3D3D3D; border-radius: 3px; }
            QListWidget::indicator:checked { background-color: #0078D4; border: 1px solid #0078D4; }
            QListWidget::indicator:unchecked { background-color: #1A1A1A; }
        """)
        self.type_list.itemClicked.connect(self.on_item_clicked)
        
        self.update_task_types()
        layout.addWidget(self.type_list)

        layout.addSpacing(10)
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("NEXT")
        self.add_btn.setObjectName("NextBtn")
        self.add_btn.clicked.connect(self.on_add_clicked)
        
        self.create_btn = QPushButton("DONE")
        self.create_btn.setObjectName("DoneBtn")
        self.create_btn.clicked.connect(self.on_done_clicked)
        
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.create_btn)
        layout.addLayout(btn_layout)

    def on_item_clicked(self, item):
        if item.checkState() == Qt.Checked:
            item.setCheckState(Qt.Unchecked)
        else:
            item.setCheckState(Qt.Checked)

    def toggle_all_tasks(self):
        any_unticked = False
        for i in range(self.type_list.count()):
            if self.type_list.item(i).checkState() == Qt.Unchecked:
                any_unticked = True
                break
        
        new_state = Qt.Checked if any_unticked else Qt.Unchecked
        for i in range(self.type_list.count()):
            self.type_list.item(i).setCheckState(new_state)
        
        self.select_all_btn.setText("UNSELECT ALL" if any_unticked else "SELECT ALL")

    def on_pick_thumbnail(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select Thumbnail", "", "Images (*.png *.jpg *.jpeg)")
        if p:
            self.thumbnail_path = p
            self.thumb_btn.setText(os.path.basename(p))

    def validate(self, data):
        if not data["name"].strip():
            QMessageBox.warning(self, "Required Field", "Please provide a name for the entity.")
            return False
        if not data["types"]:
            QMessageBox.warning(self, "Required Field", "Please select at least one task type.")
            return False
        return True

    def on_add_clicked(self):
        data = self.get_data()
        if self.validate(data):
            self.add_requested.emit(data)
            self.clear_inputs()

    def on_done_clicked(self):
        data = self.get_data()
        # If user filled nothing, just close. If they filled something, validate.
        if not data["name"].strip() and not data["types"]:
             self.reject()
             return
             
        if self.validate(data):
            self.accept()

    def clear_inputs(self):
        if self.category == "Assets":
            self.name_edit.clear()
        else:
            self.seq_num.setValue(self.seq_num.value() + 10)
        for i in range(self.type_list.count()):
            self.type_list.item(i).setCheckState(Qt.Unchecked)
        self.select_all_btn.setText("SELECT ALL")

    def set_category(self, cat):
        self.category = cat
        self.asset_btn.setProperty("selected", cat == "Assets")
        self.shot_btn.setProperty("selected", cat == "Shots")
        self.asset_btn.style().unpolish(self.asset_btn)
        self.asset_btn.style().polish(self.asset_btn)
        self.shot_btn.style().unpolish(self.shot_btn)
        self.shot_btn.style().polish(self.shot_btn)

        self.name_stack.setCurrentIndex(0 if cat == "Assets" else 1)
        
        is_asset = (cat == "Assets")
        self.asset_group_combo.setVisible(is_asset)
        self.type_label.setVisible(is_asset)
        self.name_label.setText("Name:" if is_asset else "Shot Name:")
        self.frame_label.setVisible(not is_asset)
        self.frame_widget.setVisible(not is_asset)
        
        self.update_task_types()
        self.select_all_btn.setText("SELECT ALL")

    def update_task_types(self):
        self.type_list.clear()
        types = self.asset_types if self.category == "Assets" else self.shot_types
        for t in types:
            item = QListWidgetItem(t)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.type_list.addItem(item)

    def get_data(self):
        if self.category == "Assets":
            sub_cat = self.asset_map.get(self.asset_group_combo.currentText(), "02_Props")
            name = self.name_edit.text()
            frame_range = None
        else:
            sub_cat = "02_Shots"
            name = "SH" + str(self.shot_num.value()).zfill(2) + "_SQ" + str(self.seq_num.value()).zfill(4)
            frame_range = (self.frame_start.value(), self.frame_end.value())

        selected_types = []
        for i in range(self.type_list.count()):
            item = self.type_list.item(i)
            if item.checkState() == Qt.Checked:
                selected_types.append(item.text())

        return {
            "category": self.category,
            "sub_category": sub_cat,
            "name": name,
            "types": selected_types,
            "thumbnail": self.thumbnail_path,
            "assigned_to": self.assign_combo.currentText(),
            "frame_range": frame_range
        }

