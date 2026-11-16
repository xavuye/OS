# 2 laboratorinis darbas – atsiskaitymo atmintinė

„Virtualios ir realios mašinos realizacija“, 5 variantas (paketinė OS).
Visi skaičiai žemiau gauti tikrai paleidus šią versiją. GUI ir CLI paleidžiami su `seed=42`, todėl, atliekant veiksmus ta pačia tvarka, blokų numeriai sutaps.

## 1. Paleidimas

| Kas | Komanda |
|---|---|
| Grafinė sąsaja (pagrindinė) | `python vm_os/gui.py` arba ▶ VS Code lange, atidarius `gui.py` |
| Komandinė sąsaja | `python vm_os/cli.py`, po to `help` |
| Testai | `python -B -m unittest discover -s tests -v` |

Išorinių bibliotekų nereikia: pakanka Python 3 ir standartinio `tkinter`.

### GUI mygtukai
| Mygtukas | Ką daro |
|---|---|
| **Load paketas** | „Prijungti flash“: pasirinkti paketo failą iš `vm_os/programs` |
| **Step** | Įvykdyti vieną komandą (žingsninis režimas) |
| **Run / Pause** | Vykdyti iki pabaigos be žingsnių skaičiaus ribos; paspaudus dar kartą – pauzė |
| **Flash+** | Pridėti duomenų bloką į flash srautą (komandai GD) |
| VM laukas + **Use** | Atiduoti procesorių pasirinktai VM |
| ID laukas + **Create / Remove / Kill** | Sukurti VM iš užduoties diske, pašalinti baigtą, nutraukti vykdomą (pvz., amžiną ciklą) |
| **Set AX / Set BX / Set MEM** | Pakeisti registrą ar atminties žodį vykdymo metu |
| **Printer** | Parodyti spausdintuvo išvestį |
| Skirtukas **RM atmintis** + **Rodyti RB** | Parodyti fizinį RM bloką |

### Spalvos atminties lentelėse
- **geltona** – pasikeitė po paskutinio veiksmo, `*` – pakitęs žodis;
- **mėlyna** – puslapis ar blokas, kuriame įvykdyta paskutinė komanda;
- **žalia** – aktyvios VM blokai;
- **pilka** – puslapių lentelės.

---

## 2. Kaip tai veikia (pasakojimas 2–3 minutėms)

### Trys sluoksniai

```
 ┌──────────── GUI / CLI ─────────────┐   tik kviečia OS metodus
 └───────────────┬────────────────────┘
 ┌───────────────▼────────────────────┐
 │ ControlProgram (OS)                │   control_program.py
 │  paketo įkėlimas, VM kūrimas,      │
 │  handle_interrupt, planuoklis,     │
 │  FORK, GD/PD per kanalą            │
 └───────┬───────────────────┬────────┘
 ┌───────▼────────┐   ┌──────▼─────────────────────────┐
 │ VirtualMachine │   │ RealMachine („aparatūra“)      │
 │ antraštė (ID), │   │ CPU: AX BX PC FLAGS PTR MODE   │
 │ išsaugotas     │   │      PI SI TI                  │
 │ kontekstas,    │   │ MMU: translate() pagal PTR     │
 │ komandų        │   │ atmintis 64 blokai × 16 žodžių │
 │ interpretat.   │   │ diskas 128 blokai, flash,      │
 └────────────────┘   │ spausdintuvas                  │
                      │ ChannelDevice: ST DT SB DB XCHG│
                      └────────────────────────────────┘
```

Ką sakyti:
- **RM** yra „aparatūra“. Ji nieko nežino apie OS procesus: turi tik registrus, atmintį, MMU ir kanalą.
- **VM** yra tai, ką mato naudotojo programa: AX, BX, PC, FLAGS ir 256 atminties žodžius (16 puslapių × 16 žodžių, žodis – 4 baitai). `VirtualMachine` objektas – tai antraštė (ID) ir išsaugotas procesoriaus kontekstas. Komandas vykdo realus procesorius USER režimu (ataskaitos §2.7).
- **OS** (`ControlProgram`) yra tarpininkas. Tik ji dirba SUPERVISOR režimu: skirsto atmintį, nustato PTR, aptarnauja pertraukimus ir valdo įrenginius per kanalą.

