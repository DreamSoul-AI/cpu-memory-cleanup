"""次级范围、方式与规则窗口；复用主窗工作线程及Package。"""

from dataclasses import asdict
from pathlib import Path
import time

from ..contracts import ActionRequest
from ..application.targets import (select_rows, targets_from_rows, executable_groups,
                                   target_key, protected_reason, plan_targets)
from ..application.policy import Policy, Rule
from ..application.settings import validate_preferences


class Workspace:
    def __init__(self, owner, *, settings_path=None):
        import tkinter as tk
        from tkinter import ttk, messagebox
        self.owner, self.app, self.messagebox = owner, owner.app, messagebox
        self.store = self.app.preferences_factory(settings_path or Path.home() / ".cpu-memory-cleanup" / "preferences.json")
        self.policy, self.rows, self.selected = Policy(), [], set()
        self.armed_targets, self.armed_rules = (), []
        self.last_tick = 0
        self.tree_rows = {}
        try:
            self.settings = self.store.load()
            error = ""
        except (OSError, ValueError, TypeError) as exc:
            self.settings = validate_preferences({"schema_version": 1})
            error = f"偏好读取失败，未覆盖原文件：{exc}"
        self.window = tk.Toplevel(owner.root)
        self.window.title("整理范围与规则")
        self.window.geometry(self.settings.get("geometry") or "860x640")
        self.window.minsize(720, 540)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="选择处理对象", font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        tools = ttk.Frame(frame)
        tools.grid(row=1, column=0, sticky="ew", pady=10)
        ttk.Button(tools, text="读取 / 刷新", command=self.refresh).pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(tools, textvariable=self.query, width=24).pack(side="left", padx=8)
        self.query.trace_add("write", lambda *unused: self.render())
        self.descending = tk.BooleanVar(value=self.settings["descending"])
        ttk.Checkbutton(tools, text="占用从高到低", variable=self.descending, command=self.render).pack(side="left")
        self.grouped = tk.BooleanVar(value=False)
        ttk.Checkbutton(tools, text="程序文件分组", variable=self.grouped, command=self.render).pack(side="left", padx=8)
        tree_frame = ttk.Frame(frame)
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=("pid", "working_set", "protection"), selectmode="extended")
        for col, title, width in (("#0", "程序 / 完整路径", 370), ("pid", "PID", 65),
                                  ("working_set", "工作集", 100), ("protection", "保护", 130)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=60)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="ew", pady=8)
        self.clean_button = ttk.Button(actions, text="整理选中对象", command=self.clean_selected)
        self.clean_button.pack(side="left")
        ttk.Button(actions, text="整理未保护对象…", command=self.clean_unprotected).pack(side="left", padx=6)
        ttk.Button(actions, text="保护选中程序文件", command=self.protect).pack(side="left")
        ttk.Button(actions, text="管理保护名单", command=self.protections).pack(side="left", padx=6)
        ttk.Button(actions, text="停止后续项", command=owner.cancel).pack(side="left")
        self.count = tk.StringVar(value="未读取目标；可多选。分组仅表示同一程序文件，不保证整个应用。")
        ttk.Label(frame, textvariable=self.count, wraplength=780).grid(row=4, column=0, sticky="w")
        notebook = ttk.Notebook(frame)
        notebook.grid(row=5, column=0, sticky="ew", pady=8)
        methods, rules, resident = (ttk.Frame(notebook, padding=8) for _ in range(3))
        notebook.add(methods, text="清理方式")
        notebook.add(rules, text="规则（默认关闭）")
        notebook.add(resident, text="常驻与偏好")
        ttk.Label(methods, text="工作集：整理页面驻留，后续访问可能回升。选中/未保护对象走逐进程路径。\n主窗绿色按钮为全局方式，不支持排除。Standby仅为高级系统范围，可能增加读盘。",
                  wraplength=760).pack(anchor="w")
        ttk.Button(methods, text="高级：一次系统Standby整理…", command=self.standby).pack(anchor="w", pady=5)
        config = self.settings.get("rule", asdict(Rule()))
        self.mode = tk.StringVar(value=config["mode"])
        self.interval = tk.StringVar(value=str(config["interval_s"]))
        self.threshold = tk.StringVar(value=str(config["available_pct"]))
        self.sustain = tk.StringVar(value=str(config["sustain_s"]))
        self.cooldown = tk.StringVar(value=str(config["cooldown_s"]))
        ttk.Combobox(rules, textvariable=self.mode, values=("interval", "pressure"), state="readonly", width=10).grid(row=0, column=0)
        for col, (label, variable) in enumerate((("间隔秒", self.interval), ("可用%低于", self.threshold),
                                               ("持续秒", self.sustain), ("冷却秒", self.cooldown)), 1):
            ttk.Label(rules, text=label).grid(row=0, column=col * 2 - 1, padx=3)
            ttk.Entry(rules, textvariable=variable, width=6).grid(row=0, column=col * 2)
        ttk.Button(rules, text="本次启用：仅当前选中快照…", command=self.arm).grid(row=1, column=0, columnspan=4, sticky="w", pady=6)
        ttk.Button(rules, text="停用规则", command=self.stop).grid(row=1, column=4, columnspan=2)
        ttk.Label(rules, text="interval=定时；pressure=持续低可用比例。数值是触发条件，不是提速保证。重启后不自动启用。",
                  wraplength=760).grid(row=2, column=0, columnspan=9, sticky="w")
        self.resident = tk.BooleanVar(value=self.settings["resident"])
        ttk.Checkbutton(resident, text="启用可选托盘入口（关闭主窗仍退出，收起必须单独点击）", variable=self.resident,
                        command=self.set_resident).pack(anchor="w")
        controls = ttk.Frame(resident)
        controls.pack(anchor="w", pady=5)
        ttk.Button(controls, text="保存偏好", command=self.save).pack(side="left")
        ttk.Button(controls, text="收起到托盘", command=owner.hide_to_tray).pack(side="left", padx=8)
        ttk.Button(controls, text="退出程序并停用规则", command=owner.close).pack(side="left")
        self.note = tk.StringVar(value=error or "待操作。列表按需读取，点击才执行；规则需要本次明确启用。")
        ttk.Label(frame, textvariable=self.note, wraplength=780).grid(row=6, column=0, sticky="ew")
        self.rendering = False
        self.render()

    def show(self):
        self.window.deiconify()
        self.window.lift()

    def hide(self):
        self.window.withdraw()

    def refresh(self):
        if self.owner._submit(self.app.memory_targets):
            self.note.set("正在读取目标，不执行整理…")

    def render(self):
        self.rendering = True
        try:
            self.tree.delete(*self.tree.get_children())
            self.tree_rows.clear()
            visible = select_rows(self.rows, self.query.get(), descending=self.descending.get())
            rules = self.settings["protections"]
            if self.grouped.get():
                groups = executable_groups(visible)
                for n, group in enumerate(groups):
                    group_id = f"g{n}"
                    self.tree.insert("", "end", iid=group_id, text=(group[0].image_path or group[0].name) + f"（{len(group)}项）", open=True)
                    self.tree_rows[group_id] = group
                    for target in group:
                        self.insert_target(target, group_id, rules)
            else:
                for target in targets_from_rows(visible):
                    self.insert_target(target, "", rules)
            self.tree.selection_set([key for key, values in self.tree_rows.items()
                                     if not key.startswith("g") and target_key(values[0]) in self.selected])
        finally:
            self.rendering = False
        self.count.set(f"当前列表 {len(self.rows)} 项 · 选中 {len(self.selected)} 项 · 保护规则 {len(self.settings['protections'])} 条")

    def insert_target(self, target, parent, rules):
        key = f"p{target.pid}_{target.created_at}"
        row = next(row for row in self.rows if (row["pid"], row["created_at"]) == target_key(target))
        amount = row.get("working_set_bytes")
        label = "未知" if amount is None else f"{amount / 1024**2:.1f} MiB"
        self.tree.insert(parent, "end", iid=key, text=target.image_path or target.name,
            values=(target.pid, label, protected_reason(target, rules)))
        self.tree_rows[key] = [target]

    def selection_changed(self, event=None):
        if self.rendering:
            return
        visible = {target_key(t) for values in self.tree_rows.values() for t in values}
        chosen = {target_key(t) for key in self.tree.selection() for t in self.tree_rows.get(key, [])}
        self.selected = (self.selected - visible) | chosen
        self.count.set(f"当前列表 {len(self.rows)} 项 · 选中 {len(self.selected)} 项 · 保护规则 {len(self.settings['protections'])} 条")

    def chosen(self):
        return tuple(t for t in targets_from_rows(self.rows) if target_key(t) in self.selected)

    def submit_batch(self, targets, *, automatic=False):
        if self.owner._worker is not None or self.app.busy or self.owner._closing:
            self.note.set("当前忙碌，等待当前请求收尾。")
            return False
        rules = self.armed_rules if automatic else self.settings["protections"]
        try:
            planned, _ = plan_targets(targets, rules)
        except ValueError as error:
            self.note.set(str(error))
            return False
        if not automatic:
            included = [row for row in planned if not row["reason"]]
            summary = "\n".join(f"PID {row['target']['pid']} {row['target']['name']}" for row in included[:12])
            if not self.messagebox.askyesno("确认工作集批次", f"本次目标{len(planned)}项，跳过{len(planned)-len(included)}项。\n{summary}\n只整理列出的目标，不结束程序；可能产生缺页和I/O。", parent=self.window):
                return False
        return self.owner._submit(self.app.memory_batch_cleanup, targets, self.owner._output_dir,
            protections=rules, confirmed=True, confirmation_source="desktop:rule" if automatic else "desktop:batch-button")

    def clean_selected(self):
        self.submit_batch(self.chosen())

    def clean_unprotected(self):
        self.submit_batch(targets_from_rows(self.rows))

    def protect(self):
        targets = self.chosen()
        if not targets:
            self.note.set("请先选择需要保护的程序。")
            return
        self.stop()
        for target in targets:
            rule = {"image_path": target.image_path} if target.image_path else {"pid": target.pid, "created_at": target.created_at}
            if rule not in self.settings["protections"]:
                self.settings["protections"].append(rule)
        self.save()
        self.render()

    def protections(self):
        import tkinter as tk
        from tkinter import ttk
        dialog = tk.Toplevel(self.window)
        dialog.title("保护名单")
        dialog.geometry("680x300")
        listing = tk.Listbox(dialog, selectmode="extended", exportselection=False)
        listing.pack(fill="both", expand=True, padx=12, pady=12)
        for rule in self.settings["protections"]:
            listing.insert("end", rule.get("image_path") or f"仅实例 PID {rule['pid']} / {rule['created_at']}")
        def remove():
            self.stop()
            for index in reversed(listing.curselection()):
                del self.settings["protections"][index]
                listing.delete(index)
            self.save()
            self.render()
        ttk.Button(dialog, text="移除选中保护", command=remove).pack(pady=6)

    def read_rule(self):
        return Rule(self.mode.get(), float(self.interval.get()), float(self.threshold.get()),
                    float(self.sustain.get()), float(self.cooldown.get()))

    def arm(self):
        try:
            targets = self.chosen()
            plan_targets(targets, self.settings["protections"])
            rule = self.read_rule()
            if not self.messagebox.askyesno("本次启用规则", f"仅处理当前选中的{len(targets)}个进程身份快照，遵守当前保护规则。\n规则可能自动读取内存及执行整理；身份失效不会替换为新进程。\n重启后关闭，退出即停用。是否启用？", parent=self.window):
                return
            self.policy = Policy(rule)
            self.armed_targets = targets
            self.armed_rules = [dict(r) for r in self.settings["protections"]]
            self.policy.arm(time.monotonic(), confirmed=True, source="desktop:explicit-rule-enable")
            self.note.set(f"规则已启用：固定{len(targets)}个身份快照；手动修改选择不会扩展本次范围。")
        except (ValueError, TypeError) as error:
            self.note.set(str(error))

    def stop(self):
        self.policy.stop()
        self.note.set("规则已停用；已经派发的动作仍等待收尾。")

    def tick(self):
        now = time.monotonic()
        if now - self.last_tick < 1 or not self.policy.armed:
            return
        self.last_tick = now
        shown = self.owner._shown or {}
        total, available = shown.get("total"), shown.get("available")
        pct = available / total * 100 if total and available is not None else None
        # 压力规则只能使用最新实际报告，不能以动画插值证明持续压力。
        snapshot = getattr(self.owner, "_rule_snapshot", None)
        pct = None
        if snapshot is not None and now - snapshot[0] <= 2:
            pct = snapshot[1]
        try:
            if self.policy.due(now, available_pct=pct, busy=self.app.busy or self.owner._worker is not None):
                if self.submit_batch(self.armed_targets, automatic=True):
                    self.policy.dispatched(now)
        except (ValueError, RuntimeError) as error:
            self.stop()
            self.note.set(str(error))

    def standby(self):
        if "standby" not in self.app.capabilities.mechanisms:
            self.note.set("当前接入不支持Standby。")
            return
        if self.messagebox.askyesno("系统范围高级动作", "此操作不支持任何应用保护或进程排除，可能增加磁盘读取。\n确认执行一次系统Standby整理？", parent=self.window):
            self.owner._submit(self.app.maintain, ActionRequest("standby", confirmed=True,
                confirmation_source="desktop:advanced-standby"), self.owner._output_dir, lightweight=True)

    def save(self):
        try:
            self.settings.update(descending=self.descending.get(), resident=self.resident.get(),
                                 geometry=self.window.geometry(), rule=asdict(self.read_rule()))
            self.store.save(self.settings)
            self.note.set("偏好已保存；本次规则启用状态不保存。")
        except (OSError, ValueError, TypeError) as error:
            self.note.set(f"偏好未保存：{error}")

    def set_resident(self):
        try:
            self.owner.enable_tray(self.resident.get())
            self.save()
        except (OSError, AttributeError, RuntimeError) as error:
            self.resident.set(False)
            self.note.set(f"托盘未启用，窗口保留：{error}")

    def on_report(self, report):
        if report.mode == "targets":
            self.rows = report.samples[0].processes if report.samples else []
            identities = {target_key(t) for t in targets_from_rows(self.rows)}
            self.selected.intersection_update(identities)
            self.render()
            self.note.set(f"列表读取：{report.completion}；不可读项不代表不存在。")
        elif report.mode == "batch":
            lines = []
            for item in report.metadata.get("items", []):
                summary = item.get("analysis") or {}
                change = summary.get("working_set_delta_bytes")
                label = "未知" if change is None else f"{change / 1024**2:+.1f} MiB"
                lines.append(f"PID {item['target']['pid']}: {item['status']} · 工作集变化 {label} {item.get('reason', '')}")
            self.owner.result.configure(text="批次整理：" + report.completion)
            self.owner.result_note.configure(text="逐项结果见范围窗口；不累加为系统释放量。")
            self.note.set(f"批次 {report.completion}；记录：{report.output_dir}")
            import tkinter as tk
            dialog = tk.Toplevel(self.window)
            dialog.title("批次结果")
            text = tk.Text(dialog, width=90, height=20, wrap="word")
            text.pack(fill="both", expand=True)
            text.insert("1.0", "\n".join(lines) + "\n" + str(report.errors))
            text.configure(state="disabled")
            if report.completion != "success" and self.policy.armed:
                self.stop()
                self.note.set("批次未完整成功，规则已停用；请核对原记录，不自动重试。")
