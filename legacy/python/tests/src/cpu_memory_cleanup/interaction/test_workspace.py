"""桌面详情构造和关闭不产生读取；使用可记录的控件替身。"""
from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest.mock import Mock, patch
from cpu_memory_cleanup.api import create_application, Capabilities
from cpu_memory_cleanup.interaction.workspace import Workspace
from tests.fixtures import ScenarioBackend, FakeRuntime
from .test_desktop import Widget, Variable

class Var(Variable):
    def __init__(self,value=""): super().__init__(value)
    def trace_add(self,*args): pass

class Control(Widget):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.pages=[];self.rows={};self.chosen=[];self.hidden=False
    def add(self,page,**options): self.pages.append((page,options))
    def configure(self,*args,**kwargs): super().configure(**kwargs)
    def heading(self,*args,**kwargs): pass
    def column(self,*args,**kwargs): pass
    def get_children(self): return tuple(self.rows)
    def selection_set(self,keys): self.chosen=list(keys)
    def selection(self): return tuple(self.chosen)
    def delete(self,*keys):
        for key in keys:self.rows.pop(key,None)
        self.options["text"]=""
    def insert(self,*args,**kwargs):
        if "iid" in kwargs:self.rows[kwargs["iid"]]=kwargs
        elif len(args)>1:self.options["text"]=args[1]
    def title(self,value): self.options["title"]=value
    def geometry(self,value=None):
        if value:self.options["geometry"]=value
        return self.options.get("geometry","860x650")
    def minsize(self,*args): pass
    def update_idletasks(self): pass
    def winfo_reqwidth(self): return 730
    def winfo_reqheight(self): return 560
    def protocol(self,*args): pass
    def deiconify(self): self.hidden=False
    def withdraw(self): self.hidden=True
    def lift(self): pass

class WorkspaceTests(unittest.TestCase):
    def test_three_sections_open_and_hide_without_reader_or_rule_activation(self):
        backend=ScenarioBackend()
        with tempfile.TemporaryDirectory() as directory:
            app=create_application(reader=backend,maintainer=backend,runtime=FakeRuntime(),capabilities=Capabilities("synthetic","available",("working-set",)))
            owner=types.SimpleNamespace(app=app,root=Control(),cancel=Mock(),close=Mock(),_latest_report=None,
                _report_details="尚无运行记录",_commit_text="提交量 未知",_memory_context="最近更新：未知")
            tk=types.ModuleType("tkinter");ttk=types.SimpleNamespace(**{n:Control for n in ("Frame","Label","Button","Entry","Checkbutton","Treeview","Scrollbar","Notebook","Combobox","Style")})
            tk.ttk=ttk;tk.messagebox=Mock();tk.Toplevel=tk.Text=tk.Listbox=Control;tk.StringVar=tk.BooleanVar=Var
            with patch.dict(sys.modules,{"tkinter":tk}),patch.object(backend,"process_targets") as read:
                workspace=Workspace(owner,settings_path=Path(directory)/"preferences.json")
                self.assertEqual([options["text"] for _,options in workspace.notebook.pages],["进程范围","整理设置","运行详情"])
                self.assertEqual(workspace.rows,[])
                self.assertFalse(workspace.policy.armed)
                workspace.hide();self.assertTrue(workspace.window.hidden)
                workspace.show();self.assertFalse(workspace.window.hidden)
                read.assert_not_called();owner.close.assert_not_called()
                self.assertEqual(backend.actions,[])
                self.assertFalse((Path(directory)/"preferences.json").exists())
            app.shutdown()