### Užduoties kelias (ataskaitos §3)
1. **Flash → tikrinimas.** Paketą iš tekstinio failo (flash imitacija) analizuoja `loader.parse_package`. Tikrinama: ID iš 4 skaitmenų ir nesikartoja, PAGES 1–16, yra DATASEG/CODESEG/$END/HALT, mnemonikos teisingos, adresai telpa, duomenys ir kodas nesikerta.
2. **→ Diskas.** Teisinga užduotis įrašoma į diską kanalu `SUP → DISK` (`store_job_on_disk`).
3. **→ Atmintis.** Jei laisvos RAM pakanka, `create_vm` skiria **atsitiktinius** blokus: PAGES puslapiams ir 1 puslapių lentelei. Tada įrašo lentelę, perkelia puslapius kanalu `DISK → USER`, nustato PTR. Jei atminties nepakanka, užduotis laukia diske („laukia RM atminties“) ir paleidžiama pati, kai atmintis atsilaisvina (`admit_waiting_jobs`).
4. **Valdymas.** Jį gauna **vėliausiai užkrauta** VM.
5. **Vykdymas.** VM dirba USER režimu. GD, PD, FORK, HALT, klaidos ir kvanto pabaiga perduoda valdymą OS per `handle_interrupt`.
6. **Pabaiga.** Po HALT ar klaidos OS atlaisvina blokus ir išsaugo atminties kopiją peržiūrai.

### Adreso vertimas (ataskaitos §1.4) – pavyzdys su tikrais skaičiais
Įkeliame `prog1_israisaka.txt` arba `prog6_faktorialas.txt`:
```
PTR = 0210000E    a0=02 (2 puslapiai)  a1=10h (daugiausia 16)  a2a3=000E (lentelė bloke 14)
Lentelė RB14:     [3, 35, -1, -1, ...]    → VP0→RB3, VP1→RB35 (ne iš eilės, atsitiktinai)
```
- Virtualus adresas `01h` = puslapis 0, poslinkis 1 → PT[0]=3 → fizinis adresas 16·3+1 = **49**.
- Virtualus adresas `10h` (pirmoji komanda) = puslapis 1, poslinkis 0 → PT[1]=35 → 16·35 = **560**.
- Adresas `20h` = puslapis 2, bet a0=2 → tokio puslapio nėra → **PI=1**.

Puslapių lentelė yra atskirame bloke, kuris nepatenka į VM adresų erdvę. Todėl programa negali jos nei perskaityti, nei pakeisti.

### Procesoriaus žingsnis ir pertraukimai (ataskaitos §1.5)
```
VirtualMachine.step()            USER: komanda paimama pagal PC per MMU,
                                 vykdoma funkcija iš COMMANDS,
                                 PC+1, TI -= taktai (įprasta 1, GD/PD – 3)
        │  nustato PI (klaida) arba SI (GD=1, PD=2, FORK=3, HALT=4)
        ▼
ControlProgram.handle_interrupt()   PI, SI, TI tikrinami po KIEKVIENOS komandos
   priežasčių nėra → grįžti, VM tęsia
   yra            → įėjimas į SUPERVISOR:
     1) nustatyti priežastį ir aptarnauti SI (GD/PD per kanalą, FORK)
     2) nuspręsti: VM baigiama (PI arba HALT) ar tęsia
     3) išvalyti PI/SI, jei TI≤0 – atstatyti taimerį
     4) planavimas: baigtą VM atlaisvinti, baigus kvantą → kita parengta VM
   išėjimas: MODE=USER, jei procesoriuje yra VM
```

---

## 3. Demonstracija – žingsnis po žingsnio

