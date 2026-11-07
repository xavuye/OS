import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_program import ControlProgram


HELP = """Komandos:
 load <failas>        nuskaityti paketa (flash) ir irasyti uzduotis i diska
 create <ID>          sukurti VM is diske esancios uzduoties
 remove <ID>          pasalinti baigta VM/uzduoti ir atlaisvinti resursus
 list                 disko uzduotys / VM sarasas
 use <ID>             perjungti aktyvia VM (valdymas veliausiai uzkrautajai)
 regs                 registrai + PI/SI/TI/MODE/PTR
 next                 sekanti vykdoma komanda
 step [n]             vykdyti n zingsniu (po 1)
 run                  vykdyti iki HALT/klaidos
 vmmem [p]            VM atmintis (visos arba vienas puslapis)
 rmmem <blokas>       RM naudotojo atminties blokas
 rmall                visi RM blokai trumpai
 pt                   puslapiu lentele
 dev                  isoriniai irenginiai (flash/spausdintuvas/disk/kanalas)
 timer <n>            nustatyti kvanta
 flash_add <a,b,..>   prideti 16 zodziu flash bloka (kableliais)
 flash_file <failas>  prideti flash bloka is failo (16 reiksmiu)
 setreg AX|BX|PC <v>  keisti registra vykdymo metu
 setmem <AA> <v>      keisti VM zodi adresu AA (hex 00-FF)
 printer              spausdintuvo turinys
 log                  OS zurnalas
 help                 si pagalba
 quit                 iseiti"""


def parse_num(s):
    try:
        return int(s, 0)
    except ValueError:
        pass
    try:
        return int(s, 16)
    except ValueError:
        return None


def fmt_regs(osys):
    rm = osys.rm
    vm = osys.active()
    s = rm.status()
    head = f"Aktyvi VM: {vm.id if vm else '-'} state={vm.state if vm else '-'}"
    rows = [head]
    rows.append(f"AX={s['AX']} BX={s['BX']} PC={s['PC']:02X} ZF={s['ZF']} SF={s['SF']} OF={s['OF']}")
    rows.append(f"PTR={s['PTR']} MODE={s['MODE']} PI={s['PI']} SI={s['SI']} TI={s['TI']}")
    rows.append(f"ST={s['ST']} DT={s['DT']} SB={s['SB']} DB={s['DB']} "
                f"laisviRB={s['free_blocks']} laisviDisko={s['disk_free']}")
    return "\n".join(rows)


def show_step(osys, r):
    if r.get("done"):
        print(r.get("msg"))
        return
    print(f"[{r['vm']}] {r['instr']}  event={r['event']}")
    b, a = r["before"], r["after"]
    print(f"  pries: AX={b['AX']} BX={b['BX']} PC={b['PC']:02X} ZF={b['ZF']} SF={b['SF']} OF={b['OF']} TI={b['TI']}")
    print(f"  po:    AX={a['AX']} BX={a['BX']} PC={a['PC']:02X} ZF={a['ZF']} SF={a['SF']} OF={a['OF']} TI={a['TI']} MODE={a['MODE']}")
    if r.get("page") is not None and r.get("real") is not None:
        page_line = f"VP{r['page']:X}->RB{r['real']} turinys: {r['page_vals']}"
    else:
        page_line = "VP- (neteisingas adresas)"
    print(f"  sekanti: {r['next']} | {page_line}")
    if r["halted"]:
        vm = osys.vms[r["vm"]]
        print(f"  ** VM {r['vm']} baigta: {vm.error or 'HALT'} **")


