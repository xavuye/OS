"""GUI testai: Tk taimerio kvietiniai, modaliniai dialogai ir atminties lenteles."""

import sys
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vm_os"))
try:
    import tkinter as tk
    from gui import App
except ImportError:
    tk = None

from control_program import ControlProgram


@unittest.skipIf(tk is None, "Tkinter neidiegtas")
class GuiTests(unittest.TestCase):
    def test_modal_actions_cancel_pending_execution_before_opening_dialog(self):
        for action, args, dialog in (
                ("on_setreg", ("AX",), "gui.simpledialog.askstring"),
                ("on_setmem", (), "gui.simpledialog.askstring"),
                ("on_flash", (), "gui.simpledialog.askstring"),
                ("on_load", (), "gui.filedialog.askopenfilename")):
            with self.subTest(action=action):
                clock = tk.Tcl()
                system = ControlProgram()
                system.load_package_text(
                    "$JOB\nID 0001\nPAGES 02\nDATASEG 00\nDW 1\n"
                    "CODESEG 10\nJM10\nHALT\n$END")
                app = SimpleNamespace(
                    osys=system, run_iterator=system.run_all(),
                    run_button=SimpleNamespace(configure=lambda **kwargs: None),
                    after_cancel=clock.after_cancel)
                app.stop_run = MethodType(App.stop_run, app)
                app.run_callback = clock.after(1, lambda: next(app.run_iterator))
                before = system.view_status()

                def modal(*args, **kwargs):
                    self.assertIsNone(app.run_callback)
                    self.assertIsNone(app.run_iterator)
                    clock.after(5, lambda: clock.setvar("dialog_done", 1))
                    clock.eval("vwait dialog_done")
                    self.assertEqual(system.view_status(), before)
                    return None

                with patch(dialog, side_effect=modal):
                    getattr(App, action)(app, *args)

    def test_memory_tables_show_pages_and_mark_changes(self):
        try:
            app = App()
        except tk.TclError as exc:
            self.skipTest(f"Nera ekrano: {exc}")
        try:
            app.withdraw()
            program = Path(__file__).resolve().parents[1] / "vm_os" / "programs" / "prog1_israisaka.txt"
            with patch("gui.filedialog.askopenfilename", return_value=str(program)):
                app.on_load()
            vm = app.osys.active()
            rows = [app.vmtable.item(i, "values") for i in app.vmtable.get_children()]
            self.assertEqual([r[1] for r in rows], [str(b) for b in app.osys.page_table(vm)])
            self.assertEqual(rows[0][2:6], ("20", "30", "60", "-1000"))
            self.assertEqual(len(app.rmtable.get_children()), 64)
            # Ikelimas ka tik irase PT ir puslapius -> pazymeti kaip pakite.
            self.assertIn("changed", app.rmtable.item(str(vm.pt_block), "tags"))

            for _ in range(4):                       # LA00 LB01 MUL SA05
                app.on_step()
            self.assertIn("pt", app.rmtable.item(str(vm.pt_block), "tags"))
            data_row = app.vmtable.get_children()[0]
            self.assertIn("changed", app.vmtable.item(data_row, "tags"))
            self.assertEqual(app.vmtable.item(data_row, "values")[2 + 5], "600*")
            data_block = str(app.osys.page_table(vm)[0])
            self.assertIn("changed", app.rmtable.item(data_block, "tags"))

            app.on_step()                            # LA02: atmintis nepakito
            self.assertNotIn("changed", app.vmtable.item(app.vmtable.get_children()[0], "tags"))
            self.assertIn("current", app.vmtable.item(app.vmtable.get_children()[1], "tags"))

            app.blkvar.set(str(vm.pt_block))
            app.on_rm()
            self.assertEqual(app.rmtable.selection(), (str(vm.pt_block),))
        finally:
            app.destroy()