### 1 demonstracija. Žingsninis režimas, registrai, atmintis (GUI) – svarbiausia
1. `python vm_os/gui.py` → **Load paketas** → `prog6_faktorialas.txt` (N!, N=6).
2. Parodyti kairįjį skydelį:
   - `PTR=0210000E (a0=2 a1=16 a2a3=PT blokas 14 ...)`, `MODE=USER`, `TI=30`;
   - `PT RB14: VP0->RB3, VP1->RB35` – puslapiai ne iš eilės;
   - `Sekanti: LA01` – kita komanda.
3. Skirtukas **VM atmintis**: VP0 – duomenys `6 1 1`, VP1 – kodas.
4. Paspausti **Step** 4 kartus (LA01, LB00, MUL, SA01). Po SA01:
   - registrai `AX=6 BX=6 PC=14`;
   - lentelėje VP0 tapo geltonas, 1 žodis = `6*` – pasikeitė;
   - VP1 mėlynas – ten buvo vykdoma komanda.
5. Skirtukas **RM atmintis** → įvesti `14` → **Rodyti RB**: blokas `PT 0006` turi `3 35 -1 -1 …`. Tai ir yra puslapių lentelė, į kurią rodo PTR.
6. **Run** → programa pasiekia HALT. **Printer**: `VM0006 p0: 1 720 1 …` – tai 6! = 720.

> Ką sakyti apie taimerį. Vykdant Run, ties komanda `JG10` žurnale bus `TIMER`: 30 taktų kvantas baigėsi, TI=0. Kitos parengtos VM nėra, todėl OS atiduoda procesorių tai pačiai VM su nauju kvantu (TI=30).

### 2 demonstracija. Programinis pertraukimas
- Atidaryti `prog6_faktorialas.txt`, pakeisti `DW 6` į `DW 13`, išsaugoti, įkelti, **Run**.
  → `PI=4 aritmetinis perpildymas` (13! netelpa į 32 bitus), VM nutraukta, atmintis atlaisvinta.
- Arba `prog5_pi_demo.txt` → `PI=5 dalyba is nulio`.

### 3 demonstracija. FORK, flash ir taimeris (aiškiau CLI)
```
python vm_os/cli.py
flash_file vm_os/programs/prog3_flash.txt
load vm_os/programs/prog3_fork_io.txt
timer 1
step
list
run
printer
```
- Po `step`:
  - `[0003] FORK event=FORK`, `AX=9001`, `pertraukimas: PI=0 SI=3 TI=0`;
  - viena komanda sukėlė **iš karto SI ir TI**, ir abu įvykius apdorojo vienas `handle_interrupt`: sukūrė vaiką ir perjungė procesorių.
- `list`: `VM0003 AX=9001 PC=11` ir `VM9001 AX=0 PC=11` – abi tęsia nuo kitos komandos, vaiko AX=0.
- Toliau tėvas ir vaikas keičiasi kas 1 taktą (round-robin). Šakos skirtingos: tėve `CMP` duoda ZF=0, vaike ZF=1, todėl `JE18` šoka tik vaike.
- `printer`: tėvas spausdina savo duomenų puslapį, vaikas – bloką, perskaitytą iš flash komanda `GD20` (kanalas FLASH→USER).

### 4 demonstracija. Paketas, „vėliausiai užkrauta“, atminties trūkumas, kill
1. **Load paketas** → `paketas_2jobs.txt` → žurnale sukurtos dvi VM, aktyvi **0003** – vėliausiai užkrauta.
2. **Set AX** → 5, **Set MEM** → adresas `00`, reikšmė `77` → registras ir atmintis pakeisti vykdymo metu. Pakeitimas vyksta per OS SUPERVISOR režimu.
3. Atminties trūkumui parodyti: įkelti paketą, kurio užduotims iš viso reikia daugiau nei 64 blokų. Žurnale bus „laukia RM atminties“. Po **Kill** vykdomai VM laukianti užduotis įsikels pati.
4. **Kill** + ID – nutraukti VM, pvz., su amžinu ciklu `JM10`. Žingsnių skaičiaus ribos nėra, todėl užsiciklinusią programą sustabdo operatorius.

