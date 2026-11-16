import sys
import os
import signal
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from control_program import ControlProgram


HELP = """Komandos:
 load <failas>        nuskaityti paketa (flash) ir irasyti uzduotis i diska
 create <ID>          sukurti VM is diske esancios uzduoties
 remove <ID>          pasalinti baigta VM/uzduoti ir atlaisvinti resursus
 kill <ID>            nutraukti vykdoma VM (pvz., amzina cikla)
 list                 disko uzduotys / VM sarasas
 use <ID>             perjungti aktyvia VM (valdymas veliausiai uzkrautajai)
 regs                 registrai + PI/SI/TI/MODE/PTR
 next                 sekanti vykdoma komanda
 step [n]             vykdyti n zingsniu (po 1)
 run                  vykdyti paketa iki pabaigos (Ctrl+C pristabdo)
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


def parse_word(token):
    value = parse_num(token)
    return token if value is None else value


def fmt_regs(osys):
    s = osys.view_status()
    head = f"Aktyvi VM: {s['vm'] or '-'} state={s['state'] or '-'}"
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
    intr = r["interrupts"]
    if intr["PI"] or intr["SI"] or intr["TI"] <= 0:
        print(f"  pertraukimas: PI={intr['PI']} SI={intr['SI']} TI={intr['TI']}")
    if r.get("page") is not None and r.get("real") is not None:
        page_line = f"VP{r['page']:X}->RB{r['real']} turinys: {r['page_vals']}"
    else:
        page_line = "VP- (neteisingas adresas)"
    print(f"  sekanti: {r['next']} | {page_line}")
    status = osys.view_status()
    print(f"  irenginiai: flash={status['flash_blocks']} printer={status['printer_jobs']} "
          f"disk={status['disk_free']}/{status['disk_blocks']} laisvi "
          f"kanalas={status['ST']}[{status['SB']}]->{status['DT']}[{status['DB']}]")
    if r["halted"]:
        print(f"  ** VM {r['vm']} baigta: {r['error'] or 'HALT'} **")


def run_package(osys):
    paused = False

    def request_pause(_signum, _frame):
        nonlocal paused
        paused = True

    previous = signal.signal(signal.SIGINT, request_pause)
    try:
        for result in osys.run_all():
            if result["done"]:
                print(result["msg"])
                return
            if result["event"] != "OK":
                show_step(osys, result)
            if paused:
                print("Vykdymas pristabdytas.")
                return
    finally:
        signal.signal(signal.SIGINT, previous)


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
                    state = "VM sukurta" if job.job_id in osys.job_status()["vms"] else "laukia RM atminties"
                    print(f"Uzduotis {job.job_id} diske: {state}")
            elif c == "create" and len(p) == 2:
                print(osys.create_vm(p[1]))
            elif c == "remove" and len(p) == 2:
                print(osys.remove_vm(p[1]))
            elif c == "kill" and len(p) == 2:
                print(osys.kill_vm(p[1]))
            elif c == "list":
                jobs = osys.job_status()
                print("Diske:", list(jobs["disk"]))
                print("Laukia atminties:", list(jobs["waiting"]))
                for vid, vm in jobs["vms"].items():
                    mark = "*" if vid == osys.active_id else " "
                    print(f"{mark} VM{vid} pages={vm['pages']} state={vm['state']} "
                          f"AX={vm['AX']} PC={vm['PC']:02X} {vm['error']}")
            elif c == "use" and len(p) == 2:
                print(osys.switch(p[1]))
            elif c == "regs":
                print(fmt_regs(osys))
            elif c == "next":
                status = osys.view_status()
                print(f"PC={status['PC']:02X} -> {status['next']}" if status["vm"] else "Nera aktyvios VM")
            elif c == "step":
                n = int(p[1]) if len(p) > 1 else 1
                for _ in range(n):
                    r = osys.step_once()
                    show_step(osys, r)
                    if r.get("done") or r.get("halted"):
                        break
            elif c == "run":
                run_package(osys)
            elif c == "vmmem":
                if len(p) == 2:
                    print(osys.vm_memory_text(pages=[int(p[1], 16)]))
                else:
                    print(osys.vm_memory_text())
            elif c == "rmmem" and len(p) == 2:
                print(osys.rm_block_text(int(p[1])))
            elif c == "rmall":
                print(osys.rm_memory_text())
            elif c == "pt":
                print(osys.page_table_text())
            elif c == "dev":
                s = osys.view_status()
                print(f"Flash blokai: {s['flash_blocks']} Printer isvestys: {s['printer_jobs']} "
                      f"Disko laisvi: {s['disk_free']}/{s['disk_blocks']} "
                      f"Kanalas: {s['ST']}[{s['SB']}]->{s['DT']}[{s['DB']}]")
                for entry in osys.channel_entries():
                    print("  " + entry)
            elif c == "printer":
                for j in osys.printer_output():
                    print(f"VM{j['vm']} p{j['page']}: {' '.join(str(x) for x in j['data'])}")
            elif c == "timer" and len(p) == 2:
                value = int(p[1])
                osys.set_timer(value)
                print(f"Kvantas {value}")
            elif c == "flash_add" and len(p) >= 2:
                rest = cmd[len("flash_add"):].strip()
                number = osys.add_flash_block(parse_word(token.strip()) for token in rest.split(","))
                print(f"Pridetas flash blokas #{number}")
            elif c == "flash_file" and len(p) == 2:
                with open(p[1], "r", encoding="utf-8") as f:
                    words = f.read().replace(",", " ").split()
                number = osys.add_flash_block(parse_word(token) for token in words)
                print(f"Pridetas flash blokas #{number} is {p[1]}")
            elif c == "setreg" and len(p) == 3:
                name, value = p[1].upper(), parse_num(p[2])
                osys.debug_set_register(name, value)
                print(f"{name}={value}")
            elif c == "setmem" and len(p) == 3:
                address, value = int(p[1], 16), parse_word(p[2])
                osys.debug_set_memory(address, value)
                print(f"[{address:02X}]={value} OK")
            elif c == "log":
                for entry in osys.journal_entries():
                    print(entry)
            else:
                print("Nezinoma komanda. Rasykite help.")
        except (ValueError, OSError) as e:
            print(f"Klaida: {e}")


if __name__ == "__main__":
    main()
