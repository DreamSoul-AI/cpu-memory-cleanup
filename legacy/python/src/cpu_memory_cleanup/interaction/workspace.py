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
        self.window.title("内存清理 · 详情与设置")
        self.window.geometry(self.settings.get("geometry") or "860x650")
        self.window.minsize(740, 560)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        style = ttk.Style(self.window)
        style.configure("Cleanup.TFrame", background="#F3F7F4")
        style.configure("Cleanup.TLabel", background="#F3F7F4", foreground="#426451", font=("Microsoft YaHei UI", 10))
        style.configure("Cleanup.TButton", padding=(12, 7), font=("Microsoft YaHei UI", 10))
        style.configure("Cleanup.Treeview", rowheight=32, font=("Microsoft YaHei UI", 10))
        shell = ttk.Frame(self.window, padding=18, style="Cleanup.TFrame")
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)
        ttk.Label(shell, text="详情与设置", style="Cleanup.TLabel", font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 14))
        self.notebook = ttk.Notebook(shell)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        frame, settings_page, runtime = (ttk.Frame(self.notebook, padding=14, style="Cleanup.TFrame") for _ in range(3))
        self.notebook.add(frame, text="进程范围")
        self.notebook.add(settings_page, text="整理设置")
        self.notebook.add(runtime, text="运行详情")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text="按需读取，明确选择后才整理", style="Cleanup.TLabel").grid(row=0, column=0, sticky="w")
        tools = ttk.Frame(frame, style="Cleanup.TFrame")
        tools.grid(row=1, column=0, sticky="ew", pady=10)
        ttk.Button(tools, text="读取 / 刷新", command=self.refresh, style="Cleanup.TButton").pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(tools, textvariable=self.query, width=20).pack(side="left", padx=8)
        self.query.trace_add("write", lambda *unused: self.render())
        self.descending = tk.BooleanVar(value=self.settings["descending"])
        ttk.Checkbutton(tools, text="工作集降序", variable=self.descending, command=self.render).pack(side="left")
        self.grouped = tk.BooleanVar(value=False)
        ttk.Checkbutton(tools, text="程序文件分组", variable=self.grouped, command=self.render).pack(side="left", padx=8)
        tree_frame = ttk.Frame(frame)
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=("pid", "working_set", "protection"), selectmode="extended", height=8, style="Cleanup.Treeview")
        for col, title, width in (("#0", "程序 / 完整路径", 320), ("pid", "PID", 65), ("working_set", "工作集", 90), ("protection", "保护", 120)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=60)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        actions = ttk.Frame(frame, style="Cleanup.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=10)
        self.clean_button = ttk.Button(actions, text="整理选中对象", command=self.clean_selected, style="Cleanup.TButton")
        self.clean_button.grid(row=0, column=0, padx=(0, 6), pady=4)
        for column, (title, command) in enumerate((("整理未保护对象…", self.clean_unprotected), ("保护选中程序", self.protect), ("管理保护名单", self.protections), ("停止后续项", owner.cancel)), 1):
            ttk.Button(actions, text=title, command=command, style="Cleanup.TButton").grid(row=column//3, column=column%3, padx=(0, 6), pady=4)
        self.count = tk.StringVar(value="未读取目标；可多选。文件组不保证覆盖整个应用。")
        ttk.Label(frame, textvariable=self.count, wraplength=670, style="Cleanup.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(settings_page, text="主按钮整理全局工作集，不支持应用排除。保护仅用于逐进程批次。", wraplength=670, style="Cleanup.TLabel").pack(anchor="w", pady=(0, 10))
        notebook = ttk.Notebook(settings_page)
        notebook.pack(fill="both", expand=True)
        methods, rules, resident = (ttk.Frame(notebook, padding=14, style="Cleanup.TFrame") for _ in range(3))
        notebook.add(methods, text="方式与保护")
        notebook.add(rules, text="自动规则 · 默认关闭")
        notebook.add(resident, text="偏好")
        ttk.Label(methods, text="工作集：整理页面驻留，后续访问可能回升。\nStandby：仅为高级系统动作，不能排除应用，可能增加读盘。", wraplength=620, style="Cleanup.TLabel").pack(anchor="w")
        ttk.Button(methods, text="高级：一次系统Standby整理…", command=self.standby, style="Cleanup.TButton").pack(anchor="w", pady=12)
        ttk.Button(methods, text="管理保护名单", command=self.protections, style="Cleanup.TButton").pack(anchor="w")
        config = self.settings.get("rule", asdict(Rule()))
        self.mode = tk.StringVar(value=config["mode"])
        self.interval = tk.StringVar(value=str(config["interval_s"]))
        self.threshold = tk.StringVar(value=str(config["available_pct"]))
        self.sustain = tk.StringVar(value=str(config["sustain_s"]))
        self.cooldown = tk.StringVar(value=str(config["cooldown_s"]))
        ttk.Label(rules, text="触发方式", style="Cleanup.TLabel").grid(row=0, column=0, sticky="w", pady=8)
        ttk.Combobox(rules, textvariable=self.mode, values=("interval", "pressure"), state="readonly", width=16).grid(row=0, column=1, sticky="w")
        for row, (label, variable) in enumerate((("间隔（秒）", self.interval), ("可用比例低于（%）", self.threshold), ("持续（秒）", self.sustain), ("冷却（秒）", self.cooldown)), 1):
            ttk.Label(rules, text=label, style="Cleanup.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 18), pady=6)
            ttk.Entry(rules, textvariable=variable, width=18).grid(row=row, column=1, sticky="w")
        ttk.Button(rules, text="本次启用：仅选中快照…", command=self.arm, style="Cleanup.TButton").grid(row=5, column=0, sticky="w", pady=12)
        ttk.Button(rules, text="停用规则", command=self.stop, style="Cleanup.TButton").grid(row=5, column=1, sticky="w")
        ttk.Label(rules, text="interval=定时；pressure=持续低可用比例。重启不自动启用。", wraplength=620, style="Cleanup.TLabel").grid(row=6, column=0, columnspan=2, sticky="w")
        self.resident = tk.BooleanVar(value=self.settings["resident"])
        ttk.Label(resident, text="偏好可保存，规则启用状态不保存。可选托盘沿用既有原型，本轮未扩展。", wraplength=620, style="Cleanup.TLabel").pack(anchor="w", pady=10)
        ttk.Checkbutton(resident, text="保留可选托盘入口", variable=self.resident, command=self.set_resident).pack(anchor="w")
        ttk.Button(resident, text="保存偏好", command=self.save, style="Cleanup.TButton").pack(anchor="w", pady=10)
        runtime.columnconfigure(0, weight=1)
        runtime.rowconfigure(2, weight=1)
        self.runtime_context = tk.StringVar(value="")
        ttk.Label(runtime, textvariable=self.runtime_context, wraplength=670, style="Cleanup.TLabel").grid(row=0, column=0, sticky="w", pady=8)
        self.review_button = ttk.Button(runtime, text="离线回看最近记录", command=self.review, style="Cleanup.TButton")
        self.review_button.grid(row=1, column=0, sticky="w", pady=8)
        self.report_text = tk.Text(runtime, width=60, height=10, wrap="word", relief="flat", bg="white", fg="#355A46", font=("Microsoft YaHei UI", 10), padx=12, pady=12)
        self.report_text.grid(row=2, column=0, sticky="nsew")
        self.note = tk.StringVar(value=error or "列表按需读取，展开详情不会开始清理。")
        ttk.Label(shell, textvariable=self.note, wraplength=700, style="Cleanup.TLabel").grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self.rendering = False
        self.render()
        self.refresh_runtime()
        self.window.update_idletasks()
        width, height = max(740, self.window.winfo_reqwidth()), max(560, self.window.winfo_reqheight())
        self.window.minsize(width, height)
        if not self.settings.get("geometry"):
            self.window.geometry(f"{max(860, width)}x{max(650, height)}")

    def show(self):
        self.refresh_runtime()
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
        elif report.mode == "batch" and report.completion != "success" and self.policy.armed:
            self.stop()
            self.note.set("批次未完整成功，规则已停用；不会自动重试。")
        self.refresh_runtime()

    def refresh_runtime(self):
        import json
        report = self.owner._latest_report
        self.runtime_context.set(self.owner._commit_text + "\n" + self.owner._memory_context)
        self.review_button.configure(state="normal" if report is not None and report.output_dir else "disabled")
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", "end")
        content = self.owner._report_details
        if report is not None:
            content += "\n\n" + json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
        self.report_text.insert("1.0", content)
        self.report_text.configure(state="disabled")

    def review(self):
        report = self.owner._latest_report
        if report is not None and report.output_dir:
            self.owner._submit(self.app.memory_result, report.output_dir)