### CLI komandų atmintinė
`load <f>`, `step [n]`, `run` (Ctrl+C – pauzė), `regs`, `next`, `pt`, `vmmem [p]`, `rmmem <b>`, `rmall`, `dev`, `printer`, `list`, `use <id>`, `kill <id>`, `remove <id>`, `timer <n>`, `setreg AX|BX|PC <v>`, `setmem <AA> <v>`, `flash_file <f>`, `log`.

### Pavyzdinė programa – paaiškinti eilutė po eilutės (`prog6_faktorialas.txt`)
```
DATASEG 00        00: N=6   01: rezultatas=1   02: konstanta 1
CODESEG 10
10 LA01   AX := rezultatas
11 LB00   BX := N
12 MUL    AX := AX·BX          (perpildymas → OF, PI=4)
13 SA01   rezultatas := AX
14 LA00   AX := N
15 LB02   BX := 1
16 SUB    AX := N−1
17 SA00   N := AX
18 CMP    požymiai pagal AX−BX, t. y. (N−1) − 1
19 JG10   jei N−1 > 1 – kartoti
1A PD00   0 puslapį į spausdintuvą (SI=2, 3 taktai)
1B HALT   SI=4
```

---

## 4. Dėstytojo pastabos: kas buvo, kas padaryta, ką parodyti

