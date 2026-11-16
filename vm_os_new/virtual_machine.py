from real_machine import WORD_MIN, WORD_MAX, PAGE_SIZE, VM_PAGES, ptr_encode, wrap32


VALID_NOADDR = {"ADD", "SUB", "MUL", "DIV", "AND", "OR", "XOR", "CMP", "FORK", "HALT"}


def parse_addr2(s):
    if len(s) == 2 and all(c in "0123456789ABCDEFabcdef" for c in s):
        return int(s, 16)
    return None


def classify(op):
    u = str(op).strip().upper()
    if len(u) == 4 and u[:2] in ("LA", "LB", "SA", "SB", "JM", "JE", "JG", "JL"):
        a = parse_addr2(u[2:4])
        return (u[:2], a) if a is not None else (None, None)
    if len(u) == 4 and u[:2] in ("GD", "PD"):
        addr = parse_addr2(u[2:])
        if addr is not None and addr % PAGE_SIZE == 0:
            return (u[:2], addr // PAGE_SIZE)
        return (None, None)
    if u in VALID_NOADDR:
        return (u, None)
    return (None, None)


class VirtualMachine:
    """VM deskriptorius: antraste (ID), issaugota procesoriaus busena ir PTR.

    VM atmintis gyvena RM naudotojo atminties blokuose. Adresus verčia
    RealMachine.translate pagal PTR; VM objektas neturi savo puslapiu lenteles kopijos.
    owned_blocks - OS resursu apskaita (kuriuos blokus atlaisvinti), ne adresu vertimui.
    """

    def __init__(self, vm_id, pages_needed):
        self.id = vm_id
        self.pages_needed = pages_needed
        self.pt_block = None
        self.owned_blocks = []
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
        self.error = ""
        self.final_page_table = None
        self.final_pages = None

    @property
    def ptr(self):
        if self.pt_block is None:
            return None
        return ptr_encode(self.pages_needed, VM_PAGES, self.pt_block)

    def load_to_rm(self, rm):
        """OS (SUPERVISOR) ikelia VM konteksta; i USER grizta isejus is OS."""
        ptr = self.ptr
        if ptr is None:
            raise ValueError("VM neturi puslapiu lenteles")
        rm.ax, rm.bx, rm.pc = self.ax, self.bx, self.pc
        rm.zf, rm.sf, rm.of = self.zf, self.sf, self.of
        rm.ptr = ptr
        rm.pi = 0
        rm.si = 0

    def save_from_rm(self, rm):
        self.ax, self.bx, self.pc = rm.ax, rm.bx, rm.pc
        self.zf, self.sf, self.of = rm.zf, rm.sf, rm.of

    @staticmethod
    def set_flags_arith(rm, true_result, wrapped):
        rm.zf = (wrapped == 0)
        rm.sf = (wrapped < 0)
        if true_result < WORD_MIN or true_result > WORD_MAX:
            rm.of = True
            rm.pi = 4
        else:
            rm.of = False

    def step(self, rm):
        """Viena komanda realiu procesoriumi USER rezimu. Atmintis - tik per rm (PTR)."""
        if rm.mode != "USER" or self.state != "READY":
            raise PermissionError("Vykdyti galima tik parengta VM USER rezimu")
        if rm.ptr != self.ptr:
            raise RuntimeError("OS klaida: procesoriaus PTR nepriklauso vykdomai VM")
        rm.pi = 0
        rm.si = 0
        pc = rm.pc
        if pc not in self.code_addrs:
            rm.pi = 1
            rm.ti -= 1
            return {"ok": False, "instr": "<CODE ADDR ERR>", "ticks": 1}
        t = rm.translate(pc)
        if t is None:
            rm.pi = 1
            rm.ti -= 1
            return {"ok": False, "instr": "<ADDR ERR>", "ticks": 1}
        real, d, vpage = t
        instr = str(rm.memory[real][d]).strip().upper()
        op, arg = classify(instr)
        if op is None:
            rm.pi = 2
            rm.pc = pc + 1
            rm.ti -= 1
            return {"ok": False, "instr": instr, "ticks": 1, "page": vpage, "real": real}
        handler, ticks = COMMANDS[op]
        jumped = handler(self, rm, arg)
        if not jumped and rm.pi != 1:
            rm.pc = pc + 1
        rm.ti -= ticks
        return {"ok": rm.pi == 0, "instr": instr, "ticks": ticks, "page": vpage, "real": real}


# --- Komandu sistema: kiekviena komanda - atskira funkcija (vm, rm, arg) -> ar ivyko suolis ---

def _load(register):
    def command(vm, rm, addr):
        value = rm.read_word(addr)          # blogas adresas -> PI=1 (nustato MMU)
        if rm.pi == 0:
            if type(value) is int:
                setattr(rm, register, value)
            else:
                rm.pi = 3                   # i registra galima ikelti tik skaiciu
    return command


def _store(register):
    def command(vm, rm, addr):
        if rm.translate(addr) is None:
            rm.pi = 1
        elif addr in vm.code_addrs:
            rm.pi = 3                       # programos kodas apsaugotas nuo rasymo
        else:
            rm.write_word(addr, getattr(rm, register))
    return command


def _arith(operation):
    def command(vm, rm, _):
        true = operation(rm.ax, rm.bx)
        wrapped = wrap32(true)
        VirtualMachine.set_flags_arith(rm, true, wrapped)
        rm.ax = wrapped
    return command


def _div(vm, rm, _):
    a, b = rm.ax, rm.bx
    if b == 0:
        rm.pi = 5
        return
    true = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        true = -true                        # sveikoji dalis artyn nulio
    wrapped = wrap32(true)
    VirtualMachine.set_flags_arith(rm, true, wrapped)
    rm.ax = wrapped


def _logic(operation):
    def command(vm, rm, _):
        w = wrap32(operation(rm.ax, rm.bx))
        rm.ax = w
        rm.zf, rm.sf, rm.of = (w == 0), (w < 0), False
    return command


def _cmp(vm, rm, _):
    true = rm.ax - rm.bx
    w = wrap32(true)
    rm.zf, rm.sf = (w == 0), (w < 0)
    rm.of = not (WORD_MIN <= true <= WORD_MAX)


def _jump(condition):
    def command(vm, rm, target):
        if not condition(rm):
            return False
        if target not in vm.code_addrs or rm.translate(target) is None:
            rm.pi = 1
            return False
        rm.pc = target
        return True
    return command


def _interrupt(code):
    """GD/PD/FORK/HALT tik nustato SI; veiksma atlieka OS (handle_interrupt).
    GD/PD puslapio operanda OS perskaito pati is komandos adresu PC-1."""
    def command(vm, rm, _):
        rm.si = code
    return command


COMMANDS = {
    # komanda: (funkcija, trukme taktais)
    "LA": (_load("ax"), 1),
    "LB": (_load("bx"), 1),
    "SA": (_store("ax"), 1),
    "SB": (_store("bx"), 1),
    "ADD": (_arith(lambda a, b: a + b), 1),
    "SUB": (_arith(lambda a, b: a - b), 1),
    "MUL": (_arith(lambda a, b: a * b), 1),
    "DIV": (_div, 1),
    "AND": (_logic(lambda a, b: a & b), 1),
    "OR": (_logic(lambda a, b: a | b), 1),
    "XOR": (_logic(lambda a, b: a ^ b), 1),
    "CMP": (_cmp, 1),
    "JM": (_jump(lambda rm: True), 1),
    "JE": (_jump(lambda rm: rm.zf), 1),
    "JG": (_jump(lambda rm: not rm.zf and rm.sf == rm.of), 1),
    "JL": (_jump(lambda rm: rm.sf != rm.of), 1),
    "GD": (_interrupt(1), 3),
    "PD": (_interrupt(2), 3),
    "FORK": (_interrupt(3), 1),
    "HALT": (_interrupt(4), 1),
}
