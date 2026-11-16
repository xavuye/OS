import functools
from contextlib import contextmanager

from real_machine import RealMachine, PAGE_SIZE, VM_PAGES, VM_WORDS, WORD_MIN, WORD_MAX, is_word, ptr_encode
from virtual_machine import VirtualMachine, classify
from loader import parse_package


PI_TEXT = {0: "-", 1: "neteisingas virtualus adresas", 2: "neteisingas operacijos kodas",
           3: "neleistinas priskyrimas / apsaugos pazeidimas", 4: "aritmetinis perpildymas", 5: "dalyba is nulio"}
SI_EVENTS = {1: "GD", 2: "PD", 3: "FORK", 4: "HALT"}


def os_entry(method):
    """Naudotojo sasajos kreipinys i OS: vykdomas SUPERVISOR rezimu."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._supervisor():
            return method(self, *args, **kwargs)
    return wrapper


class ControlProgram:
    def __init__(self, seed=None, quantum=30):
        self.rm = RealMachine(seed=seed)
        self._os_depth = 0
        self.vms = {}
        self.active_id = None      # VM, kuria rodo/valdo naudotojo sasaja
        self.cpu_vm = None         # VM, kurios kontekstas dabar ikeltas i procesoriu
        self.order = []
        self.disk_jobs = {}
        self.next_fork_num = 9000
        self.log = []
        self.set_timer(quantum)

    @contextmanager
    def _supervisor(self):
        """Vienintelis iejimas i OS. Iejus MODE=SUPERVISOR; iseinant is isorinio
        iejimo procesorius grizta i USER tik tada, kai jame ikeltas VM kontekstas."""
        self._os_depth += 1
        self.rm.mode = "SUPERVISOR"
        try:
            yield self.rm
        finally:
            self._os_depth -= 1
            if self._os_depth == 0:
                self.rm.mode = "USER" if self.cpu_vm is not None else "SUPERVISOR"

    def msg(self, s):
        self.log.append(s)
        if len(self.log) > 500:
            self.log = self.log[-500:]

    def store_job_on_disk(self, job):
        images = [[0] * PAGE_SIZE for _ in range(job.pages)]
        for k, w in enumerate(job.data_words):
            va = job.data_addr + k
            p = va // PAGE_SIZE
            d = va % PAGE_SIZE
            if 0 <= p < job.pages:
                images[p][d] = w
        for k, c in enumerate(job.code):
            va = job.code_addr + k
            p = va // PAGE_SIZE
            d = va % PAGE_SIZE
            if 0 <= p < job.pages:
                images[p][d] = c
        blocks = self.rm.alloc_disk_blocks(job.pages)
        if blocks is None:
            return None
        try:
            for image, block in zip(images, blocks):
                self.rm.supervisor_memory[0] = image
                self.rm.xchg("SUP", 0, "DISK", block)
        except Exception:
            self.rm.free_disk_blocks(blocks)
            raise
        finally:
            self.rm.supervisor_memory[0] = [0] * PAGE_SIZE
        return blocks

    @os_entry
    def load_package_text(self, text):
        jobs, errors = parse_package(text)
        for e in errors:
            self.msg("KLAIDA: " + e)
        accepted = []
        for job in jobs:
            if job.job_id in self.disk_jobs or job.job_id in self.vms:
                errors.append(f"Uzduotis {job.job_id}: ID jau egzistuoja (panaikinkite su remove)")
                continue
            blocks = self.store_job_on_disk(job)
            if blocks is None:
                errors.append(f"Uzduotis {job.job_id}: nepakanka disko bloku (reikia {job.pages})")
                continue
            job.disk_blocks = blocks
            self.disk_jobs[job.job_id] = job
            self.msg(f"Uzduotis {job.job_id} patikrinta ir irasyta i diska "
                     f"(PAGES={job.pages}, disk blokai {blocks[0]}-{blocks[-1]})")
            accepted.append(job)
        self.admit_waiting_jobs()
        for job in accepted:
            if job.job_id not in self.vms:
                self.msg(f"Uzduotis {job.job_id} laukia laisvos RM atminties diske")
        return accepted, errors

    def load_package_file(self, path):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        return self.load_package_text(text)

    # --- VM atminties perziura OS vardu: vertimas per tos VM PTR ---
    def page_table(self, vm):
        """Puslapiu lentele, perskaityta is RM atminties (blokas PTR.a2a3)."""
        if vm.pt_block is None:
            return list(vm.final_page_table or [])
        table = []
        for page in range(vm.pages_needed):
            t = self.rm.translate(page * PAGE_SIZE, vm.ptr)
            table.append(t[0] if t else None)
        return table

    def vm_translate(self, vm, vaddr):
        return None if vm.pt_block is None else self.rm.translate(vaddr, vm.ptr)

    def vm_read(self, vm_id, vaddr):
        vm = self.vms.get(vm_id)
        t = self.vm_translate(vm, vaddr) if vm else None
        if t is None:
            return None
        real, offset, _ = t
        return self.rm.memory[real][offset]

    def release_vm(self, vm):
        if vm.pt_block is None and not vm.owned_blocks:
            return
        vm.final_page_table = self.page_table(vm)
        vm.final_pages = {p: list(self.rm.memory[real])
                          for p, real in enumerate(vm.final_page_table) if real is not None}
        blocks = list(vm.owned_blocks)
        self.rm.free_block_list(blocks)
        vm.owned_blocks = []
        vm.pt_block = None
        if self.cpu_vm is vm:
            self.rm.ptr = ptr_encode(0, VM_PAGES, 0)
            self.cpu_vm = None
        self.msg(f"VM {vm.id} resursai atlaisvinti ({len(blocks)} bloku)")

    @os_entry
    def create_vm(self, job_id):
        if job_id not in self.disk_jobs:
            return f"Nera uzduoties diske: {job_id}"
        job = self.disk_jobs[job_id]
        old = self.vms.get(job_id)
        if old is not None and old.state == "READY":
            return f"VM {job_id} jau sukurta"
        if not job.disk_blocks:
            return f"Uzduotis {job_id} nera diske"
        need = job.pages + 1
        blocks = self.rm.alloc_random_blocks(need)
        if blocks is None:
            return (f"Nepakanka laisvos atminties VM {job.job_id}: reikia {need} bloku, "
                    f"laisva {len(self.rm.free_blocks)}")
        pt_block = blocks[0]
        page_blocks = blocks[1:]
        vm = VirtualMachine(job.job_id, job.pages)
        vm.pt_block = pt_block
        vm.owned_blocks = list(blocks)
        loaded = False
        try:
            self._write_page_table(pt_block, page_blocks)
            if len(job.disk_blocks) != job.pages:
                raise ValueError("Neteisingas uzduoties bloku skaicius")
            for disk_block, real in zip(job.disk_blocks, page_blocks):
                self.rm.xchg("DISK", disk_block, "USER", real, vm_id=vm.id)
            loaded = True
        except ValueError as exc:
            message = f"Nepavyko ikelti VM {job_id}: {exc}"
            self.msg(message)
            return message
        finally:
            if not loaded:
                self.rm.free_block_list(blocks)
        vm.data_start = job.data_addr
        vm.data_count = len(job.data_words)
        vm.code_start = job.code_addr
        for k, c in enumerate(job.code):
            vm.code_addrs.add(job.code_addr + k)
        vm.pc = job.code_addr
        vm.ax = 0
        vm.bx = 0
        vm.state = "READY"
        vm.error = ""
        self.vms[vm.id] = vm
        if vm.id in self.order:
            self.order.remove(vm.id)
        self.order.append(vm.id)
        self.switch(vm.id)
        mapping = ", ".join(f"VP{p}->RB{b}" for p, b in enumerate(page_blocks))
        self.msg(f"Sukurta VM {vm.id}: PT blokas {pt_block}, {mapping} (ne nuosekliai, atsitiktinai)")
        self.msg(f"Valdymas atiduotas veliausiai uzkrautajai VM {vm.id}")
        return f"VM {vm.id} sukurta. Valdymas -> {vm.id}"

    def _write_page_table(self, pt_block, page_blocks):
        self.rm.memory[pt_block] = list(page_blocks) + [-1] * (VM_PAGES - len(page_blocks))

    def admit_waiting_jobs(self):
        """Ikelia laukiancias uzduotis is disko atvykimo tvarka, jei ju puslapiai telpa RM."""
        started = []
        for job_id, job in self.disk_jobs.items():
            if job_id in self.vms or len(self.rm.free_blocks) < job.pages + 1:
                continue
            self.create_vm(job_id)
            if job_id in self.vms:
                started.append(job_id)
        return started

    @os_entry
    def remove_vm(self, vm_id):
        vm = self.vms.get(vm_id)
        job = self.disk_jobs.get(vm_id)
        if vm is None and job is None:
            return f"Nera VM/uzduoties {vm_id}"
        if vm is not None and vm.state == "READY":
            return f"VM {vm_id} dar vykdoma"
        if vm is not None:
            self.release_vm(vm)
            del self.vms[vm_id]
            if vm_id in self.order:
                self.order.remove(vm_id)
            if self.active_id == vm_id:
                self.active_id = None
                self.rm.ptr = ptr_encode(0, VM_PAGES, 0)
        if job is not None:
            if job.disk_blocks:
                self.rm.free_disk_blocks(job.disk_blocks)
            del self.disk_jobs[vm_id]
            self.msg(f"Uzduotis {vm_id} pasalinta is disko")
        return f"Pasalinta {vm_id}"

    @os_entry
    def kill_vm(self, vm_id):
        """Operatorius nutraukia vykdoma VM (pvz., amzina cikla): OS atlaisvina jos resursus."""
        vm = self.vms.get(vm_id)
        if vm is None:
            return f"Nera VM {vm_id}"
        if vm.state != "READY":
            return f"VM {vm_id} jau baigta"
        if vm is self.cpu_vm:
            vm.save_from_rm(self.rm)
        self.msg(f"VM {vm_id}: nutraukta operatoriaus (kill)")
        self._finish_vm(vm, "Nutraukta operatoriaus (kill)")
        return f"VM {vm_id} nutraukta"

    def active(self):
        if self.active_id is None:
            return None
        return self.vms.get(self.active_id)

    def view_status(self):
        vm = self.active()
        return {**self.rm.status(), "vm": self.active_id, "state": vm.state if vm else None,
                "next": self.peek_next(vm) if vm and vm.state == "READY" else "-"}

    def peek_next(self, vm):
        pc = self.rm.pc if vm is self.cpu_vm else vm.pc
        if pc not in vm.code_addrs:
            return f"<PI=1 PC={pc:02X} nera programos kode>"
        t = self.vm_translate(vm, pc)
        if t is None:
            return f"<PI=1 neteisingas adresas PC={pc:02X}>"
        real, offset, _ = t
        return f"{self.rm.memory[real][offset]}"

    def job_status(self):
        return {"disk": tuple(self.disk_jobs),
                "waiting": tuple(job_id for job_id in self.disk_jobs if job_id not in self.vms),
                "vms": {vm.id: dict(pages=vm.pages_needed, state=vm.state,
                                    AX=self.rm.ax if vm is self.cpu_vm else vm.ax,
                                    PC=self.rm.pc if vm is self.cpu_vm else vm.pc,
                                    error=vm.error) for vm in self.vms.values()}}

    def page_table_text(self):
        vm = self.active()
        if vm is None:
            return "Nera aktyvios VM"
        table = self.page_table(vm)
        if not table:
            return "VM neturi puslapiu lenteles"
        title = f"PT RB{vm.pt_block}" if vm.pt_block is not None else "Paskutine PT kopija"
        mapping = ", ".join(f"VP{page:X}->RB{block}" for page, block in enumerate(table) if block is not None)
        return f"{title}: {mapping}"

    def printer_output(self):
        return [{**entry, "data": entry["data"].copy()} for entry in self.rm.printer]

    def channel_entries(self):
        return tuple(self.rm.channel.log[-5:])

    def journal_entries(self):
        return tuple(self.log[-20:])

    @os_entry
    def set_timer(self, value):
        if type(value) is not int or value < 1:
            raise ValueError("Taimerio kvantas turi buti teigiamas")
        self.rm.timer_quantum = self.rm.ti = value

    @staticmethod
    def _validate_word(value):
        if not is_word(value):
            raise ValueError("Zodis turi buti 32 bitu sveikasis skaicius arba 1-4 simboliai")

    def add_flash_block(self, words):
        block = list(words)
        if len(block) > PAGE_SIZE:
            raise ValueError("Flash blokas turi ne daugiau kaip 16 zodziu")
        for value in block:
            self._validate_word(value)
        block.extend([0] * (PAGE_SIZE - len(block)))
        self.rm.flash.append(block)
        return len(self.rm.flash) - 1

    def _debug_target(self):
        vm = self.cpu_vm
        if vm is None or vm.state != "READY":
            raise ValueError("Nera parengtos aktyvios VM")
        return vm

    @os_entry
    def debug_set_register(self, name, value):
        vm = self._debug_target()
        if name not in ("AX", "BX", "PC"):
            raise ValueError("Galima keisti AX, BX arba PC")
        low, high = (0, VM_WORDS - 1) if name == "PC" else (WORD_MIN, WORD_MAX)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name} reiksme uz ribu")
        setattr(self.rm, name.lower(), value)
        vm.save_from_rm(self.rm)

    @os_entry
    def debug_set_memory(self, address, value):
        vm = self._debug_target()
        self._validate_word(value)
        t = self.vm_translate(vm, address)
        if t is None:
            raise ValueError("Neteisingas virtualus adresas")
        if address in vm.code_addrs:
            raise ValueError("Programos kodas apsaugotas")
        real, offset, _ = t
        self.rm.memory[real][offset] = value

    @os_entry
    def switch(self, vm_id):
        if vm_id not in self.vms:
            return f"Nera VM {vm_id}"
        if self.vms[vm_id].state != "READY":
            return f"VM {vm_id} jau baigta (galima perziureti jos atminties kopija)"
        vm = self.vms[vm_id]
        current = self.cpu_vm
        if current is not vm:
            if current is not None:
                current.save_from_rm(self.rm)
            vm.load_to_rm(self.rm)
            self.cpu_vm = vm
            self.rm.ti = self.rm.timer_quantum
        self.active_id = vm_id
        return f"Aktyvi VM {vm_id}"

    def _free_fork_id(self):
        """Vaiko ID is intervalo 9001-9999 (formatas ID nnnn), pradedant po paskutinio isduoto."""
        for k in range(999):
            num = 9001 + (self.next_fork_num - 9000 + k) % 999
            if f"{num:04d}" not in self.vms and f"{num:04d}" not in self.disk_jobs:
                return num
        return None

    def _fork(self, parent):
        """Grazina None, jei vaikas sukurtas, arba priezasti, kodel FORK nepavyko (AX=-1)."""
        sources = self.page_table(parent)
        if None in sources:
            raise ValueError("FORK: neteisinga tevo puslapiu lentele")
        child_num = self._free_fork_id()
        if child_num is None:
            return "nera laisvo VM ID"
        blocks = self.rm.alloc_random_blocks(parent.pages_needed + 1)
        if blocks is None:
            return "nepakanka atminties"
        child = VirtualMachine(f"{child_num:04d}", parent.pages_needed)
        child.pt_block = blocks[0]
        child.owned_blocks = list(blocks)
        try:
            for source, destination in zip(sources, blocks[1:]):
                self.rm.xchg("USER", source, "USER", destination, vm_id=child.id)
            self._write_page_table(blocks[0], blocks[1:])
        except Exception:
            self.rm.free_block_list(blocks)
            raise
        child.save_from_rm(self.rm)
        child.ax = 0
        child.code_start = parent.code_start
        child.code_addrs = parent.code_addrs.copy()
        child.data_start = parent.data_start
        child.data_count = parent.data_count
        self.vms[child.id] = child
        self.order.append(child.id)
        self.next_fork_num = child_num
        self.rm.ax = child_num
        self.msg(f"FORK: tevas {parent.id} AX={child_num}, vaikas {child.id} AX=0 PC={child.pc:02X}")
        return None

    def _service_io(self, vm, event):
        rm = self.rm
        # OS pati perskaito pertraukima sukelusia komanda: PC jau rodo i kita komanda.
        t = rm.translate(rm.pc - 1)
        op, page = classify(rm.memory[t[0]][t[1]]) if t else (None, None)
        if op != event:
            return 2, f"{event}: nepavyko perskaityti komandos"
        mapped = rm.translate(page * PAGE_SIZE)
        real = mapped[0] if mapped else None
        if real is None:
            return 1, f"{event}: neteisingas puslapis"
        if event == "GD":
            if any(addr // PAGE_SIZE == page for addr in vm.code_addrs):
                return 3, "GD: bandymas perrasyti programos koda"
            if not rm.flash:
                # Prasymas neivykdytas, bet tai ne programos klaida: AX=-1, puslapis nekeiciamas.
                rm.ax = -1
                self.msg(f"VM {vm.id} GD{page:X}0: flash srautas tuscias, AX=-1")
                return 0, "FAIL"
            rm.xchg("FLASH", 0, "USER", real, vm_id=vm.id)
            rm.flash.pop(0)
        else:
            rm.xchg("USER", real, "PRINTER", page, vm_id=vm.id)
        self.msg(f"VM {vm.id} {event}{page:X}0: RB{real} per XCHG")
        return 0, ""

    def _cpu_snapshot(self):
        rm = self.rm
        return dict(AX=rm.ax, BX=rm.bx, PC=rm.pc, ZF=int(rm.zf), SF=int(rm.sf), OF=int(rm.of),
                    TI=rm.ti, PI=rm.pi, SI=rm.si, MODE=rm.mode)

    def handle_interrupt(self, vm, instr):
        """Vienintelis pertraukimu apdorojimo taskas: tikrinami PI, SI ir TI po kiekvienos komandos."""
        rm = self.rm
        if self.cpu_vm is not vm or rm.mode != "USER":
            raise PermissionError("Pertraukimas turi priklausyti aktyviai VM USER rezime")
        interrupts = dict(PI=rm.pi, SI=rm.si, TI=rm.ti)
        timer_hit = rm.ti <= 0
        if not (rm.pi or rm.si or timer_hit):
            return "OK", self._cpu_snapshot(), interrupts

        with self._supervisor():
            # 1. Priezasties nustatymas ir aptarnavimas (SI -> OS paslauga).
            event = "TIMER"
            detail = ""
            if rm.pi:
                event = "PI"
            elif rm.si:
                event = SI_EVENTS.get(rm.si, "PI")
                try:
                    if event in ("GD", "PD"):
                        rm.pi, detail = self._service_io(vm, event)
                        if detail == "FAIL":
                            event, detail = event + "_FAIL", ""
                    elif event == "FORK":
                        reason = self._fork(vm)
                        if reason is not None:
                            rm.ax = -1
                            event = "FORK_FAIL"
                            self.msg(f"VM {vm.id} FORK: {reason}, AX=-1")
                    elif event == "PI":
                        rm.pi, detail = 2, f"Neteisingas SI={rm.si}"
                except ValueError as exc:
                    rm.pi, detail = 3, str(exc)

            # 2. Sprendimas: VM baigiama (PI arba HALT) ar tesia darba.
            interrupts["PI"] = rm.pi
            finished = bool(rm.pi) or event == "HALT"
            if rm.pi:
                vm.error = f"PI={rm.pi} {detail or PI_TEXT.get(rm.pi, '?')}"
                self.msg(f"VM {vm.id} {instr}: {vm.error}")
            elif event == "HALT":
                self.msg(f"VM {vm.id} HALT: AX={rm.ax} BX={rm.bx}")

            # 3. Pertraukimu registru isvalymas ir taimerio atstatymas.
            rm.pi = rm.si = 0
            if timer_hit:
                self.msg(f"VM {vm.id}: taimerio pertraukimas TI={rm.ti}")
                rm.ti = rm.timer_quantum
            vm.save_from_rm(rm)
            after = self._cpu_snapshot()

            # 4. Planavimas: baigta VM atlaisvinama, kvantas baigesi -> kita VM.
            if finished:
                self._finish_vm(vm, vm.error)
            elif timer_hit:
                self._give_cpu_away(vm)
        after["MODE"] = rm.mode
        return event, after, interrupts

    def _finish_vm(self, vm, error=""):
        """Bendras VM pabaigos kelias (HALT, PI, kill): busena, resursai, laukiancios uzduotys, CPU."""
        vm.state = "HALTED"
        vm.error = error
        self.release_vm(vm)
        self.admit_waiting_jobs()
        self._give_cpu_away(vm)

    def _give_cpu_away(self, vm):
        if self.active_id == vm.id:
            following = self.next_ready(vm.id)
            if following is not None:
                self.switch(following)
                self.msg(f"Procesorius perduotas VM {following}")

    def prepare_run(self, vm_id=None):
        if vm_id is not None:
            target = self.vms.get(vm_id)
            if target is None or target.state != "READY":
                return None, f"VM {vm_id} neparengta vykdyti"
            self.switch(vm_id)
        vm = self.active()
        if vm is None or vm.state != "READY":
            next_id = self.next_ready(self.active_id)
            if next_id is None:
                return None, "Nera parengtu VM"
            self.switch(next_id)
            vm = self.active()
        if self.cpu_vm is not vm:
            self.switch(vm.id)
        return vm, ""

    def next_ready(self, exclude=None):
        candidates = self.order
        if exclude in self.order:
            i = self.order.index(exclude) + 1
            candidates = self.order[i:] + self.order[:i]
        for vid in candidates:
            if vid != exclude and vid in self.vms and self.vms[vid].state == "READY":
                return vid
        return None

    def step_once(self, vm_id=None):
        vm, err = self.prepare_run(vm_id)
        if vm is None:
            return {"done": True, "msg": err}
        rm = self.rm
        before = self._cpu_snapshot()
        info = vm.step(rm)
        page = info.get("page")
        real = info.get("real")
        page_vals = rm.dump_block(real) if real is not None else None
        event, after, interrupts = self.handle_interrupt(vm, info["instr"])
        done = vm.state != "READY"
        nxt = "-" if done else self.peek_next(vm)
        return {"done": False, "vm": vm.id, "instr": info["instr"], "before": before, "after": after,
                "event": event, "halted": done, "next": nxt, "page": page, "real": real,
                "page_vals": page_vals, "error": vm.error, "interrupts": interrupts}

    def run_all(self):
        while True:
            result = self.step_once()
            yield result
            if result["done"]:
                return

    # --- Atminties perziura: OS pateikia eilutes, sasaja tik jas atvaizduoja ---
    def vm_memory_rows(self, vm_id=None):
        """VM atmintis kaip lentele: (antraste, [(VP, RB arba None, 16 zodziu), ...])."""
        vm = self.vms.get(vm_id or self.active_id)
        if vm is None:
            return "Nera VM", []
        if vm.pt_block is not None:
            table = self.page_table(vm)
            rows = [(p, real, list(self.rm.memory[real]) if real is not None else ["-"] * PAGE_SIZE)
                    for p, real in enumerate(table)]
            return f"VM {vm.id} atmintis (VP -> RB)", rows
        if vm.final_pages:
            table = vm.final_page_table or []
            rows = [(p, table[p] if p < len(table) else None, list(words))
                    for p, words in sorted(vm.final_pages.items())]
            return f"VM {vm.id}: resursai atlaisvinti, paskutine kopija", rows
        return f"VM {vm.id}: atmintis atlaisvinta", []

    def block_owner(self, block):
        for vid, vm in self.vms.items():
            if vm.pt_block == block:
                return f"PT {vid}"
            if block in vm.owned_blocks:
                return f"VM {vid}"
        return "-"

    def rm_memory_rows(self):
        """Visa RM naudotojo atmintis: [(RB, savininkas, 16 zodziu), ...]."""
        return [(b, self.block_owner(b), list(self.rm.memory[b])) for b in range(self.rm.user_blocks)]

    def vm_memory_text(self, vm_id=None, pages=None):
        title, rows = self.vm_memory_rows(vm_id)
        lines = [title]
        for p, real, words in rows:
            if pages is None or p in pages:
                real_text = f"RB{real}" if real is not None else "RB-"
                lines.append(f"VP{p:X}->{real_text}: " + " | ".join(str(x) for x in words))
        return "\n".join(lines)

    def rm_block_text(self, b):
        if not (0 <= b < self.rm.user_blocks):
            return f"Blokas {b} uz ribu 0-{self.rm.user_blocks-1}"
        return f"RB{b} [{self.block_owner(b)}]: " + " | ".join(str(x) for x in self.rm.memory[b])

    def rm_memory_text(self):
        return "\n".join(self.rm_block_text(block) for block in range(self.rm.user_blocks))
