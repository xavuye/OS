import random


WORD_MIN = -(2 ** 31)
WORD_MAX = 2 ** 31 - 1
PAGE_SIZE = 16
VM_PAGES = 16
VM_WORDS = VM_PAGES * PAGE_SIZE
USER_BLOCKS = 64
DISK_BLOCKS = 128
TIMER_QUANTUM = 30


def wrap32(v):
    v = v & 0xFFFFFFFF
    if v >= 0x80000000:
        return v - 0x100000000
    return v


def ptr_encode(allocated, max_pages, pt_block):
    """PTR = a0 a1 a2a3 (ataskaita 1.4).

    a0   - VM skirtu puslapiu skaicius,
    a1   - maksimalus puslapiu skaicius (16),
    a2a3 - puslapiu lenteles bloko numeris; realus lenteles adresas = 16 * a2a3.
    """
    if (any(type(field) is not int for field in (allocated, max_pages, pt_block))
            or not (0 <= allocated <= max_pages and 1 <= max_pages <= VM_PAGES)
            or not 0 <= pt_block <= 0xFFFF):
        raise ValueError("Neteisingi PTR laukai")
    return (allocated << 24) | (max_pages << 16) | pt_block


def is_word(value):
    return ((type(value) is int and WORD_MIN <= value <= WORD_MAX)
            or (isinstance(value, str) and 1 <= len(value) <= 4))


def ptr_decode(ptr):
    if type(ptr) is not int or not 0 <= ptr <= 0xFFFFFFFF:
        raise ValueError("PTR turi buti 32 bitu reiksme")
    return (ptr >> 24) & 0xFF, (ptr >> 16) & 0xFF, ptr & 0xFFFF


class ChannelDevice:
    """Kanalu irenginys: sinchroninis bloko perdavimas, valdomas SUPERVISOR rezimu per ST/DT/SB/DB."""

    def __init__(self, rm):
        self._rm = rm
        self.st = self.dt = ""
        self.sb = self.db = 0
        self.log = []

    def _require_supervisor(self):
        if self._rm.mode != "SUPERVISOR":
            raise PermissionError("Kanalas prieinamas tik SUPERVISOR rezimu")

    def configure(self, src_type, src_block, dst_type, dst_block):
        self._require_supervisor()
        self.st, self.sb = src_type, src_block
        self.dt, self.db = dst_type, dst_block

    def _memory_endpoint(self, kind, block, *, source):
        rm = self._rm
        if kind == "USER":
            memory, free = rm.memory, rm.free_blocks
        elif kind == "SUP":
            memory, free = rm.supervisor_memory, ()
        elif kind == "DISK":
            memory, free = rm.disk, rm.disk_free
        elif source and kind == "FLASH":
            memory, free = rm.flash, ()
        else:
            raise ValueError(f"Neleistinas kanalo tipas: {kind}")
        if type(block) is not int or not 0 <= block < len(memory) or block in free:
            raise ValueError(f"Nepasiekiamas kanalo blokas: {kind}[{block}]")
        return memory

    def xchg(self, vm_id="SYS"):
        self._require_supervisor()
        source = self._memory_endpoint(self.st, self.sb, source=True)
        data = source[self.sb]
        if not isinstance(data, list) or len(data) != PAGE_SIZE or not all(map(is_word, data)):
            raise ValueError("Kanalui reikia 16 zodziu bloko")
        if self.dt == "PRINTER":
            if type(self.db) is not int or self.db < 0:
                raise ValueError("Neteisingas spausdintuvo puslapis")
            self._rm.printer.append({"vm": vm_id, "page": self.db, "data": data.copy()})
        else:
            destination = self._memory_endpoint(self.dt, self.db, source=False)
            destination[self.db] = data.copy()
        self.log.append(f"XCHG {self.st}[{self.sb}] -> {self.dt}[{self.db}]")
        if len(self.log) > 200:
            del self.log[0]
        return data.copy()


