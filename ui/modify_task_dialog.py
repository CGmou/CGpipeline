from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLineEdit, QPushButton, QLabel, 
                             QComboBox, QFormLayout, QHBoxLayout, QListWidget, QListWidgetItem, QFileDialog, QScrollArea, QWidget, QFrame, QCheckBox)
from PySide6.QtCore import Qt, Signal
import os
from core.constants import TASK_STATUSES, DEFAULT_STATUS

class TaskRow(QFrame):
    def __init__(self, task_obj, users):
        super().__init__()
        self.task_obj = task_obj
        self.setObjectName("TaskRow")
        self.setStyleSheet("""
            #TaskRow { background-color: #252525; border-radius: 4px; padding: 5px; border: 1px solid transparent; }
            #TaskRow:hover { background-color: #303030; border: 1px solid #444; }
            QCheckBox::indicator { width: 18px; height: 18px; background-color: #1A1A1A; border: 1px solid #3D3D3D; border-radius: 3px; }
            QCheckBox::indicator:checked { background-color: #0078D4; }
        """)
        layout = QHBoxLayout(self)

        self.checkbox = QCheckBox()
        self.checkbox.setFixedWidth(30)
        layout.addWidget(self.checkbox)

        self.type_label = QLabel(task_obj["type"])
        self.type_label.setFixedWidth(80)
        self.type_label.setStyleSheet("font-weight: bold; color: #0078D4;")
        layout.addWidget(self.type_label)

        self.status_combo = QComboBox()
        self.status_combo.addItems(TASK_STATUSES)
        self.status_combo.setCurrentText(task_obj.get("status", DEFAULT_STATUS))
        layout.addWidget(self.status_combo)

        self.priority_combo = QComboBox()
        self.priority_combo.addItems(["Low", "Normal", "High", "Critical"])
        self.priority_combo.setCurrentText(task_obj.get("priority", "Normal"))
        layout.addWidget(self.priority_combo)

        self.assign_combo = QComboBox()
        self.assign_combo.addItems(users)
        self.assign_combo.setCurrentText(task_obj.get("assigned_to", ""))
        layout.addWidget(self.assign_combo)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.childAt(event.pos()) in [None, self.checkbox]:
                self.checkbox.setChecked(not self.checkbox.isChecked())
        super().mousePressEvent(event)

    def is_selected(self):
        return self.checkbox.isChecked()

    def get_data(self):
        data = {
            "id": self.task_obj["id"],
            "status": self.status_combo.currentText(),
            "priority": self.priority_combo.currentText(),
            "assigned_to": self.assign_combo.currentText()
        }
        return data

class ModifyTaskDialog(QDialog):
    add_type_requested = Signal()
    delete_selected_requested = Signal(list)
    delete_all_requested = Signal()

    def __init__(self, name, tasks, auth_manager, parent=None):
        super().__init__(parent)
        self.auth = auth_manager
        self.setWindowTitle("Edit Tasks: " + name)
        self.entity_name = name
        self.tasks = tasks
        self.setMinimumWidth(550)
        self.setMinimumHeight(450)
        self.thumbnail_path = tasks[0].get("thumbnail", "") if tasks else ""

        self.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: #BBBBBB; font-size: 13px; }
            QLineEdit, QComboBox, QListWidget {
                background-color: #2D2D2D; border: 1px solid #3D3D3D; color: white; padding: 6px; border-radius: 4px;
            }
            QPushButton {
                background-color: #0078D4; color: white; border: none; padding: 10px; border-radius: 4px; font-weight: bold;
            }
            #DeleteBtn { background-color: #A72828; }
            #DeleteSelectedBtn { background-color: #721c24; color: #f8d7da; border: 1px solid #f5c6cb; }
            #DeleteSelectedBtn:hover { background-color: #a72828; color: white; }
        """)
        self.setup_ui()

    def setup_ui(self):
        if self.layout():
            QWidget().setLayout(self.layout())

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        top_layout = QHBoxLayout()
        form = QFormLayout()
        self.name_edit = QLineEdit(self.entity_name)
        form.addRow("Entity Name:", self.name_edit)
        self.thumb_btn = QPushButton("Change Thumbnail")
        if self.thumbnail_path:
            self.thumb_btn.setText(os.path.basename(self.thumbnail_path))
        self.thumb_btn.clicked.connect(self.on_pick_thumbnail)
        form.addRow("Thumbnail:", self.thumb_btn)
        top_layout.addLayout(form)
        layout.addLayout(top_layout)

        layout.addWidget(QLabel("INDIVIDUAL TASKS CONTROL:"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        task_container = QWidget()
        self.task_layout = QVBoxLayout(task_container)

        self.task_rows = []
        users = [u["username"] for u in self.auth.users]
        for task in self.tasks:
            row = TaskRow(task, users)
            self.task_rows.append(row)
            self.task_layout.addWidget(row)

        self.task_layout.addStretch()
        scroll.setWidget(task_container)
        layout.addWidget(scroll)

        btn_layout = QVBoxLayout()
        action_row = QHBoxLayout()
        self.delete_selected_btn = QPushButton("- REMOVE")
        self.delete_selected_btn.setObjectName("DeleteSelectedBtn")
        self.delete_selected_btn.clicked.connect(self.on_delete_selected)
        
        self.add_task_btn = QPushButton("+ ADD")
        self.add_task_btn.setStyleSheet("background-color: #28A745;")
        self.add_task_btn.clicked.connect(self.add_type_requested.emit)
        
        action_row.addWidget(self.delete_selected_btn)
        action_row.addWidget(self.add_task_btn)
        btn_layout.addLayout(action_row)

        footer_row = QHBoxLayout()
        self.delete_all_btn = QPushButton("DELETE ALL")
        self.delete_all_btn.setObjectName("DeleteBtn")
        self.delete_all_btn.clicked.connect(self.delete_all_requested.emit)
        
        self.save_btn = QPushButton("SAVE")
        self.save_btn.clicked.connect(self.accept)
        
        footer_row.addWidget(self.delete_all_btn)
        footer_row.addWidget(self.save_btn)
        btn_layout.addLayout(footer_row)
        layout.addLayout(btn_layout)

    def refresh_tasks(self, new_tasks):
        self.tasks = new_tasks
        self.setup_ui()

    def on_pick_thumbnail(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select Thumbnail", "", "Images (*.png *.jpg *.jpeg)")
        if p:
            self.thumbnail_path = p
            self.thumb_btn.setText(os.path.basename(p))

    def on_delete_selected(self):
        selected_ids = [row.task_obj["id"] for row in self.task_rows if row.is_selected()]
        if not selected_ids:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "No Selection", "Please select at least one task to delete.")
            return
        self.delete_selected_requested.emit(selected_ids)

    def get_data(self):
        return {
            "name": self.name_edit.text(),
            "thumbnail": self.thumbnail_path,
            "tasks": [row.get_data() for row in self.task_rows]
        }

