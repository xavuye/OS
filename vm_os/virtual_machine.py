from real_machine import WORD_MIN, WORD_MAX, PAGE_SIZE, VM_PAGES, ptr_decode, wrap32


VALID_NOADDR = {"ADD", "SUB", "MUL", "DIV", "AND", "OR", "XOR", "CMP", "FORK", "HALT"}


def parse_addr2(s):
    try:
        v = int(s, 16)
    except ValueError:
        return None
    if 0 <= v <= 0xFF and len(s) == 2:
        return v
    return None


def classify(op):
    u = op.strip().upper()
    if len(u) == 4 and u[:2] in ("LA", "LB", "SA", "SB"):
        a = parse_addr2(u[2:4])
        if a is not None:
            return (u[:2], a)
        return (None, None)
    if len(u) == 4 and u[:2] in ("JM", "JE", "JG", "JL"):
        a = parse_addr2(u[2:4])
        if a is not None:
            return (u[:2], a)
        return (None, None)
    if len(u) == 4 and u[:2] in ("GD", "PD"):
        if u[3] == "0":
            try:
                p = int(u[2], 16)
                return (u[:2], p)
            except ValueError:
                return (None, None)
        return (None, None)
    if u in VALID_NOADDR:
        return (u, None)
    return (None, None)


