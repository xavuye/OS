from real_machine import RealMachine, PAGE_SIZE, VM_PAGES, ptr_encode
from virtual_machine import VirtualMachine
from loader import parse_package


PI_TEXT = {0: "-", 1: "neteisingas virtualus adresas", 2: "neteisingas operacijos kodas",
           3: "neleistinas priskyrimas / apsaugos pazeidimas", 4: "aritmetinis perpildymas", 5: "dalyba is nulio"}
SI_TEXT = {0: "-", 1: "GD ivedimas is flash", 2: "PD isvedimas i spausdintuva", 3: "FORK kopija", 4: "HALT pabaiga"}


class ControlProgram:
    def __init__(self, seed=None, quantum=30):
        self.rm = RealMachine(seed=seed)
        self.rm.timer_quantum = quantum
        self.rm.ti = quantum
        self.vms = {}
        self.active_id = None
        self.order = []
        self.disk_jobs = {}
        self.next_fork_num = 9000
        self.log = []

    def msg(self, s):
        self.log.append(s)
        if len(self.log) > 500:
            self.log = self.log[-500:]

    def store_job_on_disk(self, job):
        blocks = self.rm.alloc_disk_blocks(job.pages)
        if blocks is None:
            return None
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
        for vp, b in enumerate(blocks):
            self.rm.disk[b] = list(images[vp])
        return blocks

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
        return accepted, errors

    def load_package_file(self, path):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        return self.load_package_text(text)

    def release_vm(self, vm):
        if vm.pt_block is None and not vm.allocated_pages():
            return
        vm.final_page_table = list(vm.page_table)
        vm.final_pages = {}
        for p, real in enumerate(vm.page_table):
            if real is not None and 0 <= real < self.rm.user_blocks:
                vm.final_pages[p] = list(self.rm.memory[real])
        blocks = [vm.page_table[p] for p in vm.allocated_pages() if vm.page_table[p] is not None]
        if vm.pt_block is not None:
            blocks.append(vm.pt_block)
        self.rm.free_block_list(blocks)
        vm.page_table = [None] * VM_PAGES
        vm.pt_block = None
        vm._loaded = False
        if self.active_id == vm.id:
            self.rm.ptr = ptr_encode(0, VM_PAGES, 0)
        self.msg(f"VM {vm.id} resursai atlaisvinti ({len(blocks)} bloku)")

    def release_halted(self):
        for vm in list(self.vms.values()):
            if vm.state != "READY" and (vm.allocated_pages() or vm.pt_block is not None):
                self.release_vm(vm)

    def create_vm(self, job_id):
        if job_id not in self.disk_jobs:
            return f"Nera uzduoties diske: {job_id}"
        job = self.disk_jobs[job_id]
        old = self.vms.get(job_id)
        if old is not None:
            if old.state == "READY":
                return f"VM {job_id} jau sukurta"
            if old.allocated_pages() or old.pt_block is not None:
                self.release_vm(old)
            del self.vms[job_id]
            if job_id in self.order:
                self.order.remove(job_id)
            if self.active_id == job_id:
                self.active_id = None
        if not job.disk_blocks:
            return f"Uzduotis {job_id} nera diske"
        need = job.pages + 1
        blocks = self.rm.alloc_random_blocks(need)
        if blocks is None:
            self.release_halted()
            blocks = self.rm.alloc_random_blocks(need)
        if blocks is None:
            return (f"Nepakanka laisvos atminties VM {job.job_id}: reikia {need} bloku, "
                    f"laisva {len(self.rm.free_blocks)}")
        pt_block = blocks[0]
        page_blocks = blocks[1:]
        vm = VirtualMachine(job.job_id, job.pages)
        vm.pt_block = pt_block
        for vp in range(job.pages):
            vm.page_table[vp] = page_blocks[vp]
        for k in range(PAGE_SIZE):
            self.rm.memory[pt_block][k] = vm.page_table[k] if vm.page_table[k] is not None else -1
        for vp in range(job.pages):
            real = vm.page_table[vp]
            if vp < len(job.disk_blocks):
                self.rm.xchg("DISK", job.disk_blocks[vp], "USER", real, vm_id=vm.id)
            else:
                self.rm.memory[real] = [0] * PAGE_SIZE
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
        self.order.append(vm.id)
        active = self.active()
        if active is not None and active.state == "READY":
            active.save_from_rm(self.rm)
        self.active_id = vm.id
        vm.load_to_rm(self.rm)
        self.rm.ti = self.rm.timer_quantum
        vm._loaded = True
        mapping = ", ".join(f"VP{p}->RB{vm.page_table[p]}" for p in range(job.pages))
        self.msg(f"Sukurta VM {vm.id}: PT blokas {pt_block}, {mapping} (ne nuosekliai, atsitiktinai)")
        self.msg(f"Valdymas atiduotas veliausiai uzkrautajai VM {vm.id}")
        return f"VM {vm.id} sukurta. Valdymas -> {vm.id}"

    def remove_vm(self, vm_id):
        vm = self.vms.get(vm_id)
        job = self.disk_jobs.get(vm_id)
        if vm is None and job is None:
            return f"Nera VM/uzduoties {vm_id}"
        if vm is not None and vm.state == "READY":
            return f"VM {vm_id} dar vykdoma"
        if vm is not None:
            if vm.allocated_pages() or vm.pt_block is not None:
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

    def active(self):
        if self.active_id is None:
            return None
        return self.vms.get(self.active_id)

    def switch(self, vm_id):
        if vm_id not in self.vms:
            return f"Nera VM {vm_id}"
        if self.active() is not None and self.active().state == "READY":
            self.active().save_from_rm(self.rm)
        self.active_id = vm_id
        vm = self.vms[vm_id]
        if vm.state == "READY":
            vm.load_to_rm(self.rm)
            self.rm.ti = self.rm.timer_quantum
            vm._loaded = True
        return f"Aktyvi VM {vm_id}"

    def do_fork(self, parent):
        self.next_fork_num += 1
        child_id = f"{self.next_fork_num:04d}"
        while child_id in self.vms or child_id in self.disk_jobs:
            self.next_fork_num += 1
            child_id = f"{self.next_fork_num:04d}"
        need = parent.pages_needed + 1
        blocks = self.rm.alloc_random_blocks(need)
        if blocks is None:
            self.release_halted()
            blocks = self.rm.alloc_random_blocks(need)
        if blocks is None:
            self.rm.pi = 3
            parent.error = "FORK: nepakanka atminties"
            return None
        pt_block = blocks[0]
        page_blocks = blocks[1:]
        child = VirtualMachine(child_id, parent.pages_needed)
        child.pt_block = pt_block
        for vp in range(parent.pages_needed):
            child.page_table[vp] = page_blocks[vp]
            src = parent.page_table[vp]
            dst = child.page_table[vp]
            self.rm.memory[dst] = list(self.rm.memory[src])
        for k in range(PAGE_SIZE):
            self.rm.memory[pt_block][k] = child.page_table[k] if child.page_table[k] is not None else -1
        parent.save_from_rm(self.rm)
        child.ax = 0
        child.bx = parent.bx
        child.pc = parent.pc
        child.zf = parent.zf
        child.sf = parent.sf
        child.of = parent.of
        child.code_start = parent.code_start
        child.code_addrs = set(parent.code_addrs)
        child.data_start = parent.data_start
        child.data_count = parent.data_count
        child.state = "READY"
        child.error = ""
        self.rm.ax = int(child_id)
        parent.save_from_rm(self.rm)
        self.vms[child_id] = child
        self.order.append(child_id)
        self.msg(f"FORK: tevas {parent.id} AX={parent.ax}, vaikas {child_id} AX=0 PC={child.pc:02X}")
        return child

    def handle_state(self, vm, info):
        rm = self.rm
        timer_hit = rm.ti <= 0
        if rm.pi != 0:
            vm.save_from_rm(rm)
            vm.state = "HALTED"
            vm.error = f"PI={rm.pi} {PI_TEXT.get(rm.pi, '?')}"
            self.msg(f"VM {vm.id} programinis pertraukimas PI={rm.pi} "
                     f"({PI_TEXT.get(rm.pi, '?')}) instr {info['instr']}")
            rm.mode = "SUPERVISOR"
            rm.pi = 0
            if timer_hit:
                rm.ti = self.rm.timer_quantum
            self.release_vm(vm)
            return "PI"
        if rm.si == 1:
            vm.save_from_rm(rm)
            rm.mode = "SUPERVISOR"
            p = vm.io_page
            real = vm.page_real(rm, p)
            if real is None:
                rm.pi = 1
                vm.state = "HALTED"
                vm.error = "GD: neteisingas puslapis"
                self.msg(f"VM {vm.id} GD{p}: neteisingas puslapis -> PI=1")
            elif not rm.flash:
                rm.pi = 1
                vm.state = "HALTED"
                vm.error = "GD: tuscias flash srautas"
                self.msg(f"VM {vm.id} GD{p}: flash tuscias -> PI=1")
            else:
                blk = list(rm.flash[0])
                rm.xchg("FLASH", 0, "USER", real, vm_id=vm.id)
                rm.memory[real] = list(blk)
                rm.flash.pop(0)
                self.msg(f"VM {vm.id} GD{p}0: flash -> RB{real} per XCHG")
            rm.si = 0
            if timer_hit:
                self.msg(f"Taimeris TI=0: kvantas atnaujintas {self.rm.timer_quantum}")
                rm.ti = self.rm.timer_quantum
            if vm.state == "READY":
                rm.mode = "USER"
            else:
                self.release_vm(vm)
                rm.mode = "SUPERVISOR"
            return "GD"
        if rm.si == 2:
            vm.save_from_rm(rm)
            rm.mode = "SUPERVISOR"
            p = vm.io_page
            real = vm.page_real(rm, p)
            if real is None:
                rm.pi = 1
                vm.state = "HALTED"
                vm.error = "PD: neteisingas puslapis"
                self.msg(f"VM {vm.id} PD{p}: neteisingas puslapis -> PI=1")
            else:
                rm.xchg("USER", real, "PRINTER", p, vm_id=vm.id)
                vals = " ".join(str(x) for x in rm.memory[real])
                self.msg(f"VM {vm.id} PD{p}0: RB{real} -> spausdintuvas: [{vals}]")
            rm.si = 0
            if timer_hit:
                self.msg(f"Taimeris TI=0: kvantas atnaujintas {self.rm.timer_quantum}")
                rm.ti = self.rm.timer_quantum
            if vm.state == "READY":
                rm.mode = "USER"
            else:
                self.release_vm(vm)
                rm.mode = "SUPERVISOR"
            return "PD"
        if rm.si == 3:
            vm.save_from_rm(rm)
            rm.mode = "SUPERVISOR"
            child = self.do_fork(vm)
            rm.si = 0
            if timer_hit:
                rm.ti = self.rm.timer_quantum
            if child is None:
                vm.state = "HALTED"
                if not vm.error:
                    vm.error = "FORK: nepakanka atminties"
                self.msg(f"VM {vm.id} FORK nepavyko: {vm.error}")
                self.release_vm(vm)
                rm.mode = "SUPERVISOR"
                return "FORK_FAIL"
            rm.mode = "USER"
            return "FORK"
        if rm.si == 4:
            vm.save_from_rm(rm)
            vm.state = "HALTED"
            self.msg(f"VM {vm.id} HALT: programa baige darba AX={vm.ax} BX={vm.bx}")
            rm.mode = "SUPERVISOR"
            rm.si = 0
            if timer_hit:
                rm.ti = self.rm.timer_quantum
            self.release_vm(vm)
            return "HALT"
        if timer_hit:
            self.msg(f"VM {vm.id} taimeris TI=0: kvantas atnaujintas {self.rm.timer_quantum}")
            rm.ti = self.rm.timer_quantum
            rm.mode = "USER"
            return "TIMER"
        rm.mode = "USER"
        return "OK"

    def prepare_run(self, vm_id=None):
        if vm_id is not None:
            self.switch(vm_id)
        vm = self.active()
        if vm is None:
            return None, "Nera aktyvios VM. Ikeltkite uzduoti: load <failas>"
        if vm.state != "READY":
            nxt = self.next_ready(vm.id)
            if nxt is None:
                return None, f"VM {vm.id} baigta ({vm.error or 'HALT'}). Kitos parengtos VM nera."
            self.switch(nxt)
            vm = self.active()
        already = getattr(vm, "_loaded", False)
        if not already:
            vm.load_to_rm(self.rm)
            vm._loaded = True
        else:
            vm.ax, vm.bx, vm.pc = self.rm.ax, self.rm.bx, self.rm.pc
            vm.zf, vm.sf, vm.of = self.rm.zf, self.rm.sf, self.rm.of
            if self.rm.mode != "USER":
                self.rm.mode = "USER"
        return vm, ""

    def next_ready(self, exclude=None):
        for vid in self.order:
            if vid != exclude and vid in self.vms and self.vms[vid].state == "READY":
                return vid
        return None

    def step_once(self, vm_id=None):
        vm, err = self.prepare_run(vm_id)
        if vm is None:
            return {"done": True, "msg": err}
        rm = self.rm
        before = dict(AX=rm.ax, BX=rm.bx, PC=rm.pc, ZF=int(rm.zf), SF=int(rm.sf), OF=int(rm.of),
                      TI=rm.ti, PI=rm.pi, SI=rm.si, MODE=rm.mode)
        info = vm.step(rm)
        page = info.get("page")
        real = None
        page_vals = None
        if page is not None and 0 <= page < VM_PAGES and vm.page_table[page] is not None:
            real = vm.page_table[page]
            page_vals = list(rm.memory[real])
        event = self.handle_state(vm, info)
        if vm.state == "READY":
            vm.save_from_rm(rm)
        nxt = vm.peek_next(rm) if vm.state == "READY" else "-"
        after = dict(AX=rm.ax, BX=rm.bx, PC=rm.pc, ZF=int(rm.zf), SF=int(rm.sf), OF=int(rm.of),
                     TI=rm.ti, PI=rm.pi, SI=rm.si, MODE=rm.mode)
        done = vm.state != "READY"
        return {"done": False, "vm": vm.id, "instr": info["instr"], "before": before, "after": after,
                "event": event, "halted": done, "next": nxt, "page": page, "real": real,
                "page_vals": page_vals, "error": vm.error}

    def run_all(self, max_steps=10000):
        for _ in range(max_steps):
            r = self.step_once()
            yield r
            if r.get("done"):
                return
            vm = self.active()
            if vm is None:
                return
            if vm.state != "READY":
                nxt = self.next_ready(vm.id)
                if nxt is None:
                    return
                self.switch(nxt)
        yield {"done": True, "msg": "Pasiektas vykdymo zingsniu limitas"}

    def vm_memory_text(self, vm_id=None, pages=None):
        vm = self.vms.get(vm_id or self.active_id)
        if vm is None:
            return "Nera VM"
        lines = [f"VM {vm.id} atmintis (VP -> RB):"]
        if vm.allocated_pages():
            plist = vm.allocated_pages() if pages is None else pages
            for p in plist:
                real = vm.page_table[p] if 0 <= p < VM_PAGES else None
                vals = self.rm.memory[real] if real is not None else ["-"] * PAGE_SIZE
                real_text = f"RB{real}" if real is not None else "RB-"
                lines.append(f"VP{p:X}->{real_text}: " + " | ".join(str(x) for x in vals))
        elif getattr(vm, "final_pages", None):
            lines.append("(resursai atlaisvinti; paskutine kopija)")
            plist = sorted(vm.final_pages) if pages is None else pages
            for p in plist:
                real = vm.final_page_table[p] if vm.final_page_table and 0 <= p < VM_PAGES else None
                vals = vm.final_pages.get(p, ["-"] * PAGE_SIZE)
                real_text = f"RB{real}" if real is not None else "RB-"
                lines.append(f"VP{p:X}->{real_text}: " + " | ".join(str(x) for x in vals))
        else:
            lines.append("(VM atmintis atlaisvinta)")
        return "\n".join(lines)

    def rm_block_text(self, b):
        if not (0 <= b < self.rm.user_blocks):
            return f"Blokas {b} uz ribu 0-{self.rm.user_blocks-1}"
        vals = self.rm.memory[b]
        owner = "-"
        for vid, vm in self.vms.items():
            if vm.pt_block == b:
                owner = f"PT({vid})"
            elif b in (vm.page_table[p] for p in vm.allocated_pages()):
                owner = f"VM{vid}"
        return f"RB{b} [{owner}]: " + " | ".join(str(x) for x in vals)
