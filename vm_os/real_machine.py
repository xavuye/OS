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


def ptr_encode(allocated, max_pages, pt_base_addr):
    return ((allocated & 0xFF) << 24) | ((max_pages & 0xFF) << 16) | (pt_base_addr & 0xFFFF)


def ptr_decode(ptr):
    allocated = (ptr >> 24) & 0xFF
    max_pages = (ptr >> 16) & 0xFF
    pt_base = ptr & 0xFFFF
    return allocated, max_pages, pt_base


class RealMachine:
    def __init__(self, user_blocks=USER_BLOCKS, seed=None):
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
        self.st = ""
        self.dt = ""
        self.sb = 0
        self.db = 0
        self.flash = []
        self.printer = []
        self.disk = [None] * DISK_BLOCKS
        self.disk_free = list(range(DISK_BLOCKS))
        self.channel_log = []

    def reset_cpu(self):
        self.ax = 0
        self.bx = 0
        self.pc = 0
        self.zf = False
        self.sf = False
        self.of = False
        self.pi = 0
        self.si = 0
        self.ti = self.timer_quantum
        self.mode = "USER"

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
        data = [0] * PAGE_SIZE
        if src_type == "USER":
            data = list(self.memory[src_block])
        elif src_type == "DISK":
            blk = self.disk[src_block]
            data = list(blk) if blk is not None else [0] * PAGE_SIZE
        elif src_type == "FLASH":
            data = list(self.flash[src_block])
        elif src_type == "SUP":
            data = [0] * PAGE_SIZE
        self.st = src_type
        self.sb = src_block
        self.dt = dst_type
        self.db = dst_block
        if dst_type == "USER":
            self.memory[dst_block] = list(data)
        elif dst_type == "DISK":
            self.disk[dst_block] = list(data)
        elif dst_type == "PRINTER":
            self.printer.append({"vm": vm_id, "page": dst_block, "data": list(data)})
        self.channel_log.append(f"XCHG {src_type}[{src_block}] -> {dst_type}[{dst_block}]")
        if len(self.channel_log) > 200:
            self.channel_log = self.channel_log[-200:]
        return list(data)

    def dump_block(self, b):
        if 0 <= b < self.user_blocks:
            return list(self.memory[b])
        return None

    def status(self):
        allocated, maxp, base = ptr_decode(self.ptr)
        return {
            "AX": self.ax, "BX": self.bx, "PC": self.pc,
            "ZF": int(self.zf), "SF": int(self.sf), "OF": int(self.of),
            "PTR": f"{self.ptr:08X} (a0={allocated} a1={maxp} base={base})",
            "MODE": self.mode, "PI": self.pi, "SI": self.si, "TI": self.ti,
            "ST": self.st, "DT": self.dt, "SB": self.sb, "DB": self.db,
            "free_blocks": len(self.free_blocks),
            "disk_free": len(self.disk_free),
            "disk_blocks": len(self.disk),
            "flash_blocks": len(self.flash),
            "printer_jobs": len(self.printer),
        }
