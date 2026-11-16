"""Mokomojo paketines OS emuliatoriaus (RM/VM) regresiniai testai."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vm_os"))

from control_program import ControlProgram  # noqa: E402
from real_machine import ptr_decode, ptr_encode  # noqa: E402


PROGRAMS = ROOT / "vm_os" / "programs"


def run_sample(filename, *, quantum=30, flash=None):
    system = ControlProgram(seed=42, quantum=quantum)
    if flash is not None:
        system.rm.flash.append(flash)
    jobs, errors = system.load_package_file(PROGRAMS / filename)
    assert jobs and not errors, errors
    list(system.run_all())
    return system


class EmulatorTests(unittest.TestCase):
    def test_expression_and_printer(self):
        system = run_sample("prog1_israisaka.txt")
        self.assertEqual(system.vms["0001"].state, "HALTED")
        self.assertEqual(system.vms["0001"].ax, 1603)
        self.assertEqual(system.rm.printer[0]["data"][4], 1603)
        self.assertEqual(len(system.rm.free_blocks), 64)

    def test_jumps_and_logic(self):
        system = run_sample("prog2_suoliai_logika.txt", quantum=5)
        self.assertFalse(system.vms["0002"].error)
        self.assertEqual(system.rm.printer[0]["data"][:9], [3, 5, 6, 1, 12, 7, 4, 15, 11])

    def test_fork_parent_child_and_flash(self):
        system = run_sample("prog3_fork_io.txt", flash=list(range(1, 17)))
        self.assertEqual(system.vms["0003"].ax, 9001)
        self.assertEqual(system.vms["9001"].ax, 0)
        self.assertEqual(system.vms["9001"].state, "HALTED")
        self.assertEqual({p["vm"] for p in system.rm.printer}, {"0003", "9001"})
        self.assertEqual(next(p for p in system.rm.printer if p["vm"] == "9001")["data"],
                         list(range(1, 17)))

    def test_division_by_zero(self):
        system = run_sample("prog5_pi_demo.txt")
        self.assertIn("PI=5", system.vms["0005"].error)
        self.assertFalse(system.rm.printer)

    def test_sb_sample_updates_data_and_prints_result(self):
        system = run_sample("prog4_sb_memory.txt")
        self.assertEqual(system.printer_output()[0]["data"][:3], [11, 11, 22])

    def test_page_table_and_translation(self):
        system = ControlProgram(seed=42)
        jobs, errors = system.load_package_file(PROGRAMS / "prog1_israisaka.txt")
        self.assertEqual(errors, [])
        vm = system.vms[jobs[0].job_id]
        allocated, max_pages, pt_block = ptr_decode(system.rm.ptr)
        self.assertEqual((allocated, max_pages, pt_block), (2, 16, vm.pt_block))
        table = system.rm.memory[pt_block]
        for p in range(2):
            self.assertEqual(system.rm.translate(p * 16 + 3), (table[p], 3, p))
        self.assertEqual(table[2:], [-1] * 14)
        self.assertNotEqual(table[0], table[1])

    def test_round_robin_timer_saves_registers(self):
        system = ControlProgram(seed=7, quantum=1)
        jobs, errors = system.load_package_file(PROGRAMS / "paketas_2jobs.txt")
        self.assertEqual(errors, [])
        self.assertEqual(system.active_id, "0003")  # Valdymas atiduodamas veliausiai uzkrautai VM.
        first = system.step_once()
        self.assertEqual((first["vm"], first["event"]), ("0003", "FORK"))
        self.assertEqual(first["interrupts"], {"PI": 0, "SI": 3, "TI": 0})
        self.assertEqual(first["after"]["AX"], 9001)
        self.assertEqual(system.active_id, "9001")
        second = system.step_once()
        self.assertEqual((second["vm"], second["event"]), ("9001", "TIMER"))
        self.assertEqual(system.active_id, "0001")
        self.assertEqual(system.vms["0003"].bx, 0)
        executed = [x["vm"] for x in system.run_all() if not x.get("done")]
        self.assertIn("0001", executed)
        self.assertIn("9001", executed)

    def test_disk_queue_admits_jobs_after_memory_release(self):
        package = "\n".join(
            f"$JOB\nID {i:04}\nPAGES 15\nDATASEG 00\nDW {i}\n"
            "CODESEG 10\nLA00\nPD00\nHALT\n$END"
            for i in range(1, 6)
        )
        system = ControlProgram(seed=7, quantum=2)
        jobs, errors = system.load_package_text(package)
        self.assertEqual((len(jobs), errors), (5, []))
        self.assertEqual(len(system.vms), 4)
        self.assertIn("0005", system.disk_jobs)
        self.assertNotIn("0005", system.vms)
        list(system.run_all())
        self.assertEqual(len(system.vms), 5)
        self.assertTrue(all(vm.state == "HALTED" for vm in system.vms.values()))
        self.assertEqual({p["data"][0] for p in system.rm.printer}, {1, 2, 3, 4, 5})
        self.assertEqual(len(system.rm.free_blocks), 64)

    def test_flash_cannot_overwrite_code(self):
        system = ControlProgram(seed=7)
        system.rm.flash.append([7] * 16)
        text = "$JOB\nID 0010\nPAGES 02\nDATASEG 00\nDW 0\nCODESEG 10\nGD10\nHALT\n$END"
        _, errors = system.load_package_text(text)
        self.assertEqual(errors, [])
        result = system.step_once()
        self.assertEqual((result["event"], result["halted"]), ("GD", True))
        self.assertIn("PI=3", system.vms["0010"].error)
        self.assertEqual(len(system.rm.flash), 1)
        self.assertEqual(system.vms["0010"].final_pages[1][0], "GD10")

    def test_invalid_instruction_address_interrupt(self):
        system = ControlProgram(seed=7)
        text = "$JOB\nID 0011\nPAGES 02\nDATASEG 00\nDW 0\nCODESEG 10\nJM00\nHALT\n$END"
        _, errors = system.load_package_text(text)
        self.assertEqual(errors, [])
        result = system.step_once()
        self.assertEqual(result["event"], "PI")
        self.assertIn("PI=1", system.vms["0011"].error)

    def test_invalid_job_does_not_enter_disk(self):
        system = ControlProgram()
        _, errors = system.load_package_text(
            "$JOB\nID 0001\nPAGES 01\nDATASEG 00\nDW 3\n"
            "CODESEG 00\nNOT_AN_OP\nHALT\n$END"
        )
        self.assertTrue(errors)
        self.assertFalse(system.disk_jobs)

    def test_channel_is_supervisor_only(self):
        system = ControlProgram()
        system.load_package_file(PROGRAMS / "prog1_israisaka.txt")
        self.assertEqual(system.rm.mode, "USER")
        with self.assertRaises(PermissionError):
            system.rm.xchg("USER", 0, "PRINTER", 0)

    def test_channel_transfers_a_block_via_supervisor_memory(self):
        rm = ControlProgram(seed=7).rm
        user = rm.alloc_random_blocks(1)[0]
        disk = rm.alloc_disk_blocks(1)[0]
        block = list(range(16))
        rm.flash.append(block)
        rm.channel.configure("FLASH", 0, "SUP", 0)
        rm.channel.xchg()
        rm.xchg("SUP", 0, "DISK", disk)
        rm.xchg("DISK", disk, "USER", user)
        rm.xchg("USER", user, "PRINTER", 0, vm_id="0001")
        rm.memory[user][0] = 99
        self.assertEqual(rm.printer, [{"vm": "0001", "page": 0, "data": block}])
        self.assertEqual(rm.supervisor_memory[0], block)
        self.assertEqual(rm.disk[disk], block)
        rm.mode = "USER"
        with self.assertRaises(PermissionError):
            rm.channel.xchg()

    def test_channel_rejects_invalid_endpoints_without_writing(self):
        rm = ControlProgram(seed=7).rm
        user = rm.alloc_random_blocks(1)[0]
        rm.memory[user] = [3] * 16
        for transfer in (("UNKNOWN", 0, "USER", user), ("SUP", -1, "USER", user),
                         ("DISK", 0, "USER", user), ("USER", user, "FLASH", 0),
                         ("SUP", 0, "USER", rm.free_blocks[0])):
            with self.subTest(transfer=transfer), self.assertRaises(ValueError):
                rm.xchg(*transfer)
        self.assertEqual(rm.memory[user], [3] * 16)
        self.assertFalse(rm.channel.log)

    def test_inactive_vm_memory_access_uses_its_own_ptr(self):
        system = ControlProgram(seed=7)
        system.load_package_file(PROGRAMS / "paketas_2jobs.txt")
        inactive, active = system.vms["0001"], system.active()
        self.assertEqual(system.vm_read("0001", 0), 20)
        self.assertEqual(system.vm_read(active.id, 0), 0)
        self.assertNotEqual(system.vm_translate(inactive, 0)[0], system.rm.translate(0)[0])
        self.assertIsNone(system.vm_read("0001", 0xFF))
        self.assertEqual((system.rm.pi, system.rm.ptr), (0, active.ptr))

    def test_cpu_translates_through_page_table_in_rm_memory(self):
        """Saltinis tiesos - lentele RM atmintyje, i kuria rodo PTR, o ne Python kopija."""
        system = ControlProgram(seed=7)
        system.load_package_text("$JOB\nID 0001\nPAGES 02\nDATASEG 00\nDW 5\n"
                                 "CODESEG 10\nLA00\nLA00\nHALT\n$END")
        vm = system.active()
        old = system.rm.memory[vm.pt_block][0]
        new = system.rm.alloc_random_blocks(1)[0]
        system.rm.memory[new][0] = 777
        system.rm.memory[vm.pt_block][0] = new      # OS perkelia puslapi 0 i kita bloka
        self.assertEqual(system.step_once()["after"]["AX"], 777)
        system.rm.memory[vm.pt_block][0] = 64       # irasas uz RM ribu
        result = system.step_once()
        self.assertEqual((result["event"], result["interrupts"]["PI"]), ("PI", 1))
        self.assertNotEqual(old, new)

    def test_real_machine_has_no_link_to_os_objects(self):
        rm = ControlProgram().rm
        self.assertFalse([name for name in vars(rm) if "vm" in name.lower()])

    def test_invalid_vm_selection_does_not_execute_current_vm(self):
        system = ControlProgram(seed=7)
        system.load_package_file(PROGRAMS / "prog1_israisaka.txt")
        before = system.rm.status()
        self.assertTrue(system.step_once("nera")["done"])
        self.assertEqual(system.rm.status(), before)

    def test_fork_copies_resume_context_and_isolates_both_address_spaces(self):
        system = ControlProgram(seed=7)
        package = "$JOB\nID 0001\nPAGES 02\nDATASEG 00\nDW -5\nDW 3\nCODESEG 10\nLA00\nLB01\nCMP\nFORK\nHALT\n$END"
        system.load_package_text(package)
        for _ in range(4):
            result = system.step_once()
        self.assertEqual(result["event"], "FORK")
        parent, child = system.active(), system.vms["9001"]
        self.assertEqual((system.rm.ax, child.ax, child.pc, child.bx), (9001, 0, 0x14, 3))
        self.assertEqual((child.zf, child.sf, child.of), (False, True, False))
        self.assertTrue(set(parent.owned_blocks).isdisjoint(child.owned_blocks))
        self.assertEqual(system.rm.memory[child.pt_block][:2], system.page_table(child))
        self.assertEqual(ptr_decode(child.ptr), (2, 16, child.pt_block))
        real, offset, _ = system.vm_translate(child, 0)
        system.rm.memory[real][offset] = 73
        self.assertEqual(system.vm_read(parent.id, 0), -5)
        self.assertEqual(system.vm_read(child.id, 0), 73)

    def test_fork_without_memory_returns_minus_one_and_reschedules(self):
        package = "\n".join(f"$JOB\nID {i:04}\nPAGES 15\nDATASEG 00\nDW {i}\n"
                            "CODESEG 10\nFORK\nHALT\n$END" for i in range(1, 5))
        system = ControlProgram(seed=7, quantum=1)
        system.load_package_text(package)
        result = system.step_once()
        self.assertEqual((result["event"], result["after"]["AX"]), ("FORK_FAIL", -1))
        self.assertEqual(result["interrupts"], {"PI": 0, "SI": 3, "TI": 0})
        self.assertFalse(result["halted"])
        self.assertEqual(len(system.vms), 4)
        self.assertFalse(system.rm.free_blocks)
        self.assertEqual(system.active_id, "0001")

    def test_io_at_quantum_expiry_still_switches_vm(self):
        package = "\n".join(f"$JOB\nID {i:04}\nPAGES 02\nDATASEG 00\nDW {i}\n"
                            "CODESEG 10\nPD00\nHALT\n$END" for i in range(1, 3))
        system = ControlProgram(seed=7, quantum=2)
        system.load_package_text(package)
        result = system.step_once()
        self.assertEqual((result["vm"], result["event"]), ("0002", "PD"))
        self.assertEqual(result["interrupts"], {"PI": 0, "SI": 2, "TI": -1})
        self.assertEqual(system.rm.printer[0]["data"][0], 2)
        self.assertEqual((system.active_id, system.rm.ti), ("0001", 2))

    def test_run_continues_past_the_former_limit_and_reaches_halt(self):
        package = ("$JOB\nID 0001\nPAGES 02\nDATASEG 00\nDW 5500\nDW 1\n"
                   "CODESEG 10\nLA00\nLB01\nSUB\nJG12\nHALT\n$END")
        system = ControlProgram()
        system.load_package_text(package)
        steps = list(system.run_all())
        self.assertEqual(len(steps) - 1, 11003)
        self.assertEqual(steps[-2]["event"], "HALT")
        self.assertEqual(system.vms["0001"].ax, 0)

    def test_ptr_encodes_page_table_block_and_validates_fields(self):
        self.assertEqual(ptr_encode(3, 16, 5), 0x03100005)
        self.assertEqual(ptr_decode(0x1010003F), (16, 16, 63))
        for fields in ((17, 16, 0), (-1, 16, 0), (2, 17, 0), (2, 16, 0x10000), (2, 16, "5")):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                ptr_encode(*fields)

    def test_sixteen_page_vm_reaches_last_word_through_noncontiguous_mapping(self):
        system = ControlProgram(seed=7)
        package = "$JOB\nID 0001\nPAGES 16\nDATASEG 00\nDW 7\nCODESEG FE\nLA00\nHALT\n$END"
        system.load_package_text(package)
        vm = system.active()
        self.assertEqual(ptr_decode(system.rm.ptr), (16, 16, vm.pt_block))
        table = system.page_table(vm)
        self.assertGreater(max(table) - min(table), 15)
        self.assertEqual(system.vm_read(vm.id, 0xFF), "HALT")
        self.assertIsNone(system.rm.translate(0x100))
        self.assertEqual(system.step_once()["after"]["AX"], 7)
        self.assertEqual(system.step_once()["event"], "HALT")
        self.assertEqual(len(system.rm.free_blocks), 64)

    def test_cpu_refuses_to_run_with_foreign_ptr(self):
        """PTR nustato tik OS; neatitikimas yra OS klaida, o ne naudotojo programos."""
        system = ControlProgram(seed=7)
        system.load_package_file(PROGRAMS / "paketas_2jobs.txt")
        inactive = system.vms["0001"]
        system.rm.ptr = inactive.ptr
        with self.assertRaises(RuntimeError):
            system.step_once()
        self.assertEqual(system.vm_read("0001", 0), 20)

    def test_failed_io_and_timer_leave_no_pending_interrupt_for_next_vm(self):
        system = ControlProgram(seed=7, quantum=1)
        system.load_package_file(PROGRAMS / "prog1_israisaka.txt")
        system.add_flash_block([1] * 16)
        package = "$JOB\nID 0002\nPAGES 02\nDATASEG 00\nDW 5\nCODESEG 10\nGD10\nHALT\n$END"
        system.load_package_text(package)
        result = system.step_once()
        self.assertEqual(result["interrupts"], {"PI": 3, "SI": 1, "TI": -2})
        self.assertTrue(result["halted"])
        self.assertEqual((system.active_id, system.rm.pi, system.rm.si, system.rm.mode), ("0001", 0, 0, "USER"))
        self.assertEqual(system.step_once()["instr"], "LA00")

    def test_failed_reload_keeps_completed_snapshot_and_rolls_back_allocation(self):
        system = run_sample("prog1_israisaka.txt")
        old = system.vms["0001"]
        free = system.rm.free_blocks.copy()
        broken = system.disk_jobs["0001"].disk_blocks[1]
        system.rm.disk[broken] = None
        self.assertIn("Nepavyko", system.create_vm("0001"))
        self.assertIs(system.vms["0001"], old)
        self.assertEqual(old.final_pages[0][4], 1603)
        self.assertEqual(system.rm.free_blocks, free)
        self.assertEqual(system.rm.mode, "SUPERVISOR")

    def test_debugger_validates_edits_and_keeps_interrupts_clear(self):
        system = ControlProgram()
        with self.assertRaises(ValueError):
            system.debug_set_register("AX", 7)
        system.load_package_file(PROGRAMS / "prog1_israisaka.txt")
        before = system.view_status()
        for name, value in (("PC", 256), ("AX", 2 ** 31), ("PTR", 0)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                system.debug_set_register(name, value)
        for address, value in ((0x10, 2), (0xFF, 2), (0, 2 ** 31)):
            with self.subTest(address=address), self.assertRaises(ValueError):
                system.debug_set_memory(address, value)
        self.assertEqual(system.view_status(), before)
        system.debug_set_register("BX", -13)
        system.debug_set_memory(0, 71)
        result = system.step_once()
        self.assertEqual((result["after"]["AX"], result["after"]["BX"], result["event"]), (71, -13, "OK"))

    def test_loader_rejects_directive_prefix_and_signed_address(self):
        package = (PROGRAMS / "prog1_israisaka.txt").read_text(encoding="utf-8")
        for text in (package.replace("ID ", "IDENTIFIER "),
                     package.replace("LA00", "LA+0")):
            with self.subTest(text=text):
                system = ControlProgram()
                jobs, errors = system.load_package_text(text)
                self.assertFalse(jobs)
                self.assertTrue(errors)
                self.assertFalse(system.disk_jobs)


class SupervisorEntryTests(unittest.TestCase):
    JOB = "$JOB\nID {id}\nPAGES 02\nDATASEG 00\nDW 4\nCODESEG 10\nLA00\nLB00\nADD\nHALT\n$END"

    def test_os_calls_from_ui_run_in_supervisor_mode(self):
        system = ControlProgram(seed=1)
        system.load_package_text(self.JOB.format(id="0001"))
        seen = []
        real_translate = system.vm_translate

        def spy(vm, address):
            seen.append(system.rm.mode)
            return real_translate(vm, address)

        system.vm_translate = spy
        system.debug_set_memory(0, 9)
        self.assertEqual(seen, ["SUPERVISOR"])
        self.assertEqual(system.rm.mode, "USER")

    def test_mode_after_os_call_is_user_only_with_loaded_context(self):
        system = ControlProgram(seed=1)
        self.assertEqual((system.rm.mode, system.cpu_vm), ("SUPERVISOR", None))
        system.load_package_text(self.JOB.format(id="0001"))
        self.assertEqual(system.rm.mode, "USER")
        list(system.run_all())
        self.assertEqual((system.rm.mode, system.cpu_vm), ("SUPERVISOR", None))

    def test_nested_os_work_during_running_vm_keeps_supervisor_until_exit(self):
        """Paketo ikelimas -> irasymas i diska -> VM kurimas -> perjungimas: visi XCHG SUPERVISOR rezimu."""
        system = ControlProgram(seed=1, quantum=10)
        system.load_package_text(self.JOB.format(id="0001"))
        system.step_once()
        modes = []
        real_xchg = system.rm.xchg

        def spy(*args, **kwargs):
            modes.append(system.rm.mode)
            return real_xchg(*args, **kwargs)

        system.rm.xchg = spy
        system.load_package_text(self.JOB.format(id="0002"))
        self.assertTrue(modes)
        self.assertEqual(set(modes), {"SUPERVISOR"})
        self.assertEqual((system.active_id, system.rm.mode), ("0002", "USER"))
        self.assertEqual(system.vms["0001"].ax, 4)

    def test_interrupt_handler_reports_mode_left_for_cpu(self):
        system = ControlProgram(seed=1)
        system.load_package_text(self.JOB.format(id="0001"))
        results = [r for r in system.run_all() if not r["done"]]
        self.assertEqual(results[-1]["event"], "HALT")
        self.assertEqual(results[-1]["after"]["MODE"], "SUPERVISOR")
        self.assertEqual(results[-1]["after"]["AX"], 8)


class LifecycleTests(unittest.TestCase):
    @staticmethod
    def job(job_id, code, data="DW 1"):
        return (f"$JOB\nID {job_id}\nPAGES 02\nDATASEG 00\n{data}\nCODESEG 10\n"
                + "\n".join(code) + "\n$END")

    def test_kill_terminates_endless_vm_and_frees_memory(self):
        system = ControlProgram(seed=2, quantum=3)
        system.load_package_text(self.job("0001", ["JM10", "HALT"]))
        system.load_package_text(self.job("0002", ["LA00", "HALT"]))
        for _ in range(10):
            system.step_once()
        self.assertEqual(system.vms["0001"].state, "READY")
        self.assertIn("nutraukta", system.kill_vm("0001"))
        self.assertEqual(system.vms["0001"].state, "HALTED")
        self.assertIn("kill", system.vms["0001"].error)
        list(system.run_all())
        self.assertEqual(len(system.rm.free_blocks), 64)
        self.assertEqual((system.rm.mode, system.rm.pi, system.rm.si), ("SUPERVISOR", 0, 0))
        self.assertIn("jau baigta", system.kill_vm("0001"))
        self.assertIn("Pasalinta", system.remove_vm("0001"))

    def test_kill_of_running_vm_hands_cpu_to_next_ready(self):
        system = ControlProgram(seed=2)
        system.load_package_text(self.job("0001", ["LA00", "HALT"]))
        system.load_package_text(self.job("0002", ["JM10", "HALT"]))
        self.assertEqual(system.active_id, "0002")
        system.kill_vm("0002")
        self.assertEqual((system.active_id, system.cpu_vm.id, system.rm.mode), ("0001", "0001", "USER"))
        self.assertEqual(system.rm.ptr, system.vms["0001"].ptr)

    def test_fork_ids_stay_four_digits_and_are_reused(self):
        system = ControlProgram(seed=2)
        system.next_fork_num = 9998
        system.load_package_text(self.job("0001", ["FORK", "FORK", "HALT"]))
        system.step_once()
        system.step_once()
        self.assertEqual(sorted(system.vms), ["0001", "9001", "9999"])
        self.assertEqual(system.rm.ax, 9001)
        self.assertTrue(all(len(vm_id) == 4 for vm_id in system.vms))

    def test_fork_without_free_id_returns_minus_one(self):
        system = ControlProgram(seed=2)
        system.load_package_text(self.job("0001", ["FORK", "HALT"]))
        system._free_fork_id = lambda: None
        result = system.step_once()
        self.assertEqual((result["event"], result["after"]["AX"], result["halted"]), ("FORK_FAIL", -1, False))
        self.assertEqual(list(system.vms), ["0001"])

    def test_gd_with_empty_flash_returns_minus_one_and_continues(self):
        system = ControlProgram(seed=2)
        system.load_package_text(self.job("0001", ["LA00", "GD00", "SA01", "HALT"], data="DW 7"))
        system.step_once()
        result = system.step_once()
        self.assertEqual((result["event"], result["halted"], result["after"]["AX"]), ("GD_FAIL", False, -1))
        self.assertEqual(result["interrupts"], {"PI": 0, "SI": 1, "TI": 26})
        self.assertEqual(system.vm_read("0001", 0), 7)      # puslapis nepakeistas
        list(system.run_all())
        self.assertEqual(system.vms["0001"].final_pages[0][1], -1)
        self.assertFalse(system.vms["0001"].error)

    def test_loader_accepts_only_plain_decimal_numbers(self):
        base = self.job("0001", ["HALT"])
        for bad in (base.replace("PAGES 02", "PAGES +2"), base.replace("PAGES 02", "PAGES 0x2"),
                    base.replace("DW 1", "DW 1_000"), base.replace("DW 1", "DW ²"),
                    base.replace("DW 1", "DW --1")):
            with self.subTest(bad=bad.splitlines()[2:5]):
                jobs, errors = ControlProgram().load_package_text(bad)
                self.assertFalse(jobs)
                self.assertTrue(errors)
        for good, value in (("DW -5", -5), ("DW +5", 5), ("DW -2147483648", -2 ** 31)):
            system = ControlProgram()
            jobs, errors = system.load_package_text(base.replace("DW 1", good))
            self.assertEqual(errors, [])
            self.assertEqual(jobs[0].data_words, [value])


class CommandTableAndSamplesTests(unittest.TestCase):
    def test_command_table_matches_instruction_set(self):
        from virtual_machine import COMMANDS, classify
        with_address = {"LA", "LB", "SA", "SB", "JM", "JE", "JG", "JL", "GD", "PD"}
        recognised = {classify(name + ("10" if name in with_address else ""))[0] for name in COMMANDS}
        self.assertEqual(recognised, set(COMMANDS))
        self.assertEqual({op for op, (_, ticks) in COMMANDS.items() if ticks == 3}, {"GD", "PD"})
        self.assertEqual(classify("PUSH"), (None, None))
        self.assertEqual(classify("LOOP"), (None, None))

    def factorial(self, n):
        text = (PROGRAMS / "prog6_faktorialas.txt").read_text(encoding="utf-8").replace("DW 6", f"DW {n}")
        system = ControlProgram(seed=4)
        _, errors = system.load_package_text(text)
        self.assertEqual(errors, [])
        list(system.run_all())
        return system

    def test_factorial_sample_loops_until_counter_reaches_one(self):
        system = self.factorial(6)
        self.assertEqual(system.printer_output()[0]["data"][:3], [1, 720, 1])
        self.assertFalse(system.vms["0006"].error)
        self.assertEqual(self.factorial(12).printer_output()[0]["data"][1], 479001600)

    def test_factorial_overflow_raises_pi4(self):
        system = self.factorial(13)
        self.assertIn("PI=4", system.vms["0006"].error)
        self.assertFalse(system.printer_output())
        self.assertEqual(len(system.rm.free_blocks), 64)

    def test_memory_rows_come_from_os_translation(self):
        system = ControlProgram(seed=4)
        system.load_package_file(PROGRAMS / "paketas_2jobs.txt")
        vm = system.active()
        title, rows = system.vm_memory_rows()
        self.assertIn(vm.id, title)
        self.assertEqual([real for _, real, _ in rows], system.page_table(vm))
        for page, real, words in rows:
            self.assertEqual(words, system.rm.memory[real])
        owners = {block: owner for block, owner, _ in system.rm_memory_rows()}
        self.assertEqual(len(owners), 64)
        self.assertEqual(owners[vm.pt_block], f"PT {vm.id}")
        self.assertTrue(all(owners[b] == f"VM {vm.id}" for b in system.page_table(vm)))
        list(system.run_all())
        title, rows = system.vm_memory_rows("0001")
        self.assertIn("paskutine kopija", title)
        self.assertTrue(rows)
        self.assertEqual({owner for _, owner, _ in system.rm_memory_rows()}, {"-"})


class AdditionalAuditTests(unittest.TestCase):
    def test_scheduler_preserves_allocation_invariants(self):
        for seed in range(20):
            for quantum in (1, 2, 3, 30):
                system = ControlProgram(seed=seed, quantum=quantum)
                system.add_flash_block(range(16))
                system.load_package_file(PROGRAMS / "paketas_2jobs.txt")
                for result in system.run_all():
                    used = []
                    for vm in system.vms.values():
                        if vm.state == "READY":
                            used.extend(vm.owned_blocks)
                            self.assertEqual(sorted([vm.pt_block] + system.page_table(vm)),
                                             sorted(vm.owned_blocks))
                        else:
                            self.assertIsNone(vm.pt_block)
                    self.assertEqual(len(used), len(set(used)))
                    self.assertEqual(set(used) | set(system.rm.free_blocks), set(range(64)))
                    self.assertFalse(set(used) & set(system.rm.free_blocks))
                    self.assertEqual((system.rm.pi, system.rm.si), (0, 0))
                    if system.cpu_vm:
                        self.assertIs(system.active(), system.cpu_vm)
                        self.assertEqual(system.rm.ptr, system.active().ptr)
                self.assertEqual(len(system.rm.free_blocks), 64)
                self.assertEqual(len(system.printer_output()), 3)

    def test_fork_transfer_failure_releases_partial_child(self):
        from unittest.mock import patch
        system = ControlProgram()
        system.load_package_file(PROGRAMS / "paketas_2jobs.txt")
        free_before = len(system.rm.free_blocks)
        parent_size = system.active().pages_needed + 1
        real_xchg = system.rm.xchg
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("dirbtine perdavimo klaida")
            return real_xchg(*args, **kwargs)
        with patch.object(system.rm, "xchg", side_effect=fail_second):
            result = system.step_once()
        self.assertTrue(result["halted"])
        self.assertEqual(result["interrupts"]["PI"], 3)
        self.assertNotIn("9001", system.vms)
        self.assertEqual(len(system.rm.free_blocks), free_before + parent_size)
        self.assertEqual(system.active_id, "0001")
        self.assertEqual(system.step_once()["instr"], "LA00")

    def test_os_reads_io_operand_from_trapping_instruction(self):
        system = ControlProgram(seed=3)
        system.load_package_text("$JOB\nID 0001\nPAGES 03\nDATASEG 20\nDW 42\n"
                                 "CODESEG 00\nPD20\nHALT\n$END")
        result = system.step_once()
        self.assertEqual((result["event"], result["after"]["PC"]), ("PD", 1))
        self.assertEqual(system.printer_output()[0]["page"], 2)
        self.assertEqual(system.printer_output()[0]["data"][0], 42)

    def test_invalid_opcode_at_last_address_keeps_full_pc(self):
        system = ControlProgram()
        system.load_package_text("$JOB\nID 0001\nPAGES 16\nDATASEG 00\nDW 1\nCODESEG FF\nHALT\n$END")
        vm = system.active()
        system.rm.memory[system.page_table(vm)[15]][15] = "BAD"
        result = system.step_once()
        self.assertEqual(result["interrupts"]["PI"], 2)
        self.assertEqual(result["after"]["PC"], 256)


if __name__ == "__main__":
    unittest.main()
