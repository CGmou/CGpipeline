from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QLabel, QHeaderView, QPushButton, QInputDialog, QMessageBox
from PySide6.QtCore import Qt

class ProjectSheet(QWidget):
    def __init__(self, registry):
        super().__init__()
        self.registry = registry
        self.is_admin = False
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)

        title = QLabel("PROJECT MASTER SHEET")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #EEEEEE;")

        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(["Entity / Task", "Category", "Status", "Priority", "Assigned To", "Range", "Date"])
        
        self.tree.header().setSectionResizeMode(QHeaderView.Interactive)
        self.tree.header().setStretchLastSection(True)
        
        self.tree.setStyleSheet("""
            QTreeWidget {
                background-color: #1E1E1E; border: 1px solid #333333; color: #CCCCCC; font-size: 13px; outline: none;
            }
            QTreeWidget::item { padding: 8px; border-bottom: 1px solid #2A2A2A; }
            QHeaderView::section {
                background-color: #2D2D2D; color: #888888; padding: 5px; border: none; font-weight: bold; border-right: 1px solid #333;
            }
            
            QScrollBar:vertical {
                border: none; background: #1A1A1A; width: 10px; margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: #333; min-height: 20px; border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover { background: #444; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            
            QScrollBar:horizontal {
                border: none; background: #1A1A1A; height: 10px; margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:horizontal {
                background: #333; min-width: 20px; border-radius: 5px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
        """)
        self.tree.itemDoubleClicked.connect(self.on_item_double_clicked)

        toolbar = QHBoxLayout()
        self.refresh_btn = QPushButton("REFRESH")
        self.expand_btn = QPushButton("EXPAND ALL")
        self.collapse_btn = QPushButton("COLLAPSE ALL")
        for b in [self.refresh_btn, self.expand_btn, self.collapse_btn]:
            b.setStyleSheet("QPushButton { background-color: #333; color: #AAA; border: 1px solid #444; padding: 5px 15px; border-radius: 4px; font-size: 10px; font-weight: bold; } QPushButton:hover { background-color: #444; color: white; }")

        self.refresh_btn.clicked.connect(lambda: self.refresh(is_admin=self.is_admin))
        self.expand_btn.clicked.connect(self.tree.expandAll)
        self.collapse_btn.clicked.connect(self.tree.collapseAll)

        toolbar.addWidget(title)
        toolbar.addStretch()
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.expand_btn)
        toolbar.addWidget(self.collapse_btn)
        layout.addLayout(toolbar)

        layout.addWidget(self.tree)

    def get_expanded_state(self):
        expanded_paths = []
        def traverse(item, path):
            if item.isExpanded():
                current_path = path + [item.text(0)]
                expanded_paths.append("/".join(current_path))
                for i in range(item.childCount()):
                    traverse(item.child(i), current_path)

        for i in range(self.tree.topLevelItemCount()):
            traverse(self.tree.topLevelItem(i), [])
        return expanded_paths

    def restore_expanded_state(self, expanded_paths):
        def traverse(item, path):
            current_path = path + [item.text(0)]
            if "/".join(current_path) in expanded_paths:
                item.setExpanded(True)
            for i in range(item.childCount()):
                traverse(item.child(i), current_path)

        for i in range(self.tree.topLevelItemCount()):
            traverse(self.tree.topLevelItem(i), [])

    def refresh(self, is_admin=False):
        self.is_admin = is_admin
        # Reload from disk so the sheet reflects status/assignment changes made
        # elsewhere (e.g. status updates pushed from Maya/Blender).
        self.registry.load()
        expanded_paths = self.get_expanded_state()
        self.tree.clear()

        group_display = {
            "Char": "Characters",
            "Props": "Props",
            "Sets": "Sets",
            "Vehicles": "Vehicles"
        }
        
        # Reverse mapping for display logic
        asset_map_rev = {v: k for k, v in group_display.items()}
        # Ensure it handles both ways just in case
        asset_map_rev.update({
            "Char": "Characters",
            "Props": "Props",
            "Sets": "Sets",
            "Vehicles": "Vehicles"
        })


        entities = {}
        for task in self.registry.tasks:
            name = task.get("name", "Unnamed")
            if name not in entities:
                entities[name] = {"category": task.get("category"), "sub_category": task.get("sub_category"), "tasks": []}
            entities[name]["tasks"].append(task)

        categories = ["Assets", "Shots"]
        for cat in categories:
            cat_item = QTreeWidgetItem(self.tree, [cat])
            cat_item.setExpanded(True)
            cat_item.setForeground(0, Qt.gray)

            cat_entities = {k: v for k, v in entities.items() if v["category"] == cat}

            if cat == "Shots":
                shot_groups = {}
                for name, data in cat_entities.items():
                    parts = name.split("_")
                    shot_prefix = parts[0] if parts else "Unknown"
                    if shot_prefix not in shot_groups:
                        shot_groups[shot_prefix] = {}
                    shot_groups[shot_prefix][name] = data

                for shot_prefix in sorted(shot_groups.keys()):
                    prefix_item = QTreeWidgetItem(cat_item, [shot_prefix])
                    prefix_item.setBackground(0, Qt.darkBlue)
                    prefix_item.setForeground(0, Qt.white)

                    sequences = shot_groups[shot_prefix]
                    for seq_name in sorted(sequences.keys()):
                        seq_data = sequences[seq_name]
                        seq_item = QTreeWidgetItem(prefix_item, [seq_name])
                        seq_item.setForeground(0, Qt.cyan)

                        for t in seq_data["tasks"]:
                            f_start = t.get("frame_start", "-")
                            f_end = t.get("frame_end", "-")
                            f_range = str(f_start) + "-" + str(f_end) if f_start != "-" else "-"
                            
                            date_val = t.get("updated_at", "-") if t.get("status") != "Ready" else "-"
                            
                            t_item = QTreeWidgetItem(seq_item, [
                                "   " + str(t.get("type")),
                                "", 
                                t.get("status", "Ready"),
                                t.get("priority", "Normal"),
                                t.get("assigned_to", "-"),
                                f_range,
                                date_val
                            ])
                            t_item.setData(0, Qt.UserRole, t.get("id"))
            else:
                for name in sorted(cat_entities.keys()):
                    data = cat_entities[name]
                    clean_cat = asset_map_rev.get(data["sub_category"], data["sub_category"])
                    entity_item = QTreeWidgetItem(cat_item, [name, clean_cat])
                    entity_item.setBackground(0, Qt.darkCyan)
                    entity_item.setForeground(0, Qt.white)

                    for t in data["tasks"]:
                        date_val = t.get("updated_at", "-") if t.get("status") != "Ready" else "-"
                        
                        t_item = QTreeWidgetItem(entity_item, [
                            "   " + str(t.get("type")),
                            "",
                            t.get("status", "Ready"),
                            t.get("priority", "Normal"),
                            t.get("assigned_to", "-"),
                            "-",
                            date_val
                        ])
                        t_item.setData(0, Qt.UserRole, t.get("id"))

        self.restore_expanded_state(expanded_paths)
        self.tree.setColumnWidth(0, 250)
        self.tree.setColumnWidth(2, 120)
        self.tree.setColumnWidth(3, 100)
        self.tree.setColumnWidth(4, 120)
        self.tree.setColumnWidth(5, 100)

    def on_item_double_clicked(self, item, column):
        task_id = item.data(0, Qt.UserRole)
        if not task_id or not self.is_admin:
            return

        task = next((t for t in self.registry.tasks if t["id"] == task_id), None)
        if not task: return

        if column == 2: # Status
            options = ["Ready", "In Progress", "Pending Review", "Approved", "Omit"]
            current = item.text(column)
            val, ok = QInputDialog.getItem(self, "Quick Edit Status", "Select Status:", options, options.index(current) if current in options else 0, False)
            if ok:
                self.registry.update_task(task_id, status=val)
                self.refresh(is_admin=True)
        elif column == 3: # Priority
            options = ["Low", "Normal", "High", "Critical"]
            current = item.text(column)
            val, ok = QInputDialog.getItem(self, "Quick Edit Priority", "Select Priority:", options, options.index(current) if current in options else 1, False)
            if ok:
                self.registry.update_task(task_id, priority=val)
                self.refresh(is_admin=True)
        elif column == 4: # Assigned To
            users = [u["username"] for u in self.parent().parent().auth.users]
            current = item.text(column)
            val, ok = QInputDialog.getItem(self, "Quick Edit Assignment", "Select User:", users, users.index(current) if current in users else 0, False)
            if ok:
                self.registry.update_task(task_id, assigned_to=val)
                self.refresh(is_admin=True)
        elif column == 5 and task.get("category") == "Shots": # Range
            current = item.text(column)
            val, ok = QInputDialog.getText(self, "Quick Edit Range", "Enter Frame Range (e.g. 1001-1100):", text=current)
            if ok and "-" in val:
                try:
                    start, end = val.split("-")
                    self.registry.update_task(task_id, frame_start=int(start), frame_end=int(end))
                    self.refresh(is_admin=True)
                except:
                    QMessageBox.warning(self, "Invalid Format", "Please use START-END format (e.g. 1001-1100)")
        else:
            self.parent().parent().on_modify_task(task_id)

