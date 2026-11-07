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
        self.geometry("1100x700")
        self.osys = ControlProgram(seed=42)
        self.build()

    def build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=6, pady=4)
        ttk.Button(top, text="Load paketas", command=self.on_load).pack(side="left")
        ttk.Button(top, text="Step", command=self.on_step).pack(side="left", padx=4)
        ttk.Button(top, text="Run", command=self.on_run).pack(side="left")
        ttk.Button(top, text="Flash+", command=self.on_flash).pack(side="left", padx=4)
        self.vmvar = tk.StringVar()
        self.vmcombo = ttk.Combobox(top, textvariable=self.vmvar, width=10)
        self.vmcombo.pack(side="left", padx=4)
        ttk.Button(top, text="Use", command=self.on_use).pack(side="left")
        self.jobvar = tk.StringVar()
        ttk.Entry(top, textvariable=self.jobvar, width=8).pack(side="left", padx=4)
        ttk.Button(top, text="Create", command=self.on_create).pack(side="left")
        ttk.Button(top, text="Remove", command=self.on_remove).pack(side="left", padx=4)
        mid = ttk.Panedwindow(self, orient="horizontal")
        mid.pack(fill="both", expand=True, padx=6)
        left = ttk.Frame(mid)
        mid.add(left, weight=1)
        self.regs = tk.Text(left, height=12, width=45)
        self.regs.pack(fill="x")
        ttk.Label(left, text="VM atmintis").pack()
        self.vmmem = tk.Text(left, height=16, width=45)
        self.vmmem.pack(fill="both", expand=True)
        center = ttk.Frame(mid)
        mid.add(center, weight=1)
        ttk.Label(center, text="Vykdymo zurnalas").pack()
        self.journal = tk.Text(center, height=28, width=50)
        self.journal.pack(fill="both", expand=True)
        right = ttk.Frame(mid)
        mid.add(right, weight=1)
        ttk.Label(right, text="RM blokas / irenginiai").pack()
        row = ttk.Frame(right)
        row.pack(fill="x")
        self.blkvar = tk.StringVar(value="0")
        ttk.Entry(row, textvariable=self.blkvar, width=6).pack(side="left")
        ttk.Button(row, text="Rodyti RB", command=self.on_rm).pack(side="left", padx=4)
        self.rmmem = tk.Text(right, height=16, width=45)
        self.rmmem.pack(fill="both", expand=True)
        bot = ttk.Frame(self)
        bot.pack(fill="x", padx=6, pady=4)
        ttk.Button(bot, text="Set AX", command=lambda: self.on_setreg("AX")).pack(side="left")
        ttk.Button(bot, text="Set BX", command=lambda: self.on_setreg("BX")).pack(side="left", padx=4)
        ttk.Button(bot, text="Set MEM", command=self.on_setmem).pack(side="left")
        ttk.Button(bot, text="Printer", command=self.on_printer).pack(side="left", padx=4)
        self.refresh()

    def log(self, s):
        self.journal.insert("end", s + "\n")
        self.journal.see("end")

    def refresh(self):
        rm = self.osys.rm
        vm = self.osys.active()
        st = rm.status()
        t = f"VM={vm.id if vm else '-'} state={vm.state if vm else '-'}\n"
        t += f"AX={st['AX']} BX={st['BX']} PC={st['PC']} \nZF={st['ZF']} SF={st['SF']} OF={st['OF']}\n"
        t += f"PTR={st['PTR']}\nMODE={st['MODE']} PI={st['PI']} SI={st['SI']} TI={st['TI']}\n"
        t += f"Kanalas {st['ST']}[{st['SB']}]->{st['DT']}[{st['DB']}] \n"
        t += (f"Flash={st['flash_blocks']} Printer={st['printer_jobs']} "
              f"LaisviRB={st['free_blocks']} LaisviDisko={st['disk_free']}/{st['disk_blocks']}\n")
        if vm:
            if vm.state == "READY":
                t += f"Sekanti: {vm.peek_next(rm)}\n"
            else:
                t += "Sekanti: -\n"
            if vm.pt_block is not None:
                mp = ", ".join(f"{p:X}->{vm.page_table[p]}" for p in vm.allocated_pages())
                t += f"PT{vm.pt_block}: {mp}\n"
            elif getattr(vm, "final_page_table", None):
                mp = ", ".join(f"{p:X}->{vm.final_page_table[p]}" for p in sorted(vm.final_pages))
                t += f"PT atlaisvinta; paskutinis: {mp}\n"
        self.regs.delete("1.0", "end")
        self.regs.insert("end", t)
        if vm:
            self.vmmem.delete("1.0", "end")
            self.vmmem.insert("end", self.osys.vm_memory_text())
        self.vmcombo["values"] = list(self.osys.vms.keys())
        if vm:
            self.vmvar.set(vm.id)
        if vm:
            self.jobvar.set(vm.id)

    def on_load(self):
        path = filedialog.askopenfilename(filetypes=[("Tekstas", "*.txt"), ("Visi", "*.*")])
        if not path:
            return
        jobs, errs = self.osys.load_package_file(path)
        for e in errs:
            self.log("KLAIDA: " + e)
        for job in jobs:
            self.log(self.osys.create_vm(job.job_id))
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

    def on_step(self):
        r = self.osys.step_once()
        if r.get("done"):
            self.log(r.get("msg"))
        else:
            self.log(f"[{r['vm']}] {r['instr']} ev={r['event']} "
                     f"AX={r['after']['AX']} BX={r['after']['BX']} PC={r['after']['PC']:02X} next={r['next']}")
            if r.get("page") is not None and r.get("real") is not None:
                self.log(f"  VP{r['page']:X}->RB{r['real']}: {r['page_vals']}")
            if r.get("halted"):
                self.log(f"VM {r['vm']} baigta: {self.osys.vms[r['vm']].error or 'HALT'}")
        self.refresh()

    def on_run(self):
        for r in self.osys.run_all(max_steps=2000):
            if r.get("done"):
                self.log(r.get("msg"))
                break
            if r["event"] in ("PD", "GD", "FORK", "FORK_FAIL", "HALT", "PI", "TIMER") or r.get("halted"):
                self.log(f"[{r['vm']}] {r['instr']} ev={r['event']}")
                if r.get("page") is not None and r.get("real") is not None:
                    self.log(f"  VP{r['page']:X}->RB{r['real']}: {r['page_vals']}")
            if r.get("halted"):
                self.log(f"VM {r['vm']} baigta: {self.osys.vms[r['vm']].error or 'HALT'}")
        self.refresh()

    def on_use(self):
        self.log(self.osys.switch(self.vmvar.get()))
        self.refresh()

    def on_rm(self):
        try:
            b = int(self.blkvar.get())
        except ValueError:
            return
        self.rmmem.delete("1.0", "end")
        self.rmmem.insert("end", self.osys.rm_block_text(b))

    def on_flash(self):
        s = simpledialog.askstring("Flash", "16 reiksmiu kableliais:")
        if not s:
            return
        vals = []
        for t in s.split(","):
            t = t.strip()
            v = parse_num(t)
            vals.append(v if v is not None else t[:4])
        while len(vals) < 16:
            vals.append(0)
        self.osys.rm.flash.append(vals[:16])
        self.log(f"Flash blokas pridetas: {vals[:16]}")
        self.refresh()

    def on_setreg(self, name):
        v = simpledialog.askstring(name, f"Nauja {name} reiksme:")
        if v is None:
            return
        iv = parse_num(v)
        if iv is None:
            messagebox.showerror("Klaida", "Turi buti skaicius")
            return
        if name == "AX":
            self.osys.rm.ax = iv
        else:
            self.osys.rm.bx = iv
        self.log(f"{name}={iv} (pakeista vykdymo metu)")
        self.refresh()

    def on_setmem(self):
        a = simpledialog.askstring("MEM", "Adresas hex 00-FF:")
        v = simpledialog.askstring("MEM", "Reiksme (int arba tekstas):")
        if a is None or v is None:
            return
        vm = self.osys.active()
        try:
            addr = int(a, 16)
            iv = parse_num(v)
            if iv is None:
                iv = v[:4]
            ok = vm.write_virtual(self.osys.rm, addr, iv) if vm else False
            self.log(f"VM[{addr:02X}]={iv} {'OK' if ok else 'KLAIDA'}")
        except ValueError:
            messagebox.showerror("Klaida", "Blogas adresas")
        self.refresh()

    def on_printer(self):
        self.log("--- Spausdintuvas ---")
        for j in self.osys.rm.printer:
            self.log(f"VM{j['vm']} p{j['page']}: {' '.join(str(x) for x in j['data'])}")


if __name__ == "__main__":
    App().mainloop()