| # | Pastaba | Kas buvo blogai | Kas padaryta | Kur parodyti |
|---|---|---|---|---|
| 1 | **Handle interrupt – viena funkcija** | Kiekviena priežastis (PI, GD, PD, FORK, HALT) buvo apdorojama savo šakoje su savais registrų valymais ir režimo keitimu. Atlikus FORK kvanto pabaigoje TI buvo prarandamas | Viena funkcija `handle_interrupt` iš 4 žingsnių: priežastis → sprendimas → registrų valymas → planavimas. Bendrą VM pabaigos kelią (`_finish_vm`) naudoja HALT, PI ir kill | [control_program.py:443](vm_os/control_program.py#L443), [_finish_vm :502](vm_os/control_program.py#L502) |
| 2 | **Jokių apribojimų (max steps)** | `run_all(max_steps=10000)`, GUI turėjo savo ribą | `run_all` sukasi, kol yra parengtų VM. GUI žingsnius vykdo per `after()` ir neužstringa, CLI sustabdomas Ctrl+C. Užsiciklinusią programą nutraukia `kill` | [run_all :561](vm_os/control_program.py#L561), [kill_vm :242](vm_os/control_program.py#L242). Testas įvykdo 11 003 žingsnius |
| 3 | **Kanalų įrenginys** | `xchg` SUP šaltiniui grąžindavo nulius, nežinomas šaltinis – irgi nulius, patikrinimų nebuvo | `ChannelDevice` su registrais ST, DT, SB, DB ir komanda XCHG (1 blokas = 16 žodžių) veikia **tik SUPERVISOR** režimu. Per kanalą eina: paketas SUP→DISK, įkėlimas DISK→USER, GD FLASH→USER, PD USER→PRINTER, puslapių kopija atliekant FORK USER→USER | [real_machine.py:46](vm_os/real_machine.py#L46). GUI skydelyje: `Kanalas DISK[1]->USER[35]` |
| 4 | **ptr_encode blogai** | Laukai buvo tyliai apkarpomi kaukėmis. Vertimas priklausė nuo globalaus `rm.ptr` ir Python lentelės kopijos, todėl neaktyvi VM galėjo skaityti svetimą atmintį | `PTR = a0 a1 a2a3`: a2a3 – lentelės **bloko numeris**, reikšmės tikrinamos. Adresą verčia **tik** `RealMachine.translate` pagal PTR ir lentelę **RM atmintyje**. Šešėlinės lentelės kopijos nebėra | [ptr_encode :21](vm_os/real_machine.py#L21), [translate :127](vm_os/real_machine.py#L127) |
| 5 | **Forkas blogas → blogai interruptai** | Kvanto pabaiga per FORK buvo prarandama, trūkstant atminties tėvas būdavo nutraukiamas | Vaikas gauna savus atsitiktinius blokus, savą lentelę (savą PTR), puslapių kopiją per kanalą ir registrų kopiją. PC = kita komanda, AX=0, tėvo AX = vaiko ID. Trūkstant atminties ar laisvo ID, tėvas gauna AX=−1 ir tęsia | [_fork :378](vm_os/control_program.py#L378). 3 demonstracija: `SI=3 TI=0` viename žingsnyje |
| 6 | **UI negali eiti atskirai nuo OS** | CLI ir GUI tiesiogiai keitė registrus, atmintį, flash | GUI ir CLI kviečia **tik** `ControlProgram` metodus. Kiekvienas toks kreipinys pažymėtas `@os_entry` ir vykdomas SUPERVISOR režimu. Net atminties lentelių duomenis pateikia OS (`vm_memory_rows`, `rm_memory_rows`) | [os_entry / _supervisor :14–47](vm_os/control_program.py#L14) |
| — | **Nuimti visur steką** | 5 variantas aprašo steko mašiną | Steko, PUSH, POP ir SP nėra niekur – nei kode, nei ataskaitoje | `COMMANDS` [virtual_machine.py:202](vm_os/virtual_machine.py#L202) |
| — | **AX ir BX pagrindiniai** | — | Visos aritmetinės ir loginės komandos dirba su AX ir BX, rezultatas rašomas į AX | `_arith`, `_logic` |
| — | **Tiek daug jumpų nereikia, loopo nereikia** | — | Minimalus rinkinys: JM ir trys sąlyginiai (JE, JG, JL) po CMP. LOOP komandos nėra (tai tikrina testas) | `COMMANDS` |
| — | **VM header su ID** | — | `VirtualMachine.id`: iš užduoties `ID nnnn`, FORK vaikui – naujas ID 9001–9999 | [virtual_machine.py:28](vm_os/virtual_machine.py#L28) |
| — | **Flash realiai mašinai** | — | Flash turi tik `RealMachine`, VM jo neturi. VM prašo duomenų komanda GD, o OS perkelia bloką kanalu | `rm.flash`, `_service_io` |
| — | **Nebūtinai implementation I/O, UI** | — | Ataskaita aprašo tik kanalo ir įrenginių sąsają, be realizacijos (§1.6, §1.7.3) | ataskaita |

### Jei paklaus „ką tiksliai pakeitėte FORK / pertraukimuose“
- **Anksčiau:** kiekviena priežastis turėjo savo šaką, o FORK šakoje taimeris buvo atstatomas neperjungiant VM. Kvantas būdavo prarandamas, ir tėvas galėjo užimti procesorių ilgiau nei turėtų.
- **Dabar:** `handle_interrupt` pirmiausia įsimena `timer_hit = TI ≤ 0`, tada aptarnauja SI (FORK) ir tik 4 žingsnyje žiūri į taimerį. Todėl SI ir TI tame pačiame žingsnyje apdorojami abu. Tai matyti 3 demonstracijoje (`SI=3 TI=0`) ir teste `test_round_robin_timer_saves_registers`.

---

## 5. Ką komentuoti kode (pagal failus)

### `real_machine.py` – „aparatūra“
- **`ptr_encode` / `ptr_decode`** (21, 40): PTR = 4 baitai `a0 a1 a2a3`. Paaiškinti su skaičiumi `0x0210000E`.
- **`ChannelDevice`** (46): `configure` įrašo ST, SB, DT, DB, `xchg` kopijuoja 1 bloką. `_require_supervisor` – prieigos teisės. `_memory_endpoint` tikrina tipą, bloko numerį ir ar blokas paskirtas.
- **`translate`** (127): MMU. „PTR → lentelės blokas → `memory[PT][p]` → realus blokas → `16·blokas + d`. Jei puslapis ≥ a0 arba įrašas blogas – `None`, ir CPU nustato PI=1.“
- **`read_word` / `write_word`** (145, 154): CPU prieiga prie atminties tik per MMU.
- **`alloc_random_blocks`** (167): `rnd.sample` – atsitiktiniai, ne iš eilės einantys blokai (papildomi balai).

### `virtual_machine.py` – VM ir interpretatorius
- **`VirtualMachine`** (28): antraštė (ID) ir išsaugotas kontekstas (AX, BX, PC, požymiai). Savos puslapių lentelės kopijos ji neturi. `owned_blocks` – tai OS išteklių apskaita, adresų vertime ji nedalyvauja.
- **`load_to_rm` / `save_from_rm`** (62, 73): konteksto perjungimas – VM registrus įkelti į CPU ir išsaugoti atgal.
- **`step`** (87): viena komanda. Tikrina USER režimą ir ar PTR priklauso šiai VM, skaito komandą pagal PC per MMU, kviečia funkciją iš `COMMANDS`, padidina PC, sumažina TI.
- **`COMMANDS`** (202): komandų sistema lentele. Kiekviena komanda – atskira funkcija, šalia jos trukmė. GD, PD, FORK, HALT **tik nustato SI**, visa kita atlieka OS.

### `control_program.py` – OS
- **`_supervisor` + `@os_entry`** (14–47): vienintelis įėjimas į OS. Įeinant MODE=SUPERVISOR, išeinant USER, jei yra VM. Atsakymas į 6 pastabą.
- **`load_package_text` → `store_job_on_disk` → `create_vm`** (83, 54, 150): užduoties kelias flash → diskas → atmintis, su atšaukimu klaidos atveju.
- **`page_table`, `vm_translate`, `vm_read`** (113–126): OS žiūri į **neaktyvios** VM atmintį per jos pačios PTR. Apsauga nuo klaidos, kai „svetima VM skaitė aktyviosios atmintį“.
- **`switch`** (354): konteksto perjungimas, naujas kvantas.
- **`_fork`** (378), **`_service_io`** (412): SI aptarnavimas.
- **`handle_interrupt`** (443): pagrindinė funkcija, 4 žingsniai (žr. §2). Atsakymas į 1 ir 5 pastabas.
- **`_finish_vm`, `_give_cpu_away`** (502, 510): VM pabaiga ir round-robin.
- **`step_once` / `run_all`** (544, 561): vienas žingsnis ir nepertraukiamas vykdymas be ribos.

### `loader.py` – paketo sintaksės tikrinimas
- `parse_package`: visi patikrinimai iš ataskaitos §2.6. `is_decimal` reikalingas, nes Python `int()` priimtų `+2`, `1_000` ir `²`.

### `gui.py` / `cli.py`
- Tik `self.osys.*` kvietimai. Run per `after()` – be ribos ir neužšaldant lango. Modaliniai dialogai pirmiausia sustabdo vykdymą (`stop_run`).

---

## 6. Tikėtini klausimai ir atsakymai

**Kam reikalingas registras …?**
- **PC** – kitos komandos adresas (0–255).
- **AX, BX** – operandai ir rezultatas.
- **FLAGS (ZF, SF, OF)** – CMP ir sąlyginiams šuoliams: JE, kai ZF=1, JG, kai ZF=0 ir SF=OF, JL, kai SF≠OF.
- **PTR** – kur yra einamosios VM puslapių lentelė ir kiek ji turi puslapių.
- **MODE** – USER arba SUPERVISOR, saugo privilegijuotus veiksmus.
- **PI** – programos klaida, **SI** – prašymas OS, **TI** – likęs kvanto laikas.
- **ST, DT, SB, DB** – kanalo registrai.

**Kuo PI skiriasi nuo SI?** PI – klaida: 1 adresas, 2 operacijos kodas, 3 apsauga, 4 perpildymas, 5 dalyba iš nulio; VM nutraukiama. SI – teisėtas prašymas OS: 1 GD, 2 PD, 3 FORK, 4 HALT; po aptarnavimo VM tęsia darbą, išskyrus HALT.

**Kodėl TI gali tapti −2?** GD ir PD trunka 3 taktus. Jei liko 1, po komandos bus −2. Taimeris suveikia, kai TI ≤ 0. Ataskaitoje parašyta „TI=0“, sąlyga ≤ 0 – saugus jos apibendrinimas.

**Kaip VM perduoda puslapio numerį GD/PD komandai?** Interpretatorius nustato tik SI. OS funkcijoje `_service_io` pati perskaito komandą adresu PC−1 ir paima puslapį iš jos. Taip CPU nerašo į OS struktūras. (Skirtumas nuo §2.5 formuluotės „interpretatorius nurodo puslapį“.)

**Kodėl VM negali sugadinti kitos VM atminties?**
1. Adresas verčiamas tik pagal savo PTR, tikrinama, ar puslapis < a0.
2. Puslapių lentelė yra už VM adresų erdvės.
3. PTR keičia tik OS SUPERVISOR režimu, vykdydama `switch`.
4. Programos kodas apsaugotas nuo rašymo (PI=3).

**Kodėl AX=−1 nepavykus FORK ir atliekant GD su tuščiu flash?** Ataskaitoje šie atvejai neaprašyti. Pasirinkta viena taisyklė: „prašymas OS neįvykdytas → AX=−1, VM tęsia“. Tai ne programos klaida, todėl PI nenustatomas.

**Kodėl kelios VM, jei užduotis prašo „nemultiprograminės“ OS?** Tai minimumo išplėtimas: reikalingas paketui, taimeriui ir FORK. Viena programa, žinoma, irgi įkeliama ir įvykdoma.

**Kodėl registrai, o ne stekas, kaip variante?** Suderinta su dėstytoju po pirmojo laboratorinio darbo („nuimti visur steką“, „AX ir BX pagrindiniai“). Užduotis leidžia keisti projektą, jei pakeitimai paaiškinami.

**Algoritmų sudėtingumas:**
- adreso vertimas – O(1): vienas kreipinys į lentelę;
- viena komanda – O(1);
- XCHG – fiksuoti 16 žodžių;
- FORK – O(P·16) kopijavimo, reikia P+1 laisvų blokų;
- blokų parinkimas `rnd.sample` – O(n) pagal laisvų blokų skaičių.

**Sprendimų panašumai ir skirtumai** (pavyzdys – Astrausko ir Lukausko darbas):
- **panašumas:** jų puslapių lentelės registras (PLR) taip pat saugo bloko numerį;
- **skirtumai:** jie neturi OS pertraukimų apdorojimo – klaidos atveju `step()` tik nustato PI. Kanalas aprašytas tik dokumentacijoje, blokai imami iš eilės, dalyba iš 0 duoda nulius, VM viena, nėra taimerio ir FORK;
- **mūsų:** vienas `handle_interrupt`, tikras kanalas, atsitiktiniai blokai, paketas, round-robin, FORK.

**Kaip įsitikinti, kad viskas veikia?**
- 49 automatiniai testai;
- apkrovos patikrinimas su 300 atsitiktinių paketų (68 050 žingsnių) – po kiekvieno žingsnio tikrinta, kad blokai nesikerta, lentelė atmintyje sutampa su OS apskaita, PI ir SI išvalyti, o PTR priklauso einamajai VM.

---

## 7. Sąžiningi apribojimai (jei paklaus „ką patobulintumėte“)

- **Supervizorinė atmintis** – vienas buferio blokas, kitos OS struktūros yra Python objektai. Tai leidžia ataskaitos §1.3.2 ir Šiaulio modelis.
- **Kodo apsauga** remiasi kodo adresų sąrašu iš užduoties antraštės. Atskiro aparatinio apsaugos bito projekte nėra.
- **`seed=42`** – blokai parenkami atsitiktinai, bet paleidimas atkuriamas demonstracijai. Be seed kiekvienas paleidimas duotų kitokį išdėstymą.
- **Kanalas sinchroninis**: perdavimas įvyksta iš karto, be įvedimo/išvedimo pabaigos pertraukimo. Ataskaitos §1.6: „kanalų įrenginys neturi atskiro procesoriaus“.
