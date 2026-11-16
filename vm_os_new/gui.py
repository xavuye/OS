import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_program import ControlProgram


def parse_num(s):
    try:
        return int(s, 0)
    except ValueError:
        pass
    try:
        return int(s, 16)
    except ValueError:
        return None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RM + VM emuliatorius (paketine OS)")
        # Langas pritaikomas ekranui (nesislepia po uzduociu juosta nesitikrinant DPI mastelio).
        width = min(1250, self.winfo_screenwidth() - 40)
        height = min(800, self.winfo_screenheight() - 110)
        self.geometry(f"{width}x{height}+10+10")
        self.minsize(900, 560)
        self.osys = ControlProgram(seed=42)
        self.run_iterator = None
        self.run_callback = None
        self.last_real = None          # RM blokas, kuriame buvo paskutine ivykdyta komanda
        self.prev_vm = {}              # (VM, VP) -> zodziai: pokyciams pazymeti
        self.prev_rm = {}              # RB -> zodziai
        self.build()

    def make_memory_table(self, parent, first_columns):
        """Lentele: pirmi stulpeliai (VP/RB...) ir 16 zodziu stulpeliu 0..F."""
        frame = ttk.Frame(parent)
        columns = [name for name, _ in first_columns] + [f"{i:X}" for i in range(16)]
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        for name, width in first_columns:
            tree.heading(name, text=name)
            tree.column(name, width=width, minwidth=width, anchor="center", stretch=False)
        for i in range(16):
            tree.heading(f"{i:X}", text=f"{i:X}")
            tree.column(f"{i:X}", width=40, minwidth=30, anchor="e", stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        tree.tag_configure("changed", background="#fff3a8")
        tree.tag_configure("current", background="#cfe8ff")
        tree.tag_configure("active", background="#dff5df")
        tree.tag_configure("pt", background="#e6e6e6")
        return frame, tree

    @staticmethod
    def mark_changes(words, old):
        """Pakitusius zodzius pazymi zvaigzdute; grazina (reiksmes, ar eilute pasikeite)."""
        if old is None:
            return [str(w) for w in words], False
        cells = [f"{w}*" if old[i] != w else str(w) for i, w in enumerate(words)]
        return cells, old != words

    def build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=6, pady=4)
        ttk.Button(top, text="Load paketas", command=self.on_load).pack(side="left")
        ttk.Button(top, text="Step", command=self.on_step).pack(side="left", padx=4)
        self.run_button = ttk.Button(top, text="Run", command=self.on_run)
        self.run_button.pack(side="left")
        ttk.Button(top, text="Flash+", command=self.on_flash).pack(side="left", padx=4)
        self.vmvar = tk.StringVar()
        self.vmcombo = ttk.Combobox(top, textvariable=self.vmvar, width=10)
        self.vmcombo.pack(side="left", padx=4)
        ttk.Button(top, text="Use", command=self.on_use).pack(side="left")
        self.jobvar = tk.StringVar()
        ttk.Entry(top, textvariable=self.jobvar, width=8).pack(side="left", padx=4)
        ttk.Button(top, text="Create", command=self.on_create).pack(side="left")
        ttk.Button(top, text="Remove", command=self.on_remove).pack(side="left", padx=4)
        ttk.Button(top, text="Kill", command=self.on_kill).pack(side="left")
        # Apatine juosta ir legenda dedamos pirmos (side=bottom), kad mazame lange neissikirptu.
        bot = ttk.Frame(self)
        bot.pack(side="bottom", fill="x", padx=6, pady=4)
        ttk.Label(self, text="Spalvos: geltona - pasikeite po paskutinio veiksmo (* - pakites zodis), "
                             "melyna - vykdytos komandos puslapis, zalia - aktyvios VM blokai, "
                             "pilka - puslapiu lentele").pack(side="bottom", anchor="w", padx=6)

        mid = ttk.Panedwindow(self, orient="horizontal")
        mid.pack(fill="x", padx=6)
        left = ttk.Frame(mid)
        mid.add(left, weight=1)
        ttk.Label(left, text="Registrai ir irenginiai").pack()
        self.regs = tk.Text(left, height=9, width=55)
        self.regs.pack(fill="both", expand=True)
        center = ttk.Frame(mid)
        mid.add(center, weight=2)
        ttk.Label(center, text="Vykdymo zurnalas").pack()
        self.journal = tk.Text(center, height=9, width=80, wrap="none")
        self.journal.pack(fill="both", expand=True)

        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=6, pady=4)
        vm_tab = ttk.Frame(tabs)
        tabs.add(vm_tab, text="VM atmintis")
        self.vmtitle = ttk.Label(vm_tab, text="")
        self.vmtitle.pack(anchor="w")
        frame, self.vmtable = self.make_memory_table(vm_tab, [("VP", 45), ("RB", 45)])
        frame.pack(fill="both", expand=True)

        rm_tab = ttk.Frame(tabs)
        tabs.add(rm_tab, text="RM atmintis")
        row = ttk.Frame(rm_tab)
        row.pack(fill="x")
        ttk.Label(row, text="RB:").pack(side="left")
        self.blkvar = tk.StringVar(value="0")
        ttk.Entry(row, textvariable=self.blkvar, width=6).pack(side="left")
        ttk.Button(row, text="Rodyti RB", command=self.on_rm).pack(side="left", padx=4)
        self.rminfo = ttk.Label(row, text="")
        self.rminfo.pack(side="left", padx=8)
        frame, self.rmtable = self.make_memory_table(rm_tab, [("RB", 45), ("Savininkas", 80)])
        frame.pack(fill="both", expand=True)
        self.tabs = tabs
        ttk.Button(bot, text="Set AX", command=lambda: self.on_setreg("AX")).pack(side="left")
        ttk.Button(bot, text="Set BX", command=lambda: self.on_setreg("BX")).pack(side="left", padx=4)
        ttk.Button(bot, text="Set MEM", command=self.on_setmem).pack(side="left")
        ttk.Button(bot, text="Printer", command=self.on_printer).pack(side="left", padx=4)
        self.refresh()

    def log(self, s):
        self.journal.insert("end", s + "\n")
        self.journal.see("end")

    def refresh(self):
        st = self.osys.view_status()
        vm_id = st["vm"]
        t = f"VM={vm_id or '-'} state={st['state'] or '-'}\n"
        t += f"AX={st['AX']} BX={st['BX']} PC={st['PC']:02X}\nZF={st['ZF']} SF={st['SF']} OF={st['OF']}\n"
        t += f"PTR={st['PTR']}\nMODE={st['MODE']} PI={st['PI']} SI={st['SI']} TI={st['TI']}\n"
        t += f"Kanalas {st['ST']}[{st['SB']}]->{st['DT']}[{st['DB']}]\n"
        t += (f"Flash={st['flash_blocks']} Printer={st['printer_jobs']} "
              f"LaisviRB={st['free_blocks']} LaisviDisko={st['disk_free']}/{st['disk_blocks']}\n")
        if vm_id:
            t += f"Sekanti: {st['next']}\n{self.osys.page_table_text()}\n"
        self.regs.delete("1.0", "end")
        self.regs.insert("end", t)
        self.refresh_vm_table(vm_id)
        self.refresh_rm_table(vm_id)
        self.vmcombo["values"] = list(self.osys.job_status()["vms"])
        self.vmvar.set(vm_id or "")

    def refresh_vm_table(self, vm_id):
        title, rows = self.osys.vm_memory_rows(vm_id)
        self.vmtitle.configure(text=title)
        self.vmtable.delete(*self.vmtable.get_children())
        for page, real, words in rows:
            cells, changed = self.mark_changes(words, self.prev_vm.get((vm_id, page)))
            tags = ("changed",) if changed else ("current",) if real is not None and real == self.last_real else ()
            self.vmtable.insert("", "end", values=[f"{page:X}", real if real is not None else "-"] + cells,
                                tags=tags)
            self.prev_vm[(vm_id, page)] = list(words)

    def refresh_rm_table(self, vm_id):
        self.rmtable.delete(*self.rmtable.get_children())
        for block, owner, words in self.osys.rm_memory_rows():
            cells, changed = self.mark_changes(words, self.prev_rm.get(block))
            if changed:
                tags = ("changed",)
            elif block == self.last_real:
                tags = ("current",)
            elif owner.startswith("PT"):
                tags = ("pt",)
            elif vm_id and owner == f"VM {vm_id}":
                tags = ("active",)
            else:
                tags = ()
            self.rmtable.insert("", "end", iid=str(block), values=[block, owner] + cells, tags=tags)
            self.prev_rm[block] = list(words)

    def on_load(self):
        self.stop_run()
        path = filedialog.askopenfilename(filetypes=[("Tekstas", "*.txt"), ("Visi", "*.*")])
        if not path:
            return
        try:
            jobs, errs = self.osys.load_package_file(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Klaida", str(exc))
            return
        for e in errs:
            self.log("KLAIDA: " + e)
        for job in jobs:
            state = "VM sukurta" if job.job_id in self.osys.job_status()["vms"] else "laukia RM atminties"
            self.log(f"Uzduotis {job.job_id} diske: {state}")
        self.refresh()

    def on_create(self):
        jid = self.jobvar.get().strip()
        if not jid:
            return
        self.log(self.osys.create_vm(jid))
        self.refresh()

    def on_remove(self):
        jid = self.jobvar.get().strip()
        if not jid:
            return
        self.log(self.osys.remove_vm(jid))
        self.refresh()

    def on_kill(self):
        jid = self.jobvar.get().strip() or self.vmvar.get().strip()
        if not jid:
            return
        self.log(self.osys.kill_vm(jid))
        self.refresh()

    def show_step(self, result):
        if result["done"]:
            self.log(result["msg"])
            return
        self.last_real = result["real"]
        after = result["after"]
        self.log(f"[{result['vm']}] {result['instr']} ev={result['event']} "
                 f"AX={after['AX']} BX={after['BX']} PC={after['PC']:02X} "
                 f"ZF={after['ZF']} SF={after['SF']} OF={after['OF']} next={result['next']}")
        intr = result["interrupts"]
        if intr["PI"] or intr["SI"] or intr["TI"] <= 0:
            self.log(f"  Pertraukimas PI={intr['PI']} SI={intr['SI']} TI={intr['TI']}")
        if result["real"] is not None:
            words = " ".join(str(w) for w in result["page_vals"])
            self.log(f"  VP{result['page']:X}->RB{result['real']}: {words}")
        if result["halted"]:
            self.log(f"VM {result['vm']} baigta: {result['error'] or 'HALT'}")

    def on_step(self):
        self.stop_run()
        self.show_step(self.osys.step_once())
        self.refresh()

    def on_run(self):
        if self.run_iterator is not None:
            self.stop_run()
            self.refresh()
            return
        self.run_iterator = self.osys.run_all()
        self.run_button.configure(text="Pause")
        self.run_callback = self.after(1, self.run_tick)

    def stop_run(self):
        if self.run_callback is not None:
            self.after_cancel(self.run_callback)
            self.run_callback = None
        if self.run_iterator is not None:
            self.run_iterator.close()
            self.run_iterator = None
            self.run_button.configure(text="Run")

    def run_tick(self):
        self.run_callback = None
        result = next(self.run_iterator)
        if result["done"] or result["event"] != "OK":
            self.show_step(result)
            self.refresh()
        if result["done"]:
            self.stop_run()
        else:
            self.run_callback = self.after(1, self.run_tick)

    def on_use(self):
        self.log(self.osys.switch(self.vmvar.get()))
        self.refresh()

    def on_rm(self):
        try:
            b = int(self.blkvar.get())
        except ValueError:
            return
        self.tabs.select(1)
        if self.rmtable.exists(str(b)):
            self.rmtable.selection_set(str(b))
            self.rmtable.see(str(b))
            self.rminfo.configure(text=f"RB{b}: {self.osys.block_owner(b)}")
        else:
            self.rminfo.configure(text=self.osys.rm_block_text(b))

    def on_flash(self):
        self.stop_run()
        s = simpledialog.askstring("Flash", "16 reiksmiu kableliais:")
        if not s:
            return
        vals = []
        for t in s.split(","):
            t = t.strip()
            v = parse_num(t)
            vals.append(v if v is not None else t)
        try:
            number = self.osys.add_flash_block(vals)
        except ValueError as exc:
            messagebox.showerror("Klaida", str(exc))
            return
        self.log(f"Pridetas flash blokas #{number}")
        self.refresh()

    def on_setreg(self, name):
        self.stop_run()
        v = simpledialog.askstring(name, f"Nauja {name} reiksme:")
        if v is None:
            return
        iv = parse_num(v)
        if iv is None:
            messagebox.showerror("Klaida", "Turi buti skaicius")
            return
        try:
            self.osys.debug_set_register(name, iv)
        except ValueError as exc:
            messagebox.showerror("Klaida", str(exc))
            return
        self.log(f"{name}={iv} (pakeista vykdymo metu)")
        self.refresh()

    def on_setmem(self):
        self.stop_run()
        a = simpledialog.askstring("MEM", "Adresas hex 00-FF:")
        v = simpledialog.askstring("MEM", "Reiksme (int arba tekstas):")
        if a is None or v is None:
            return
        try:
            addr = int(a, 16)
            iv = parse_num(v)
            if iv is None:
                iv = v
            self.osys.debug_set_memory(addr, iv)
            self.log(f"VM[{addr:02X}]={iv} OK")
        except ValueError as exc:
            messagebox.showerror("Klaida", str(exc))
        self.refresh()

    def on_printer(self):
        self.log("--- Spausdintuvas ---")
        for j in self.osys.printer_output():
            self.log(f"VM{j['vm']} p{j['page']}: {' '.join(str(x) for x in j['data'])}")


if __name__ == "__main__":
    App().mainloop()