def main():
    osys = ControlProgram(seed=42)
    print("Paketines OS emuliatorius: RM + VM (AX/BX, 16x16, 4B zodis). Rasykite help.")
    while True:
        try:
            cmd = input(f"[{osys.active_id or '-'}]> ").strip()
        except EOFError:
            break
        if not cmd:
            continue
        p = cmd.split()
        c = p[0].lower()
        try:
            if c == "quit" or c == "exit":
                break
            elif c == "help":
                print(HELP)
            elif c == "load" and len(p) == 2:
                jobs, errs = osys.load_package_file(p[1])
                for e in errs:
                    print("KLAIDA: " + e)
                for job in jobs:
                    print(osys.create_vm(job.job_id))
            elif c == "create" and len(p) == 2:
                print(osys.create_vm(p[1]))
            elif c == "remove" and len(p) == 2:
                print(osys.remove_vm(p[1]))
            elif c == "list":
                print("Diske:", list(osys.disk_jobs.keys()))
                for vid, vm in osys.vms.items():
                    mark = "*" if vid == osys.active_id else " "
                    print(f"{mark} VM{vid} pages={vm.pages_needed} state={vm.state} AX={vm.ax} PC={vm.pc:02X} {vm.error}")
            elif c == "use" and len(p) == 2:
                print(osys.switch(p[1]))
            elif c == "regs":
                print(fmt_regs(osys))
            elif c == "next":
                vm = osys.active()
                if vm is None:
                    print("Nera aktyvios VM")
                elif vm.state != "READY":
                    print(f"VM {vm.id} nebevykdoma ({vm.error or vm.state})")
                else:
                    print(f"PC={osys.rm.pc:02X} -> {vm.peek_next(osys.rm)}")
            elif c == "step":
                n = int(p[1]) if len(p) > 1 else 1
                for _ in range(n):
                    r = osys.step_once()
                    show_step(osys, r)
                    if r.get("done") or r.get("halted"):
                        break
            elif c == "run":
                for r in osys.run_all():
                    if r.get("done"):
                        print(r.get("msg"))
                        break
                    if r["event"] in ("PD", "GD", "FORK", "FORK_FAIL", "HALT", "PI", "TIMER") or r.get("halted"):
                        show_step(osys, r)
                print("Vykdymas baigtas. Komanda regs / printer / vmmem apziurai.")
            elif c == "vmmem":
                if len(p) == 2:
                    print(osys.vm_memory_text(pages=[int(p[1], 16)]))
                else:
                    print(osys.vm_memory_text())
            elif c == "rmmem" and len(p) == 2:
                print(osys.rm_block_text(int(p[1])))
            elif c == "rmall":
                for b in range(osys.rm.user_blocks):
                    vals = osys.rm.memory[b]
                    if any(str(x) != "0" for x in vals):
                        print(osys.rm_block_text(b))
            elif c == "pt":
                vm = osys.active()
                if vm is None:
                    print("Nera aktyvios VM")
                elif vm.pt_block is not None:
                    print(f"PT blokas {vm.pt_block}: {osys.rm.memory[vm.pt_block]}")
                    print(", ".join(f"VP{p:X}->RB{vm.page_table[p]}" for p in vm.allocated_pages()))
                elif getattr(vm, "final_page_table", None):
                    print("VM resursai atlaisvinti; paskutinis PT:")
                    print(", ".join(f"VP{p:X}->RB{vm.final_page_table[p]}"
                                    for p in sorted(vm.final_pages)))
                else:
                    print("VM neturi puslapiu lenteles")
            elif c == "dev":
                rm = osys.rm
                s = rm.status()
                print(f"Flash blokai: {len(rm.flash)} Printer isvestys: {len(rm.printer)} "
                      f"Disko laisvi: {s['disk_free']}/{s['disk_blocks']} "
                      f"Kanalas: {rm.st}[{rm.sb}]->{rm.dt}[{rm.db}]")
                for e in rm.channel_log[-5:]:
                    print("  " + e)
            elif c == "printer":
                for j in osys.rm.printer:
                    print(f"VM{j['vm']} p{j['page']}: {' '.join(str(x) for x in j['data'])}")
            elif c == "timer" and len(p) == 2:
                osys.rm.timer_quantum = int(p[1])
                osys.rm.ti = int(p[1])
                print(f"Kvantas {p[1]}")
            elif c == "flash_add" and len(p) >= 2:
                rest = cmd[len("flash_add"):].strip()
                vals = []
                for t in rest.split(","):
                    t = t.strip()
                    v = parse_num(t)
                    vals.append(v if v is not None else t[:4])
                while len(vals) < 16:
                    vals.append(0)
                osys.rm.flash.append(vals[:16])
                print(f"Pridetas flash blokas #{len(osys.rm.flash)-1}: {vals[:16]}")
            elif c == "flash_file" and len(p) == 2:
                with open(p[1], "r", encoding="utf-8") as f:
                    text = f.read()
                vals = []
                for t in text.replace(",", " ").split():
                    v = parse_num(t)
                    vals.append(v if v is not None else t[:4])
                while len(vals) < 16:
                    vals.append(0)
                osys.rm.flash.append(vals[:16])
                print(f"Pridetas flash blokas is {p[1]}: {vals[:16]}")
            elif c == "setreg" and len(p) == 3:
                r, v = p[1].upper(), parse_num(p[2])
                if v is None:
                    print("Bloga reiksme")
                    continue
                if r == "AX":
                    osys.rm.ax = v
                elif r == "BX":
                    osys.rm.bx = v
                elif r == "PC":
                    osys.rm.pc = v & 0xFF
                else:
                    print("Galima AX/BX/PC")
                    continue
                print(f"{r}={v}")
            elif c == "setmem" and len(p) == 3:
                vm = osys.active()
                a = int(p[1], 16)
                v = parse_num(p[2])
                if v is None:
                    v = p[2][:4]
                ok = vm.write_virtual(osys.rm, a, v) if vm else False
                print(f"[{a:02X}]={v} {'OK' if ok else 'KLAIDA PI=' + str(osys.rm.pi)}")
            elif c == "log":
                for e in osys.log[-20:]:
                    print(e)
            else:
                print("Nezinoma komanda. Rasykite help.")
        except Exception as e:
            print(f"Klaida: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