class RealMachine:
    def __init__(self, user_blocks=USER_BLOCKS, seed=None):
        if type(user_blocks) is not int or not 1 <= user_blocks <= 0x10000:
            raise ValueError("Bloko numeris turi tilpti i PTR a2a3 lauka")
        self.user_blocks = user_blocks
        self.memory = [[0] * PAGE_SIZE for _ in range(user_blocks)]
        self.free_blocks = list(range(user_blocks))
        self.rnd = random.Random(seed)
        self.ax = 0
        self.bx = 0
        self.pc = 0
        self.zf = False
        self.sf = False
        self.of = False
        self.ptr = ptr_encode(0, VM_PAGES, 0)
        self.mode = "SUPERVISOR"
        self.pi = 0
        self.si = 0
        self.ti = TIMER_QUANTUM
        self.timer_quantum = TIMER_QUANTUM
        self.flash = []
        self.printer = []
        self.disk = [None] * DISK_BLOCKS
        self.disk_free = list(range(DISK_BLOCKS))
        self.supervisor_memory = [[0] * PAGE_SIZE]
        self.channel = ChannelDevice(self)

    # --- Puslapiavimo mechanizmas: naudoja TIK PTR ir lentele RM atmintyje ---
    def translate(self, vaddr, ptr=None):
        """Virtualus adresas -> (realus blokas, poslinkis, puslapis) arba None.

        Be ptr argumento verciama pagal procesoriaus PTR (taip dirba CPU USER rezimu).
        OS gali paduoti kitos VM PTR, kai SUPERVISOR rezimu perziuri neaktyvia VM.
        """
        ptr = self.ptr if ptr is None else ptr
        if type(vaddr) is not int or not 0 <= vaddr < VM_WORDS:
            return None
        allocated, _, pt_block = ptr_decode(ptr)
        page, offset = divmod(vaddr, PAGE_SIZE)
        if page >= allocated or not 0 <= pt_block < self.user_blocks:
            return None
        real = self.memory[pt_block][page]
        if type(real) is not int or not 0 <= real < self.user_blocks or real == pt_block:
            return None
        return real, offset, page

    def read_word(self, vaddr):
        """CPU skaitymas per PTR; klaida -> PI=1."""
        t = self.translate(vaddr)
        if t is None:
            self.pi = 1
            return None
        real, offset, _ = t
        return self.memory[real][offset]

    def write_word(self, vaddr, value):
        """CPU rasymas per PTR; blogas adresas -> PI=1, ne zodis -> PI=3."""
        t = self.translate(vaddr)
        if t is None:
            self.pi = 1
            return False
        if not is_word(value):
            self.pi = 3
            return False
        real, offset, _ = t
        self.memory[real][offset] = value
        return True

    def alloc_random_blocks(self, n):
        if len(self.free_blocks) < n:
            return None
        chosen = self.rnd.sample(self.free_blocks, n)
        for b in chosen:
            self.free_blocks.remove(b)
            self.memory[b] = [0] * PAGE_SIZE
        return chosen

    def free_block_list(self, blocks):
        for b in blocks:
            if 0 <= b < self.user_blocks:
                self.memory[b] = [0] * PAGE_SIZE
                if b not in self.free_blocks:
                    self.free_blocks.append(b)
        self.free_blocks.sort()

    def alloc_disk_blocks(self, n):
        if n < 0 or len(self.disk_free) < n:
            return None
        chosen = self.disk_free[:n]
        for b in chosen:
            self.disk_free.remove(b)
            self.disk[b] = [0] * PAGE_SIZE
        return chosen

    def free_disk_blocks(self, blocks):
        for b in blocks:
            if 0 <= b < len(self.disk):
                self.disk[b] = None
                if b not in self.disk_free:
                    self.disk_free.append(b)
        self.disk_free.sort()

    def xchg(self, src_type, src_block, dst_type, dst_block, vm_id="SYS"):
        self.channel.configure(src_type, src_block, dst_type, dst_block)
        return self.channel.xchg(vm_id)

    def dump_block(self, b):
        if 0 <= b < self.user_blocks:
            return list(self.memory[b])
        return None

    def status(self):
        allocated, maxp, pt_block = ptr_decode(self.ptr)
        return {
            "AX": self.ax, "BX": self.bx, "PC": self.pc,
            "ZF": int(self.zf), "SF": int(self.sf), "OF": int(self.of),
            "PTR": f"{self.ptr:08X} (a0={allocated} a1={maxp} a2a3=PT blokas {pt_block}, adr. {pt_block * PAGE_SIZE})",
            "MODE": self.mode, "PI": self.pi, "SI": self.si, "TI": self.ti,
            "ST": self.channel.st, "DT": self.channel.dt,
            "SB": self.channel.sb, "DB": self.channel.db,
            "free_blocks": len(self.free_blocks),
            "disk_free": len(self.disk_free),
            "disk_blocks": len(self.disk),
            "flash_blocks": len(self.flash),
            "printer_jobs": len(self.printer),
        }
