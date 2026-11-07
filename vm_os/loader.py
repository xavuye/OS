from virtual_machine import classify, parse_addr2


class Job:
    def __init__(self):
        self.job_id = ""
        self.pages = 0
        self.data_addr = 0
        self.data_words = []
        self.code_addr = 0
        self.code = []
        self.disk_blocks = []


def parse_package(text):
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l != "" and not l.startswith(";") and not l.startswith("#")]
    jobs = []
    errors = []
    i = 0
    seen_ids = set()
    while i < len(lines):
        if lines[i].upper() != "$JOB":
            errors.append(f"Eilute {i+1}: tiketasi $JOB, rasta '{lines[i]}'")
            i += 1
            continue
        job = Job()
        jerr = []
        i += 1
        if i < len(lines) and lines[i].upper().startswith("ID"):
            parts = lines[i].split()
            if len(parts) == 2 and len(parts[1]) == 4:
                job.job_id = parts[1]
                if job.job_id in seen_ids:
                    jerr.append(f"ID {job.job_id} kartojasi pakete")
                seen_ids.add(job.job_id)
            else:
                jerr.append(f"Blogas ID formatas: '{lines[i]}' (turi buti ID nnnn)")
            i += 1
        else:
            jerr.append("Truksta ID eilutes")
        if i < len(lines) and lines[i].upper().startswith("PAGES"):
            parts = lines[i].split()
            try:
                n = int(parts[1], 10) if len(parts) == 2 else -1
                if isinstance(n, int) and 1 <= n <= 16:
                    job.pages = n
                else:
                    jerr.append(f"PAGES turi buti 1-16, rasta '{lines[i]}'")
            except ValueError:
                jerr.append(f"Blogas PAGES: '{lines[i]}'")
            i += 1
        else:
            jerr.append("Truksta PAGES eilutes")
        if i < len(lines) and lines[i].upper().startswith("DATASEG"):
            parts = lines[i].split()
            a = parse_addr2(parts[1].upper()) if len(parts) == 2 else None
            if a is None:
                jerr.append(f"Blogas DATASEG: '{lines[i]}'")
            else:
                job.data_addr = a
            i += 1
        else:
            jerr.append("Truksta DATASEG")
        while i < len(lines):
            u = lines[i].upper()
            if u.startswith("DW") or u.startswith("DB") or u.startswith("CODESEG") or u == "$END" or u == "$JOB":
                break
            jerr.append(f"Duomenu segmente netiketa eilute: '{lines[i]}'")
            i += 1
        while i < len(lines):
            u = lines[i].upper()
            if u.startswith("CODESEG") or u == "$END" or u == "$JOB":
                break
            if u.startswith("DW"):
                rest = lines[i][2:].strip()
                try:
                    v = int(rest, 10)
                    if -(2 ** 31) <= v <= 2 ** 31 - 1:
                        job.data_words.append(v)
                    else:
                        jerr.append(f"DW uz 32b ribu: '{lines[i]}'")
                except ValueError:
                    jerr.append(f"Blogas DW: '{lines[i]}'")
                i += 1
            elif u.startswith("DB"):
                rest = lines[i][2:].strip()
                if 1 <= len(rest) <= 4:
                    job.data_words.append(rest)
                else:
                    jerr.append(f"DB turi buti 1-4 simboliai: '{lines[i]}'")
                i += 1
            else:
                jerr.append(f"Bloga direktyva: '{lines[i]}'")
                i += 1
        if i < len(lines) and lines[i].upper().startswith("CODESEG"):
            parts = lines[i].split()
            a = parse_addr2(parts[1].upper()) if len(parts) == 2 else None
            if a is None:
                jerr.append(f"Blogas CODESEG: '{lines[i]}'")
            else:
                job.code_addr = a
            i += 1
        else:
            jerr.append("Truksta CODESEG")
        while i < len(lines):
            u = lines[i].upper()
            if u == "$END" or u == "$JOB":
                break
            op, arg = classify(lines[i])
            if op is None:
                jerr.append(f"Netinkamas mnemonikas: '{lines[i]}'")
            else:
                job.code.append(lines[i].strip().upper())
            i += 1
        if i < len(lines) and lines[i].upper() == "$END":
            i += 1
        else:
            jerr.append("Truksta $END")
        data_addrs = set(job.data_addr + k for k in range(len(job.data_words)))
        code_addrs = set(job.code_addr + k for k in range(len(job.code)))
        if data_addrs & code_addrs:
            jerr.append("Duomenu ir kodo sritys kertasi")
        for a in data_addrs | code_addrs:
            if not (0 <= a <= 0xFF):
                jerr.append(f"Adresas uz 00-FF: {a:02X}")
        if job.pages > 0:
            limit = job.pages * 16
            for a in data_addrs | code_addrs:
                if a >= limit:
                    jerr.append(f"Adresas {a:02X} netelpa i PAGES={job.pages} (max {limit-1:02X})")
                    break
        if "HALT" not in [c.strip().upper() for c in job.code]:
            jerr.append("Programoje nera HALT")
        if not job.job_id:
            jerr.append("Nera ID")
        if jerr:
            errors.append(f"Uzduotis {job.job_id or '?'} klaidos: " + "; ".join(jerr))
        else:
            jobs.append(job)
    return jobs, errors