class VirtualMachine:
    def __init__(self, vm_id, pages_needed):
        self.id = vm_id
        self.pages_needed = pages_needed
        self.page_table = [None] * VM_PAGES
        self.pt_block = None
        self.ax = 0
        self.bx = 0
        self.pc = 0
        self.zf = False
        self.sf = False
        self.of = False
        self.code_start = 0
        self.code_addrs = set()
        self.data_start = 0
        self.data_count = 0
        self.state = "READY"
        self.io_page = 0
        self.error = ""
        self.final_page_table = None
        self.final_pages = None

    def allocated_pages(self):
        return [p for p in range(VM_PAGES) if self.page_table[p] is not None]

    def load_to_rm(self, rm, quantum=None):
        from real_machine import ptr_encode
        rm.ax = self.ax
        rm.bx = self.bx
        rm.pc = self.pc
        rm.zf = self.zf
        rm.sf = self.sf
        rm.of = self.of
        base = self.pt_block * PAGE_SIZE
        rm.ptr = ptr_encode(self.pages_needed, VM_PAGES, base)
        rm.mode = "USER"
        rm.pi = 0
        rm.si = 0
        if quantum is not None:
            rm.ti = quantum
            rm.timer_quantum = quantum

    def save_from_rm(self, rm):
        self.ax = rm.ax
        self.bx = rm.bx
        self.pc = rm.pc
        self.zf = rm.zf
        self.sf = rm.sf
        self.of = rm.of

    def page_real(self, rm, page):
        if not (0 <= page < VM_PAGES):
            return None
        allocated, _, base = ptr_decode(rm.ptr)
        if page >= allocated:
            return None
        pt_block = base // PAGE_SIZE
        if not (0 <= pt_block < rm.user_blocks):
            return None
        real = rm.memory[pt_block][page]
        if not isinstance(real, int) or not (0 <= real < rm.user_blocks):
            return None
        return real

    def translate(self, rm, vaddr):
        if not (0 <= vaddr <= 0xFF):
            return None
        p = vaddr // PAGE_SIZE
        d = vaddr % PAGE_SIZE
        real = self.page_real(rm, p)
        if real is None:
            return None
        return (real, d, p)

    def read_virtual(self, rm, vaddr):
        t = self.translate(rm, vaddr)
        if t is None:
            rm.pi = 1
            return None
        real, d, _ = t
        return rm.memory[real][d]

    def write_virtual(self, rm, vaddr, val):
        t = self.translate(rm, vaddr)
        if t is None:
            rm.pi = 1
            return False
        if vaddr in self.code_addrs:
            rm.pi = 3
            return False
        real, d, _ = t
        rm.memory[real][d] = val
        return True

    def peek_next(self, rm):
        pc = rm.pc
        t = self.translate(rm, pc)
        if t is None:
            return f"<PI=1 neteisingas adresas PC={pc:02X}>"
        real, d, _ = t
        return f"{rm.memory[real][d]}"

    def set_flags_arith(self, rm, true_result, wrapped):
        rm.zf = (wrapped == 0)
        rm.sf = (wrapped < 0)
        if true_result < WORD_MIN or true_result > WORD_MAX:
            rm.of = True
            rm.pi = 4
        else:
            rm.of = False

    def step(self, rm):
        rm.mode = "USER"
        rm.pi = 0
        rm.si = 0
        pc = rm.pc
        t = self.translate(rm, pc)
        if t is None:
            rm.pi = 1
            rm.ti -= 1
            return {"ok": False, "instr": "<ADDR ERR>", "ticks": 1}
        real, d, vpage = t
        raw = rm.memory[real][d]
        instr = str(raw).strip().upper()
        op, arg = classify(instr)
        if op is None:
            rm.pi = 2
            rm.pc = (pc + 1) & 0xFF
            rm.ti -= 1
            return {"ok": False, "instr": instr, "ticks": 1, "page": vpage, "real": real}
        ticks = 1
        jumped = False

        def mem_load(addr):
            return self.read_virtual(rm, addr)

        if op == "LA":
            v = mem_load(arg)
            if rm.pi == 0:
                rm.ax = v if isinstance(v, int) else 0
                if not isinstance(v, int):
                    rm.pi = 3
        elif op == "LB":
            v = mem_load(arg)
            if rm.pi == 0:
                rm.bx = v if isinstance(v, int) else 0
                if not isinstance(v, int):
                    rm.pi = 3
        elif op == "SA":
            if isinstance(rm.ax, int):
                self.write_virtual(rm, arg, rm.ax)
            else:
                rm.pi = 3
        elif op == "SB":
            if isinstance(rm.bx, int):
                self.write_virtual(rm, arg, rm.bx)
            else:
                rm.pi = 3
        elif op in ("ADD", "SUB", "MUL", "DIV", "AND", "OR", "XOR", "CMP"):
            a = rm.ax
            b = rm.bx
            if not isinstance(a, int) or not isinstance(b, int):
                rm.pi = 3
            elif op == "ADD":
                true = a + b
                w = wrap32(true)
                self.set_flags_arith(rm, true, w)
                rm.ax = w
            elif op == "SUB":
                true = a - b
                w = wrap32(true)
                self.set_flags_arith(rm, true, w)
                rm.ax = w
            elif op == "MUL":
                true = a * b
                w = wrap32(true)
                self.set_flags_arith(rm, true, w)
                rm.ax = w
            elif op == "DIV":
                if b == 0:
                    rm.pi = 5
                else:
                    true = abs(a) // abs(b)
                    if (a < 0) != (b < 0):
                        true = -true
                    w = wrap32(true)
                    self.set_flags_arith(rm, true, w)
                    rm.ax = w
            elif op == "AND":
                w = (a & b) & 0xFFFFFFFF
                w = wrap32(w)
                rm.ax = w
                rm.zf = (w == 0)
                rm.sf = (w < 0)
                rm.of = False
            elif op == "OR":
                w = (a | b) & 0xFFFFFFFF
                w = wrap32(w)
                rm.ax = w
                rm.zf = (w == 0)
                rm.sf = (w < 0)
                rm.of = False
            elif op == "XOR":
                w = (a ^ b) & 0xFFFFFFFF
                w = wrap32(w)
                rm.ax = w
                rm.zf = (w == 0)
                rm.sf = (w < 0)
                rm.of = False
            elif op == "CMP":
                true = a - b
                w = wrap32(true)
                rm.zf = (w == 0)
                rm.sf = (w < 0)
                if true < WORD_MIN or true > WORD_MAX:
                    rm.of = True
                else:
                    rm.of = False
        elif op in ("JM", "JE", "JG", "JL"):
            tgt = arg
            tp = tgt // PAGE_SIZE
            if self.page_real(rm, tp) is None:
                rm.pi = 1
            else:
                do = False
                if op == "JM":
                    do = True
                elif op == "JE":
                    do = rm.zf
                elif op == "JG":
                    do = (not rm.zf) and (rm.sf == rm.of)
                elif op == "JL":
                    do = (rm.sf != rm.of)
                if do:
                    rm.pc = tgt & 0xFF
                    jumped = True
        elif op == "GD":
            self.io_page = arg
            if self.page_real(rm, arg) is None:
                rm.pi = 1
            else:
                rm.si = 1
            ticks = 3
        elif op == "PD":
            self.io_page = arg
            if self.page_real(rm, arg) is None:
                rm.pi = 1
            else:
                rm.si = 2
            ticks = 3
        elif op == "FORK":
            rm.si = 3
        elif op == "HALT":
            rm.si = 4
        if not jumped and rm.pi not in (1,) or (op not in ("JM", "JE", "JG", "JL")):
            if not jumped:
                rm.pc = (pc + 1) & 0xFF
        rm.ti -= ticks
        return {"ok": rm.pi == 0, "instr": instr, "ticks": ticks, "page": vpage, "real": real}
