# OPENTORCH: Torch R350 reflow oven reverse engineering and replacement controller

This folder holds the vendor PC control software for a Torch R350 reflow oven, screenshots, logs, manuals, bus captures, and the plan for an in-house replacement controller. The goal is to replace the vendor software with our own system (Raspberry Pi + touchscreen at the oven).

Analysis started 2026-10-11. Items marked **(inferred)** are not confirmed.

## Start here (status at 2026-10-28)

- **The protocol is now essentially known.** Live RS-485 captures (2026-10-28) confirmed the zone card replies, the write acknowledge (non-standard 6-byte), the parameter reads, the address scan, the stack light control and the conveyor VFD (Modbus ASCII slave 1). See "Zone control card protocol", "VFD / inverter protocol" and "Live bus captures".
- **The vendor software is unsafe and unreliable.** It re-enabled heaters after Stop Heating, its over-temperature cut-off did nothing, and it crashes often. Write-up: [vendor-software-issues.md](vendor-software-issues.md). Keep it updated.
- **Next: build the replacement.** The plan (architecture, safety requirements, phases, hardware) is in [replacement-plan.md](replacement-plan.md). The first task is Phase 0: a read-only probe script `controller/probe.py`, run from the MacBook with an FTDI USB-RS485 adapter. Not started yet. The adapter has not been bought yet.
- **Work happens on two Macs** that share this folder through Dropbox: a desktop Mac and a MacBook. The MacBook has Saleae Logic 2 and goes to the oven; the desktop Mac does not have Logic 2. Local Claude memory is not shared between them, so **record everything that matters in this file or the linked docs**.
- **The user's working style:** step-by-step instructions, one step at a time, when they are at the oven or in a GUI. Check instructions against the real UI (screenshots in `R350 screen grabs/`) rather than the manual's description.
- The project is not a git repository yet. The plan is to set it up before the controller code grows.

## Folder contents

- `TN_1.1.5.7/TN_1.1.5.7.exe`: "TN Series Reflow Control Systems" v1.1.5.7, a .NET Framework WinForms app (C#).
- `TN_1.1.5.7/dog_windows*.dll`, `dogdnert*.dll`: Sentinel HASP ("dog") dongle runtime.
- `TN_1.1.5.7/txt/*.txt`, `txt1/*.txt`: **serial traffic logs** (hex frames, 2014–2026). These are the best evidence available.
- `TN_1.1.5.7/TNDatabase.mdb`: **CORRUPT.** Every page after the header is zeros.
- `TN_1.1.5.7/savePicture/TNDatabase.mdb`: intact copy from 2014. It is Jet 4 with RC4 page encoding.
- `TN_1.1.5.7/en`, `id-ID`: satellite resource DLLs (UI translations).
- `vendor-software-issues.md`: summary of the safety and reliability problems found in the vendor software (the justification for replacing it). Update it when new problems are found.
- `replacement-plan.md`: architecture, safety requirements, phases and hardware for the replacement controller.
- `capture-checklist.md`: the procedure for a bus capture session (Saleae set-up, TN software steps). Section 2 is marked done (Session 8).
- `R350 Capture/`: Saleae Logic 2 captures (`.sal` raw, `.csv` exports). `R350 bus setup.sal` holds the Logic 2 settings (open it, then Start, to reuse them). Sessions 3, 5, 8, 9 and 10 are described under "Live bus captures".
- `tools/decode_capture.py`: decoder for the Logic 2 CSV exports (see "Live bus captures → Method").
- `R350 screen grabs/`: screenshots of the main console, the control card parameter form, the curve tab and the freq/time form.
- `Resources/Instruction manual V1.0.1.0.pdf`: the vendor's R350 operation manual (English, Ver 1.0.1.2, 2019, 54 pages). Open password **TORCH**, and printing is restricted. `Resources/Instruction manual V1.0.1.0 (unlocked).pdf` is the same file with the encryption removed (pikepdf), so it can be printed. The manual has no protocol details. See "Vendor manual findings".

## The executable cannot be fully decompiled

- Ghidra 12.1.2 has no CIL processor. It loads the file as x86 and finds only the `_CorExeMain` stub.
- ILSpy gives type, field and method signatures. Every method body is a stub that calls the HASP envelope loader (`_299549004._365880557`).
- The real IL is in the managed resource `.protect`, entry `ProtectData`, as a single blob for **HASP feature 890** (40,983 bytes, entropy 7.99). The blob is decrypted by `hasp_decrypt` inside the dongle and then LZMA-decompressed.
- Many string literals are also encrypted. Do not try static decryption again. The only way to get the method bodies is to dump the IL at runtime with the dongle attached.
- Readable without the dongle: class and field names, the UI layout, plaintext string literals, and resources.

### Running the vendor software on another PC (checked 2026-10-28)

- The original install is on a **32-bit Windows 7** PC inside the pick-and-place machine. No Access Database Engine is installed there, so the app reads the `.mdb` with **Microsoft.Jet.OLEDB.4.0**, which comes with Windows (the connection string itself is encrypted). The manual asks for "Office 2000 or above with Excel and Access", but the exe has no Office/Excel interop references, so Office is probably not needed.
- The exe is **CLR 2.0** (needs .NET Framework 3.5 enabled on Windows 10/11) and is built **AnyCPU** (CorFlags ILONLY only, no 32BITREQ) with no manifest. On 64-bit Windows it would run as a 64-bit process, and Jet 4.0 has no 64-bit version, so it would fail to open the database. Options: 32-bit Windows 10 (runs unmodified), or a 32-bit launcher stub that loads the exe on 64-bit Windows. Patching the CorFlags is risky because of the HASP envelope.
- The bundled dongle runtime (`dog_windows_3148630.dll`, vendor ID 3148630) supports HID "driverless" Sentinel HL keys. An older HASP HL needs the Sentinel LDK Run-time installed.
- The COM port list only offers COM1–COM8. The app writes the `.mdb` and `txt\` logs next to the exe, so install it in a writable folder (e.g. `C:\TN\`).
- The project copy of `TNDatabase.mdb` is corrupt, so copy the live one from the Win7 PC.

### Tooling recipe (macOS)

```sh
# .NET decompile (ilspycmd 8.2 targets net6; roll forward to the dotnet 8 SDK)
bash dotnet-install.sh --channel 8.0 --install-dir <dir>/dotnet
DOTNET_ROOT=<dir>/dotnet PATH=<dir>/dotnet:$PATH dotnet tool install ilspycmd --version 8.2.0.7535 --tool-path <dir>/ilspy
DOTNET_ROLL_FORWARD=Major <dir>/ilspy/ilspycmd -p -o <out> TN_1.1.5.7/TN_1.1.5.7.exe

# Ghidra headless (needs JDK 21)
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
  /opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless <proj> TN -import <exe>
```

- To read the `.mdb`, use Jackcess 4.0.5 + jackcess-encrypt 4.0.2 + bcprov + commons-lang3 + commons-logging with `CryptCodecProvider`. The Python `access-parser` and mdbtools do not handle the encoding.
- Read the logs as GB18030. Each frame line looks like `<timestamp> W|R <hex bytes>`.

## System architecture

The PC uses **one serial port**. The oven has an external **RS-232 DE9** connector. Inside the enclosure, a HEXIN RS-232↔RS-485 converter turns it into the RS-485 bus (see "Hardware → RS-485 converter"). The PC side is RS-232: a native COM port or a USB-RS-232 adapter (shows as COM3 in 2017–2026 logs; COM1/COM4 earlier). The bus connects to:

1. **Zone control cards**: one per heater zone. Each card runs its own PID loop. 2400 baud, 8N1.
2. **Profile thermocouple modules**: the same protocol and baud rate as the zone cards. These are the 4 cards on the small backplane, wired to the front "Temp test port" (see "Hardware").
3. **VFDs (inverters)**: the software supports an upper blower, a lower blower and the conveyor, at 9600 baud. **On this oven only the conveyor VFD exists**: a Delta VFD-L, **Modbus ASCII slave 1, 9600 7N2** (confirmed by capture). All blowers are wired directly to mains (see "Hardware").

How the shared bus works:

- The app runs the port at 2400 for the cards.
- Before a VFD command, it reopens the same port at 9600 (newer versions use 9600 7-bit, 2-stop framing), sends the command, then goes straight back to 2400. The methods are `xiuGBaudRate9600/2400`, and every switch is logged as a `com:COMx, b_rate:...` line.
- Address ranges don't overlap (cards 0x33–0x3E, sensors 0x47–0x4A, VFD ASCII slave 1; the older RTU VFD setup used 0x20–0x22). Devices at the other baud rate see garbage that fails the CRC/LRC check, and they ignore it.
- No zone polling happens during a VFD exchange.
- The `seriolPortSetting` table has two rows (COM1/9600, COM4/2400), but the logs never show two ports in use at the same time. **The VFD and the cards are on the same bus** (confirmed: one tap at the HEXIN terminal sees both).

The software is supervisory. It sends setpoints and PID parameters, turns heating on and off, polls temperatures, and draws curves.

## Hardware (enclosure inspection, 2026-10-12)

Photos are in `Resources/`. `2026-10-12 14.54.36.png` and `14.54.56.png` show the two sides of one card. `2026-10-11 17.31.59.jpg` shows the small backplane, and `17.32.02.jpg` shows the large backplane.

### Control card

All 16 cards look identical. Each card plugs into a card-edge slot (10 gold fingers). A 5-wire flying lead from the card plugs into a 5-pin header on the backplane, next to the slot.

| Part | Marking | Notes |
|---|---|---|
| MCU | Megawin **MA807AD44**, `1427GGCBA`, `CBR371.00B` | 8051 family with ADC. Date code 2014 wk 27. `CBR371.00B` is probably a firmware or product ID. No crystal is fitted (the HC-49 footprint is empty), so it probably uses the internal oscillator. |
| RS-485 | **MAX485** | Half duplex. It is next to 2 × EL817 and the solder pads of the 5-wire lead. |
| TC input | LM358 ("358") + 1N4148 | Analog front end. The diode is probably for cold-junction sensing **(inferred)**. Some footprints (`100/NO`, `100/RO`, `5.1K/NO`, `5m1/5k1`) look like build options for other sensor types. |
| Timer | NXP 7555CD | Probably a watchdog or an output timer **(inferred)**. |
| Isolation | 5 × EL817 + 9013 drivers | 2 near the MAX485, 2 near the card edge, 1 on the heater output. |
| Heater output | `SSR`/`SCR` jumper, MOC3061 footprint ("3061") | The card can drive an SSR, or a triac through a zero-cross optotriac. The cards seen have the jumper on SSR and no MOC3061 fitted. |
| Power | 78L05 | 5 V logic from a higher backplane rail. |
| LEDs | AL1, AL2, OUT, ERROR | These match registers 0x1006, 0x1008 and 0x1002. |
| Not fitted | 3 × TLP521 ("521"), 3 × 510 Ω, HC-49 crystal | Options. |
| 4-pin header | Not fitted, next to the MCU | Probably the ISP port. You may be able to read the firmware here if read-lock is off. |
| PCB | `DZ-FT010 2008-8` (back) | Board design from 2008-08. |

**Card power (measured):** V1 and V2 are both **12 VDC**, and **V1− and V2− are connected** (common ground). Both rails are **0 V until the green illuminated "Start" button is pressed**. So the cards (and the RS-485 bus devices on them) are unpowered, and cannot answer, until Start is pressed. The SSR drive returns to V1− (see "Heater SSRs"). Because the grounds are common at the backplane, the supplies are not isolated from each other. What the EL817 optocouplers isolate is not clear (perhaps the thermocouple front end on the card). It is not known whether V1 and V2 come from one 12 V supply or two.

### Backplanes

Both backplanes are "TORCH" boards of the same family. Each card slot has a large 2-way screw terminal above it. Both boards have 2 × HUI KE **HK3FF-DC9V-SHG** relays (9 V coil, 10 A contacts) near the AL1/AL2/220 terminals. These are probably the alarm relays **(inferred)**.

Bottom terminal strip silkscreen (read from the small board): `AL2, AL1, 220…, 485+, 485−, V1+, V1−, ?, COM2, G1…G6, V2+, V2−`. The wires hide part of the silkscreen.

| | Small backplane | Large backplane |
|---|---|---|
| Board marking | `V1.1.0-1FF0B` | `V1.0.1-1D85D` |
| Slots / fitted | 6 / 4 | 12 / 12 |
| Top screw terminals | Pairs of wires on the fitted slots (one red/green pair, the others black) | Pairs of thick wires |
| Bottom wires | 485+, 485−, V1+, V1−, V2+, V2− | L317, L316, L314, 485+, 485−, V1+, V1−, G1–G12, **V2−, V2+** |
| G outputs wired | **None** | G1–G12 |
| AL/220 terminals wired | No | Yes (L317, L316, L314, probably on AL2/AL1/220) |

Interpretation:

- **Top screw terminals = thermocouple inputs**, one for each card. Buzzed out: card-edge finger 1 (top) goes to the **right** screw terminal above the slot, and finger 2 goes to the **left** one.
- **Temp test port** (photo `Resources/2026-10-12 15.46.24.jpg`): an external panel with 4 × miniature type K sockets (CHROM +, ALUMEL −), numbered 1–4 from left to right (the number is marked on the internal cable). Buzzed out: socket N **−** goes to the **right** screw terminal of card N on the small backplane. This confirms the small backplane = profile sensors T1–T4, in slot order. Right terminal / finger 1 = **TC−**, and left terminal / finger 2 = **TC+** (probably the same on the zone cards). Socket **+** goes to the **left** screw terminal (TC+), but it does not trigger the continuity beeper. Socket 4 + to card 4 left terminal measures **about 11 Ω**. Chromel has about 2.5× the resistivity of alumel, so this is expected for the + leg of a type K extension lead. It is not a fault.
- **G1–G12 = SSR drive outputs** to the heater SSRs (GOLD SAP4025D, 3–32 VDC input; see "Heater SSRs"). G11 → zone 11 SSR IN+ is confirmed by wire tag and continuity, so G*n* → zone *n* is very likely for all of them. The SSR return is **V1−** (confirmed), not COM2. What COM2 does is not known.
- **The 4-card backplane holds the profile sensor cards T1–T4 (addresses 71–74)**, not VFD controllers. Its G terminals and alarm terminals have no wires, so these cards only measure. Confirmed by the address scan (Session 8): only 51–62 and 71–74 answer on the card side.
- L317/L316/L314 are machine wire numbers. They probably go from the alarm relays to a signal tower, a buzzer or a heater contactor, switched at 220 V **(inferred)**.
- **V2 polarity (confirmed):** the large board's silkscreen reads V2−, V2+, and the small board's reads V2+, V2−. The wire tags match the silkscreen on both boards. The difference is in the board layout, not a wiring fault.

### Power supplies and relays

Photos `Resources/2026-10-12 16.28.10.jpg` and `16.28.18.jpg`, taken looking along the enclosure.

- **3 enclosed (perforated-cover) DC power supplies** can be seen along the enclosure. The make, ratings and outputs have not been read yet. V1 and V2 are 12 V. A third rail, **V3**, exists (see below), and its voltage is not known.
- **3 × Schneider RXM plug-in relays** on a short DIN rail, each in a screw socket. They are **not** contactors. Coil **230 VAC**. The socket terminals are numbered 12/22/32/42, 14/24/34/44 and 1/2/3/4, so these are **4-pole changeover (4PDT)** relays, probably RXM4AB2P7 **(inferred)**. A short red jumper links terminals between relays.
- Wire tags seen on the relay sockets: **V3−**, **N3** (neutral), **L316**, and one that reads like "Z07" or "207" (unclear).
- **L316 matches a wire on the large backplane's AL/220 terminals.** So the backplane's HK3FF alarm relays (or whatever drives L314/L316/L317) probably switch 230 V onto these Schneider relay coils, with N3 as the return **(inferred)**.
- More wire tags seen in later photos: **L312**, **207**, and one that reads like "L004" (part hidden).

Observed behaviour (photos `Resources/2026-10-12 16.48.58.jpg` and `16.49.50.jpg`; the relay LED and flag show the state). Relays are numbered 1–3 from the power-supply end:

| State | Relay 1 | Relay 2 | Relay 3 | PSU nearest relay 1 |
|---|---|---|---|---|
| Power on (mains) | off | off | off | off |
| After **Start** | **on** | off | off | **on** (green LED) |
| After **Stop** | off | **on** | off | off |

- **Relay 1 = run latch.** Start energises it, and it switches on the nearest PSU (by its AC input or its output, not known). That is almost certainly the 12 V supply for **V1/V2**, which only appear after Start. Stop drops relay 1.
- **Relay 2 is energised in the stopped state after a run.** It does **not** time out (it stayed on for several minutes). So it is a "stopped" latch, not a timed cool-down. What its contacts switch is not known.

Behaviour with **no PC connected** to the RS-485 (2026-10-12), so this is the oven's own hard-wired logic:

- **Cooling blowers run continuously from mains power-on**, before Start and after Stop. They are not under software or relay-1 control.
- **The conveyor does not move**, before or after Start. This is consistent with the Delta VFD-L set to **REMOTE**: the conveyor needs an RS-485 run command and frequency from the PC.
- The **stack light** on top of the machine shows **steady green** through power-on, Start and Stop. Green probably means "powered" or "no alarm". The other lamps (and any buzzer) are probably driven through the alarm relays / L314, L316, L317 **(inferred)**.
- **The hot-air circulation blowers also run continuously whenever the oven has power**, even with the **E-stop pressed**. They are not controlled by the software, the relays or the E-stop. This is probably deliberate, to keep air moving over the heaters and to prevent heater or chamber overheating **(inferred)**. So the software's "upper/lower blower" VFD functions (0x20/0x21) are not used on this oven.
- **Relay 3** did not change state during Start/Stop. It is perhaps driven by the alarm outputs (L314/L316/L317) **(inferred)**.
- V3− is on a relay contact, so a relay also switches a DC rail (V3) on its negative side. Which relay that is has not been traced.

### Heater layout (chamber)

Photos `Resources/2026-10-12 17.05.01.jpg` (bottom, under the mesh belt) and `17.05.05.jpg` (lid open, top).

- Heaters are **ceramic infrared emitter plates** marked **220/230V 250W** (white) and **220/230V 125W** (cream). They run line-to-neutral on the 3-phase supply.
- **Bottom: 7 columns** across the belt. Each column has 3 plates (250 W, 125 W, 250 W = about 625 W) and **one thermocouple probe** (a rod in front of the plate nearest the viewer).
- **Top (lid): 4 visible columns**, built the same way, each with one thermocouple probe. Beside them is a **perforated stainless plate** covering an area about the size of one column. It looks like an air exhaust / extraction area.
- So 11 heater columns with thermocouples are visible, against 12 zone cards and 12 SSRs.
- **Conveyor direction: left → right, seen from the front** (confirmed). In `17.05.01.jpg` the photo is taken from the front with the lid hinged at the back. So the **entry is on the left** (the same end as the Temp test port), and the **perforated plate in the lid is at the exit end**, after the 4 top columns. An exit-end perforated plate is most likely the **top cooling section**, with the always-on cooling blowers blowing through it **(inferred)**. If so, it is not a hidden heater zone, and the 5th "top" SSR group zone is still not accounted for. **But the vendor manual's spec table says "Heater No. 12pcs (5 up / 7 down)"**, so a 5th top heater does exist. It may be under the perforated plate. Seen from the front, the bottom columns are numbered B1 (entry) … B7 (exit), and the top columns T1 … T4 from the entry end.

**(Inferred) Zone → position.** The SSR groups match the physical sides: group 1 (zones **1, 3, 5, 7, 9** = 5 SSRs) is the **top**, and group 2 (zones **2, 4, 6, 8, 10, 11, 12** = 7 SSRs) is the **bottom (7 columns)**. That leaves one SSR in group 1 without a visible top column (the manual confirms 5 top / 7 bottom heaters). The perforated plate is at the exit end and is probably cooling, so the extra zone could be under it after all, could be heaters somewhere else (for example a pre-heat section), or could be unused. It also means **zones 11 and 12 are both bottom zones**, which breaks the software's "odd = top, even = bottom" pattern. Not confirmed. To check, buzz each column's thermocouple to its card, follow the SSR load wires (L-numbers) to the columns, or switch on one zone at a time from the PC and see which column heats. Also check what is under the perforated plate.

### Heater SSRs

Photo `Resources/2026-10-12 15.54.17.jpg`, taken through a small hole in the enclosure. Access needs extensive disassembly. Two SSRs are visible, mounted on a heatsink, with clear covers labelled "heating zone 11" and "heating zone 12" (zone 11 confirmed in `Resources/2026-10-12 16.29.33.jpg`).

A second panel (`Resources/2026-10-12 16.47.52.jpg`) shows 5 more SSRs of the same type, labelled **heating zone 9, 7, 5, 3, 1** (from left to right). These are the odd (top) zones. The **other group** (the panel with zones 11/12; `Resources/2026-10-12 17.17.09.jpg`) holds 7 SSRs, labelled **2, 4, 6, 8, 10, 11, 12** (from right to left in that view). So all 12 zone SSRs are accounted for: 5 in one group (1, 3, 5, 7, 9) and 7 in the other (the even zones plus **11**). Zone 11 (odd, so top) is grouped with the bottom zones, probably only because of space or wiring. The physical grouping does not match a strict top/bottom split. Load-side wire tags (terminals 1/2, heavy red high-temperature wire):

| Zone | Load wire tags |
|---|---|
| 1 | L103, L104 |
| 3 | L107, L108 |
| 5 | L111, L112 |
| 7 | L205, L206 |
| 9 | L318, L319 |

**The oven has a 3-phase supply (confirmed 2026-10-12).** The first digit of the wire tag probably gives the phase (L1/L2/L3): zones 1/3/5 on L1, zone 7 on L2, zone 9 on L3. So the heater zones are spread across the phases **(phase-per-tag inferred)**. The L3xx wires on the backplanes' AL/220 terminals (L314, L316, L317) and on the relays (L312) are then probably on phase L3. **Safety:** adjacent SSRs can have about 400 V between them. One input wire on the zone 3 SSR is tagged **V1−**, which matches the zone 11 finding.


- Make/model: **GOLD SAP4025D**, from Jiangsu Gute Electric Control Technology Co. (江苏固特电气控制技术有限公司, `www.sst.com.cn`).
- Ratings printed on the case: load **40–480 VAC**, input **3–32 VDC** ("D" = DC control). Single phase, 25 A (from the model number).
- Terminals: 1, 2 = load (AC). **3 = IN+**, **4 = IN−**. A red/black twisted pair on spade terminals goes to the input.
- **Confirmed:** the wire on the zone 11 SSR's + input (terminal 3) is tagged **"G11"** and buzzes out to G11 on the large backplane. So **G*n* → SSR IN+ of heating zone *n***, and the card output sources current into the SSR. **Confirmed:** SSR IN− (terminal 4) goes to **V1−**. So the G outputs are referenced to V1 (12 V), and the drive voltage is probably up to 12 V (within the 3–32 V input range).
- Load: heavy red high-temperature wire.
- Whether these are zero-cross or random-turn-on is not confirmed. Check the GOLD datasheet. With the card jumper on SSR, the card most likely does time-proportional on/off drive **(inferred)**.

### RS-485 converter

Photo `Resources/2026-10-12 15.53.55.jpg`. The external DE9 connector on the oven is part of a **HEXIN "Interface Converters"** RS-232↔RS-485 dongle (the label reads only `S/N:HEXIN`, no model). Its RS-485 end plugs into a small green adapter PCB with a 3-way blue screw terminal. One black wire is on the top terminal (probably GND). Each of the other two terminals (probably 485+ and 485−) has 2 wires in a ferrule, so the bus branches here, probably to the two backplanes and the VFD.

**(Inferred)** HEXIN dongles of this shape are usually passive, port-powered converters with automatic direction control:
- They take power from the PC's RS-232 control lines, so a replacement host should hold DTR/RTS asserted.
- The transmitter turns on automatically while data is being sent, so no RTS direction control is needed. The baud changes between 2400 and 9600 are therefore handled transparently.
- The 8-byte "R" lines in the logs may be the converter echoing the transmitted frame back (local echo), not responses from the cards. The RS-485 side shows no echo (captures), so any echo happens in the converter on the RS-232 side **(inferred)**.

### VFD

One **Delta VFD-L** was found (photos `Resources/2026-10-12 15.34.00.jpg`, `15.34.07.jpg`, `15.35.02.jpg`). It is the panel-mount version with switches and a knob, not a keypad. The front shows FWD/REV and STOP/RUN slide switches, a speed knob (Low–High), POWER and ALARM LEDs, and an ACC/DEC trimpot (0–30 s). A label on it reads `60 / 100W 230V 1 PHASE`. A 100 W motor suggests this is the **conveyor** drive **(inferred)**.

DIP switch (behind the cover). Positions are read from the photo:

| Switch | Options | Set to |
|---|---|---|
| 1 | X1 / X2 | X1 |
| 2 | 60HZ / 50HZ (?) | 60HZ |
| 3 | REV EN / REV DIS | REV EN |
| 4 | LO TQUE / HI | LO TQUE |
| 5 | HI THM / LO | HI THM |
| 6 | PANEL / EXT | PANEL |
| 7 | LOCAL / REMOTE | **REMOTE** |

The back has the terminals ⏚, L/L1, N/L2 (230 V single-phase in), U/T1, V/T2, W/T3 (motor out), a green control terminal block, and an **RS485 modular jack** (RJ11 style). Only 2 wires (brown, green) are crimped into the jack.

- REMOTE probably hands run/stop and frequency control to RS-485 **(inferred)**. It is not known whether PANEL (sw 6) still affects the frequency source in REMOTE mode.
**From the VFD-L user manual** (`Resources/Delta-VFD-L-Series-Users-Manual.pdf`). This is the keypad version's manual. It does not describe the switch panel (the DIP switch, LOCAL/REMOTE) fitted here.

- **Comms factory defaults:** Pr 9-00 address = **1**, Pr 9-01 baud = **9600**, Pr 9-04 protocol = **Modbus ASCII 7,N,2**. These match the newer logs' 9600 / 7 data / 2 stop framing and the code literal `01062001` (ASCII frame `:01062001…`, slave 1, write 0x2001). **Confirmed by capture (Sessions 3, 9, 10): slave 1, ASCII, 9600 7N2**, its factory comms settings. The switch panel gives no way to change them. The 0x20–0x22 RTU addresses in the 2015–2016 logs are not this drive's current settings.
- Other protocol options (Pr 9-04): ASCII 7E1/7O1/8N2/8E1/8O1, and RTU 8N2/8E1/8O1. There is **no 8N1** option.
- Pr 9-02 comm fault = "warn and keep operating" (default). Pr 9-03 Modbus watchdog = disabled (default). So **if the host stops talking, the conveyor keeps running** at its last speed.
- On the keypad version, RS-485 control needs Pr 2-00 = d4 (frequency from RS-485) and Pr 2-01 = d3/d4 (run command from RS-485). On this unit, the REMOTE DIP switch probably selects the same **(inferred)**.
- **Registers:**
  - `2000H` command: bits 0–1 = 01 stop, 10 run, 11 jog+run. Bits 4–5 = 01 FWD, 10 REV, 11 change direction. So the logged values 1 = stop and 2 = run match.
  - `2001H` frequency command in 0.01 Hz.
  - `2002H` bit 0 = external fault, bit 1 = reset.
  - `2100H` error code (0 = none, 1 oc, 2 ov, 3 oH, 5 oL1, 6 EF, 14 Lv, …).
  - `2101H` status (run/stop/dir bits; bit 8 = freq from comms, bit 10 = command from comms).
  - `2102H` freq command, `2103H` output freq, `2104H` output current (A), `2105H` DC bus V, `2106H` output V.
  - Parameters are at `ggnnH` (e.g. Pr 4-01 = 0401H). Read them with 03H one at a time.
- Exceptions: the function code has its top bit set (e.g. 86H). Codes appear as "CExx" on the keypad.
- **RJ-11 jack pinout:** 1 = +EV, 2 = GND, 3 = SG−, 4 = SG+. The manual draws the pins numbered 6…1, so the orientation is ambiguous. Check it with a meter. Only 2 wires are fitted here (presumably SG+/SG−).
- Drive size: 100 W = identity code 2, rated current 0.8 A (Pr 0-00/0-01). The exact model number is not in this manual's model lists.
- **This is the only VFD on the oven** (the conveyor). All blowers are hard-wired to mains. The 2015–2016 logs show RTU writes to 0x20, 0x21 and 0x22. Those may be from a different configuration or model preset, because the software writes to all three whether or not they exist. The 2017–2026 logs (7-bit/2-stop framing) record only the baud switches, not the VFD frames. The bus captures show the current set-up: ASCII slave 1.

## Zone control card protocol

- **Modbus RTU**, 2400 baud, 8N1, RS-485. The CRC is standard Modbus CRC16 (`ClassCRC` has the `auchCRCHi/Lo` tables). About 1M logged frames all have valid CRCs.
- The frame struct `serialPortCommand` is `adr, order(func), regHi, regLo, dataHi, dataLo, crcHi, crcLo`.
- Function codes in use: `03` (read holding registers) and `06` (write single register).
- **Addresses** are decimal in the UI and in the DB `ADDR` column:
  - Zones 1–12: 51–62 (0x33–0x3E).
  - Profile sensors T1–T4: 71–74 (0x47–0x4A).
  - Older logs also show address 50 (0x32).
- The software's UI assumes 6 top (odd) and 6 bottom (even) zones, but this oven has **5 top and 7 bottom heaters** (manual and SSR grouping; see "Heater layout"). The software supports up to 16 zones.

### Register map

The registers are spaced 2 apart. **A multi-register read returns one word per parameter, with word k coming from register start + 2k** (confirmed 2026-10-28: `03 1000 x3` returns PV, OUT, SV). So the count is the number of parameters, and the address works like a byte offset. Older card firmware uses the same layout at base `0x0000` (seen in 2015 logs).

| Reg | Name | Notes / typical value |
|---|---|---|
| 0x1000 | PV | Measured temperature in whole °C (unsigned word, no decimal). Polled with count 3, about every 5 s per zone. The reply is standard Modbus `adr 03 06 PV OUT SV crc`, e.g. `33 03 06 00 0B 00 64 00 B4 88 7D` = PV 11, OUT 100, SV 180. |
| 0x1002 | OUT | **Live heater output in %** (0–100), as the manual says ("current output power"). It varies 0–100 while the card regulates (Session 10), and reads 100 when the oven is cold and far below SV. Treat it as read-only. |
| 0x1004 | SV | Setpoint in °C, 140–260 seen |
| 0x1006 | AL1 | Alarm 1, 400. The PC also writes 0 here when it forces a zone off. |
| 0x1008 | AL2 | Heating alarm control. The manual says 400 = heat, 0 = no heat, default 0. The PC writes 400 to the "Heating Area" card (zone 1 by default; see System Parameter Setting) at "start of heating", and 0 at "stop". This probably switches that card's relay output to show or enable heating **(inferred)**. |
| 0x100A | SC | Sensor correction, 0 |
| 0x100C | **P** | 30 |
| 0x100E | **I** | 250 |
| 0x1010 | **D** | 50 |
| 0x1012 | XL | Temperature control slope (from the manual), default 1000 |
| 0x1014 | ADDR | Card address. "Start Scanning" reads `03 1014 x2` at **every address 0–255**, about 0.2 s each, about 52 s in all (confirmed). "Modify Address" writes it. |
| 0x1016 | ? | Unknown, reads **100 on every card** (seen as the second word of the ADDR read). Possibly LCK or a scale limit **(inferred)**. |
| 0x1022 | Command | `0x99` = heat ON (run), `0xCC` = heat OFF (stop), `0x03` = self-tune (**inferred**; it follows the SelfTuning button) |

The DB also has an `LCK` column, a parameter lock with no register seen in the logs. The manual says LCK is a password that stops the card's menu from being changed. It also lists lower and upper scale limits of 0–400 ("do not modify"), so the cards have more parameters than the ones in this table.

**Write acknowledge is non-standard:** a card answers a `06` write with only 6 bytes, `adr 06 regHi regLo crcLo crcHi`, with the CRC over those 4 bytes (e.g. `3E 06 10 06 60 0F`). Standard Modbus would echo all 8 bytes. A generic Modbus library will reject this as a short or invalid reply, so the replacement needs its own framing.

Example frames:

```
33 06 10 04 00 DC C9 40   zone1 SV = 220
33 06 10 22 00 99 E9 78   zone1 heat on
33 06 10 22 00 CC 29 47   zone1 heat off
33 03 10 00 00 03 05 19   read zone1 PV block
```

### Logged command sequences

- **"The start of heating"**: writes `33 06 1008 = 400`, then `1022 = 0x99` to all cards. Confirmed live (Session 10): the **stack light changes from green to orange** at this point, so AL2 on the "Heating Area" card (zone 1) drives the orange "heating" lamp.
- **"Stop heating"**: writes `1008 = 0` on the first card, then `1022 = 0xCC` to all cards, then `1006 = 400` to all cards (Session 10). The stack light goes back to green.
- **"writer Data"** (Write Data) only writes SV (`1004`) to each card. The UI will not do this while heating or inspection is running.
- **"Began to patrol"** (Start Inspection): loops over the cards reading `03 1000 x3`, one card about every 420 ms (a 12-zone round about every 5 s). In older logs, for a disabled or over-temperature zone it also writes `1006 = 0` and `1022 = 0xCC` on every poll, and when the zone recovers it re-enables it with `1006 = 400` and `1022 = 0x99`. **This re-enable logic also fires after Stop Heating** (Session 10): it turns zones back on as they cool below SV. See the safety bug under "Live bus captures".
- **Enable checkbox** on a zone: untick sends `1022 = 0xCC`, re-tick sends `1022 = 0x99`.

## Temperature control and PID

- **The PID runs on each card's firmware.** P, I and D are just parameters the PC writes. In a 2 h 40 min run (`txt/20221209.txt`), SV was written only 12 times, all at the start. After that the traffic is only PV polling and on/off commands.
- The PC adds an **on/off over-temperature cutoff**. Inputs are `tempQu{heatTemp, baojingTemp}` and the DB fields `超温报警` (over-temp alarm threshold, 10–80) and `报警温区`. Screen colours: yellow = warning, red = "Stop Heating" (cut). **In the live test (Session 10) it did not trigger** with a zone 30+ °C over SV for 3 minutes, so its condition is unknown and it cannot be relied on.
- Self-tuning happens on the card (`ControlParameterDataForm.adjustmentPID`; it probably writes 0x1022 = 0x03).
- Default values for every zone: P=30, I=250, D=50, XL=1000, AL1=400, OUT=100.

## Vendor manual findings

From `Resources/Instruction manual V1.0.1.0.pdf` (Beijing Torch Co., Ltd), text and screenshots. Chapters 7–10 are general reflow theory with generic diagrams (plenum, cooling, conveyor) and nothing R350-specific.

- **Specs:** 12 heaters (5 top, 7 bottom). Single-phase 220 V 50 Hz is standard, and 3-phase 380 V is a custom option, so this oven is the custom build. Max 10 kW, average 2 kW. Belt 350 mm wide, max PCB width 330 mm, chamber height 35 mm. Belt speed 120–4500 mm/min. Warm-up takes 40–60 min. Control accuracy 5 °C.
- **System Parameter Setting form (screenshot):** Port COM1, 2400, parity None, 8 data bits, 1 stop bit, **Timeout 3 s**, **Alarm 6 °C**, Communication RS485, **Heating Area: TempZone1**, **Alarm Area: TempZone2**. So the software assigns two special roles to zone cards, and both are configurable: the "heating" card (zone 1, which gets AL2 = 400/0 at start/stop of heating) and the "alarm" card (zone 2). The 6 °C is probably the over-temperature margin before an alarm **(inferred)**. The logs never show an alarm-specific write to zone 2 (its only AL2 writes are from the parameter form's write-all), so how the alarm card is driven is not known. Possibly its AL2 relay output switches the buzzer / red lamp when the PC flags over-temperature **(inferred)**. The text says "temperature controlled heating zones 1 and alarm".
- **Troubleshooting:** "Buzzer or red lights without over-temperature alarm" → the AL2 value on the card is wrong. Scan, read it, and set it to 0. This confirms that the AL2 output drives the buzzer and the stack light's red lamp.
- **Self-tuning:** it runs on the card and takes 30–40 minutes per zone. All zones should start below 45 °C, with the normal project written to the cards. **Losing power during tuning can make the card "lose its internal program"**, so be careful with anything that sends `0x1022 = 0x03`.
- **Address change procedure:** remove all cards and fit only the card to change. Use "Start Scan" to read its current address (e.g. 0), then select the new zone and click "Modify Address". This confirms that a scan reads register 0x1014 from one card at a time.
- **Delayed shutdown:** runs a cool-down so the oven can exhaust its heat before it turns off (typically 30 min). The maintenance photo caption lists an "off time delay relay", a "circuit control switch", single-phase breakers for each heating zone, and the zone SSRs. The delay relay may be one of the Schneider relays or a separate timer **(inferred)**.
- **Conveyor fault "Inverter communication error":** check the comm port, the line and the **DIP switch**. This is consistent with the VFD being under RS-485 control (REMOTE).
- **Operating sequence (4.2):** power on, reset the E-stop, press green Start, then connect the PC and dongle. In the software: System settings → Open → Read Engineering (project) → Write Data → set the conveyor speed → Start Transport, Start Heating, Start Inspection. **Shutdown:** stop the software, press the oven's **red "delay stop" button**, turn off the PC, "wait for the automatic delay until the belt stops running", then switch off the main breaker. The manual also says the shutdown cool-down must be run with transport on, so that the chain doesn't overheat. So the Stop button starts a hardware off-delay. This probably explains relay 2 (energised after Stop and still on after several minutes), which is probably the off-delay stage, with the belt/fans running until it times out **(inferred)**. Note that the conveyor did not move after Stop with no PC connected, so on this oven the belt may need the PC. Time the full delay to check.
- **Maintenance photo (5.1.6)** shows a different (probably single-phase) build: 4 × **ZYS48 digital timer relays** ("off time delay relay"), a CHINT "circuit control switch" (contactor/breaker), a row of about 12 single-pole DIN breakers (**one per heating zone**), a terminal strip and an enclosed PSU. The SSR photo shows SSRs with Chinese zone labels. This oven uses Schneider RXM relays instead, so its delay logic may be built differently.
- **Main screen (screenshot):** Control parameters F (Hz) → T table: 4 → 30.25, 5 → 22.48, 6 → 17.65, 7 → 14.32, 8 → 11.68, 9 → 7.25, 10 → 3.29. The column is labelled "T (S)", but the values are minutes (4 Hz = 30.25 min = 1815 s, which matches `TimeP` 4 → 1815). The 9 and 10 Hz values don't fit `TimeP` (10 → 360 s), so these are probably example values. Control Properties: T 0–40 min, F 0–60 Hz.
- **Control card parameter form (screenshot):** zone 1 example: OUT 100, SV 235, AL1 400, AL2 0, SC 0, **P 10, I 100, D 50, XL 950**, ADDR 51. Other zones' SVs: 235/235/235/220/200/200/220/200/280. The PID values differ from the DB defaults (30/250/50/1000), so the values are tuned for each oven. Max SV is 400 °C.
- **Software:** the installer is "TN-CH-EN1.0.0.1". Login fields: machine model (R350), user name (e.g. `torch1`), user ID (`01`) and password. User levels: Admin, User, Operator.
- **Communication not working:** possible causes include E-stop not reset, Start not pressed (the cards are unpowered until Start), "control board internal parameters disorder", poor ground, and interference.

## VFD / inverter protocol

- **Current set-up (confirmed by capture): Modbus ASCII, 9600 7N2, slave 1.** The drive answers each write with an echo of the frame about 1.5 ms after the request. Start Transport sends `:010620000002D7` (2000H = 2, run), then `:010620010220B6` (2001H = 5.44 Hz). Sometimes 2001H = 1 (0.01 Hz) is sent first. It got no reply before the oven's Start was pressed (probably unpowered, **inferred**).
- History: slaves 0x20–0x22 (RTU 8N1) in the 2015–2016 logs. The 2014 DB `变频器设定` table lists addresses 40, 41 and 42 (upper blower, lower blower, conveyor), with max 60 Hz.
- **Delta registers** (a Delta VFD-L is fitted; see "Hardware → VFD"): `0x2000` = command (1 = stop, 2 = run), and `0x2001` = frequency in 0.01 Hz (e.g. `0BB8` = 30.00 Hz).
- 2015–2016 logs use 8N1 RTU. Newer logs switch the port to **7 data bits, 2 stop bits** before talking to the inverter, which is **Modbus ASCII 7N2, the Delta VFD-L factory default** (confirmed from the manual; see "Hardware → VFD"). The code has the literal `01062001` (slave 1, write freq). ASCII frames are not in the vendor logs, but they are in the bus captures.
- The VFDs are on the same COM port as the cards. The app switches the baud rate to talk to them (see "System architecture").
- Conveyor calibration tables:
  - `TimeP`: frequency in Hz to dwell time (4→1815 … 10→360).
  - `变频器数据`: frequency in Hz to speed (about 2.9 units per Hz).
  - The UI sets the conveyor by T (0–40 min) and computes F (0–60 Hz). Seen: T 20 → F 5.51 Hz.

## Application features (from the UI)

- Temperature tab: zones with SV/PV, an on/off toggle, an enable checkbox and a bar graph for each.
- Console: open/close port, start/stop heating, inspection (patrol), transportation (conveyor), and a serial data viewer.
- Control card parameter form: read, write, save, online/disconnect, scan and modify address, self-tuning.
- Sensor inspection / Curve tab: 1–4 thermocouple channels with per-channel correction, time axis of 1200/2400/3600 s, curve analysis (P1/P2/K), and save/print of the image or `.txt`.
- Projects (recipes): `工程记录` stores Speed plus Temp1–16. Also stored: users (admin/user, passwords in plaintext in the DB) and operating records.
- Freq/time setup, blower frequency setup, and delayed shutdown (by time or temperature; it calls `ExitWindowsEx`).
- zh-CN/en-US UI (`ZH_EN` table).
- Model presets: SR330, TR340C, SR350, TN360C, SR370, TN380C, R6, R8. Each model has its own `温区控制设定N` table.

## Database schema (savePicture copy)

`温区控制设定3..8, 23` (per-model zone parameters: TempName, SV, AL1, AL2, SC, P, I, D, XL, ADDR, OUT, LCK) · `温区控制设定` · `seriolPortSetting` · `变频器设定` · `变频器数据` · `TimeP` · `工程记录` · `operatingRecords` · `User` / `用户` · `RecordOfLanding` · `SensorColor` · `ZH_EN` · `采样数据`

## Open questions

- ~~Response format of `03 1000 x3`~~: **resolved 2026-10-28** by the bus capture (PV, output %, SV; see the register map). Still open: whether PV goes negative or shows a fault code with a broken thermocouple, and why zone 1 read 11 °C when the others read 16–22 °C at room temperature (SC = 0, so not the offset; it read normally when hot).
- What the sensor cards' third word (375) means, and what register 0x1016 (100 on every card) is.
- The exact meaning of `0x1022 = 0x03`, and how the XL "slope" parameter acts. What the AL2 relay outputs of the "Heating Area" card (zone 1) and the "Alarm Area" card (zone 2) drive (L314/L316/L317, the Schneider relays, the stack light?), and how the PC triggers the alarm card. How long the Stop-button off-delay lasts, and what it keeps running.
- The card model. The hardware is now identified (see "Hardware"). Searching for `CBR371` / `DZ-FT010` may find a datasheet for the module.
- The rest of the card-edge pinout (finger 1 = TC−, finger 2 = TC+, confirmed on the T1–T4 cards), the 5-wire lead pinout, whether V1 and V2 come from one supply or two (both 12 V, common ground, switched on by "Start"), what switches them, and the purpose of COM2.
- The 3 power supplies (make, output voltages, which feeds V1/V2/V3), what V3 powers, and what each Schneider relay switches. Relay 1 = run latch (confirmed). What relay 2 (energised after Stop, does not time out) switches, and what drives relay 3 (L314/L316/L317?).
- Zone number → physical heater column (7 bottom, 4 visible top + a perforated plate; see "Heater layout"). What is under the perforated plate (exit end, probably cooling).
- Whether the L1xx/L2xx/L3xx wire tags mean phases (3-phase is confirmed), and the full zone → phase map.
- The HEXIN converter model, the pinout of its 3-way terminal, and where each bus branch goes.
- The exact Delta VFD-L model number and the orientation of the RJ-11 pinout. (Slave 1, ASCII 9600 7N2 is confirmed.)
- Whether the firmware can be read through the 4-pin ISP header.

## Live bus captures (2026-10-28)

### Method

- **Software port monitors on the Win7 PC do not work.** HHD needs a paid licence. With **Eltima Serial Port Monitor** running (even monitor-only, attached after the TN software opened COM3), every TN command failed with a COM3 error. Sysinternals Portmon is untried. So the captures use a **passive hardware tap on the RS-485 bus**. The session procedure is in [capture-checklist.md](capture-checklist.md).
- **Decoder:** [tools/decode_capture.py](tools/decode_capture.py) runs a software UART on the exported signal at 2400 8N1, 9600 7N2 and 9600 8N1, so the baud switching is handled from one capture. It checks Modbus RTU CRC / ASCII LRC, and labels zones, registers and commands. It pairs requests with replies (latency, missing replies), understands the cards' 6-byte write ack, and reports clean 2400 byte runs that are not valid frames as UNPARSED. For card 03 replies it labels word k as register start + 2k. Verified on the real captures.
- **Hardware:** an original 8-channel Saleae Logic (digital only, all channels always recorded, fixed threshold) and an **auto-direction MAX485 RS-485–TTL module**: TX tied to VCC so it can never transmit, no termination resistor, powered from 5 V, RX → Saleae channel 0, GND → Saleae GND. The module's A/B/GND go to the HEXIN converter's 3-way terminal. The tap does not disturb the TN software's comms.
- **Logic 2 settings** (saved in `R350 Capture/R350 bus setup.sal`): 1 MS/s, Timer mode 7200 s, 3 GB buffer, trim and glitch filter off. Two Async Serial analysers on channel 0: "Cards 2400" (2400 8N1, hex) and "VFD 9600" (9600, 7 bits, 2 stop bits, ASCII). The channel data inside `.sal` files is Saleae's internal format, so export CSV for the decoder.
- **Decode:** `python3 tools/decode_capture.py "R350 Capture/Session N.csv" --column "Channel 0" [--csv out.csv] > out.txt`. The summary (frame counts, latencies, missing replies, register operations) goes to stderr. Use `--start HH:MM:SS` for wall-clock times when the export has no ISO8601 timestamps. The `.sal` file's `meta.json` has `captureStartTime` (Unix ms) if the wall-clock start is needed.
- **Remember to press the oven's green Start** before a capture, or the cards are unpowered (Session 3).

### Sessions

- **First live capture, `R350 Capture/Session 3` (2026-10-28 17:20, about 50 s, Start Transport then Start Inspection):**
  - **Hardware tap:** an auto-direction MAX485 module (TX tied to VCC, no termination, 5 V) on the HEXIN terminal works. TN comms are unaffected.
  - **VFD confirmed:** slave 1, **Modbus ASCII 9600 7N2**. It replies with an echo of each write about 2 ms after the request. "Start Transport" sends `:010620000002D7` (2000H = 2, run), then `:010620010220B6` (2001H = 0x0220 = 5.44 Hz).
  - **The zone cards did not reply at all.** All 12 were polled with `03 1000 x3` (one card about every 420 ms, a full round about every 5 s), and nothing followed any request. The cause: the oven's green **Start** had not been pressed, so the cards were unpowered.
  - In the TN software, Start Inspection is disabled until Start Transport has been clicked.
- **Second capture, `R350 Capture/Session 5` (2026-10-28, about 28 s, Start pressed, Inspection running):** all 12 cards replied to every poll, 1.6–31 ms after the request (average 15 ms). Values at room temperature, cold oven:

  | Zone | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|
  | PV °C | 11 | 20 | 16 | 16 | 17 | 18 | 18 | 18 | 18 | 22 | 18 | 21 |
  | OUT | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
  | SV °C | 180 | 180 | 175 | 175 | 170 | 170 | 170 | 170 | 160 | 160 | 255 | 260 |
- **Third capture, `R350 Capture/Session 8` (2026-10-28 18:0x, about 100 s): card parameter Read (zones 1, 2, 12), then Start Scanning.**
  - **"Read" in the parameter window** reads each parameter separately as `03 <reg> x2`, for OUT, SV, AL1, AL2, SC, P, I, D, XL and ADDR (0x1002–0x1014). It does not read PV. Each reply also carries the next register.
  - **The scan found exactly 16 devices**: zones 51–62 and sensors 71–74, each returning its own address. No other devices answered at 0–255. So the VFD is not on the RTU side at 2400.
  - **PID values are tuned per card, and very different:**

    | Zone | OUT | SV | AL1 | AL2 | SC | P | I | D | XL | 0x1016 |
    |---|---|---|---|---|---|---|---|---|---|---|
    | 1 | 100 | 180 | 400 | 0 | 0 | 100 | 500 | 75 | 950 | 100 |
    | 2 | 100 | 180 | 400 | 0 | 0 | 5 | 50 | 10 | 1150 | 100 |
    | 12 | 100 | 260 | 400 | 0 | 0 | 25 | 359 | 71 | 1050 | 100 |

  - Zone 1's SC is 0, so the SC offset does not explain its low reading (11 °C against 16–22 °C).
  - A VFD frequency write (10.00 Hz) happened about 100 s in, probably on return to the main window.
- **Session 9: changing the conveyor speed crashes the Win7 PC's serial adapter** (2026-10-28, repeated several times). Windows reports "A device attached to the system is not functioning" (`ERROR_GEN_FAILURE`), the TN software then throws an exception, and only a Windows reboot recovers it. This is probably the USB-RS232 adapter driver failing on the switch to 9600 7N2 or the fast reconfiguring (counterfeit Prolific PL2303 chips are known for this) **(inferred; adapter chip not yet checked)**. Start Transport alone worked (Session 3). **`R350 Capture/Session 9` caught two of the crashes:** (1) the VFD run command went out and was answered, and then the frequency write that normally follows about 0.2 s later never appeared; (2) after a good run + 5.44 Hz, the T change sent nothing at all, and the bus stayed silent for the remaining 6 minutes (no 2400 polling either). No malformed or cut-off frames were ever sent, and the VFD answered every command it received in about 1.5 ms. **So the failure is inside the PC, on the port reconfiguration between VFD commands, before any bytes are sent.** The oven, the VFD and the bus are not at fault. Seen in the UI: T = 20 min gives F = 5.51 Hz (it was 10 Hz before). **For the replacement:** use an adapter that handles 7-bit / 2-stop framing and fast baud changes reliably (e.g. FTDI), and test the 2400 ↔ 9600 7N2 switching early.
- **Fourth capture, `R350 Capture/Session 10` (2026-10-28, about 32 min): Write Data (all SV = 150), Start Heating, Start Inspection, zone 5 checkbox, profile sensor T1, over-temperature test, shutdown.** Times below are seconds from the capture start.
  - **Heat-up:** zones went from about 20 °C to 150 °C in about 10 minutes (heating started at 287 s, and most zones were at 150 by about 900 s). Peak overshoot was about +11 °C (zone 11 at 161 °C). After that the zones held 150 ± 6 °C with OUT varying 0–100 %.
  - **Zone 5 enable checkbox:** unticking sends `0xCC` (heat off) and re-ticking sends `0x99` (heat on), about 3 s apart. The big ON/OFF "switch" is only an indicator.
  - **Profile sensor T1 (address 71):** polled with the same `03 1000 x3`, one read per 5.4 s round while SensorInspection runs. PV read 21 °C, rising to 24 °C when touched. Its third word reads 375, meaning unknown (no setpoint on a sensor card); OUT reads 100.
  - **The over-temperature test did nothing.** Zone 3 was set to SV 120 while at 152–154 °C, a 30+ °C excess, for about 3 minutes. The PC sent no cutoff (`1006 = 0` / `0xCC`), wrote nothing to the alarm card, and the stack light did not change. So the PC over-temperature cutoff seen in older logs did not trigger here. Its threshold or condition is not known (perhaps it compares against the project SV, not the card SV, or uses the DB `超温报警` value).
  - **⚠ Vendor software bug: zones turn themselves back on after Stop Heating.** At about 1694 s, Stop Heating sent heat-off to all 12 zones, and the stack light went green. But the inspection loop kept polling, and over the next 45 s it sent **heat ON (`0x99`) again to zones 2, 5, 12, 9, 8 and 4**, each as soon as its PV dropped below SV (PV 147–149 with SV 150). These are exactly the zones seen still heating. No later heat-off was sent. The cards stopped answering at about 1853 s, when card power was removed. So in the vendor software, **Stop Heating is not safe while Inspection is running**: always click Stop Inspection first, or press the oven's red Stop. The replacement must never re-enable a zone after a stop command.
  - **VFD:** a run + 5.44 Hz at 98 s got **no replies** (the drive was probably unpowered because the oven Start had not yet been pressed **(inferred)**). At 301 s the software sent 2001H = 1 (0.01 Hz), then run, then 5.44 Hz, all acknowledged.
  - **UI interlocks:** setpoints cannot be written while heating or inspection is running. Stop Heating is disabled while Inspection runs (but see the bug above).

## Vendor software quirks (for running it safely)

- **The TN software is unstable around the COM port.** If you click **Open** on the console and then **Online** in the Control card parameter window, it reports that the port is in use, throws an exception and crashes (Windows needs a restart). Going the other way fails the same way. **Workaround:** use the parameter window (Online) only in a session where Open was not clicked, and do the console work (Open, transport, heating, inspection) in a separate session. The console and the parameter window apparently each open their own SerialPort on the same COM port.
- **Changing the conveyor speed crashes it** (see Session 9 above). Click Start Transport once and do not change T or F in that session.
- **Start Inspection** is disabled until **Start Transport** has been clicked.
- **Setpoints** can only be written (Write Data, on the StatusAndParameters tab) while heating and inspection are stopped.
- **To stop safely:** click **Stop Inspection before Stop Heating**, or press the oven's red Stop. Stop Heating with Inspection running lets the patrol loop turn zones back on.
- **ExitDelay / delayed shutdown** calls the Windows shutdown function. The PC is shared with the pick-and-place machine, so do not use it.
- The **Control card parameter setting** window is opened from the **TheMachineSet** menu and needs an admin login (the `torch2` user has the admin class; the Admin account is in `R350 screen grabs/User details.PNG`). It is a separate window, not the "Control parameters" box on the main screen.
- The big ON/OFF "switches" on the zone tiles are only indicators; the checkbox switches the zone.

## Next steps

The replacement controller work is in [replacement-plan.md](replacement-plan.md) (start with Phase 0). The hardware tasks below are still open. Several can be done during Phase 1 with our own probe tool instead of the vendor software.

### 1. Bus captures

Done for everything the replacement needs (see "Live bus captures"). Not captured: SelfTuning (`0x1022 = 0x03`), the card parameter Write, Modify Address, and the over-temperature alarm path (it did not trigger). These are low priority.

### 2. Map zones to physical heaters

Kit: a meter, and an IR thermometer (optional).

- Remove the **perforated plate** in the lid (exit end) and see what is behind it: a heater, a thermocouple, or only cooling.
- **Buzz each column's thermocouple** (B1–B7, T1–T4) to the screw terminals above the cards on the large backplane. Record the column → card slot → zone mapping.
- Find which zone in SSR group 1 (zones 1, 3, 5, 7, 9) has no visible top column.
- Alternative or cross-check: turn on one zone at a time and see which column heats. This is planned as part of Phase 1 of the replacement (our own probe tool).

### 3. Finish the wiring survey

Kit: a meter. Do this with the power off unless a step says otherwise.

- **Power supplies:** read the label on each of the 3 PSUs. Find which one feeds V1, V2 and V3, and what V3 powers. Check V1+ to V2+ for continuity.
- **Relays:** find what each Schneider relay's contacts switch. What does relay 2 (energised after Stop, stays on) drive? What drives relay 3? Do L314/L316/L317 come from the backplane alarm relays?
- **COM2** on the backplanes: what is it connected to?
- **Phase map:** read the load wire tags on SSR group 2 (zones 2, 4, 6, 8, 10, 11, 12). Confirm by measurement that L1xx/L2xx/L3xx mean phases (same phase reads about 0 V, different phases about 400 V).
- **Card pinout:** buzz the rest of the 10 card-edge fingers and the 5-pin flying-lead header against the bottom terminal strip (485±, V1±, V2±, G*n*, AL1/AL2/220).
- **VFD RJ-11:** check which pins the brown/green wires use (SG+/SG−), and the pin orientation.
- **HEXIN 3-way terminal:** check that it is GND/485+/485−, and trace where each branch goes.

### 4. Desk research (no oven needed)

- Search for the card IDs `CBR371` and `DZ-FT010` (a datasheet could explain 0x1016, XL, LCK, the scale limits and self-tune).
- Find the exact model of the switch-panel Delta VFD-L (100 W, 230 V 1-phase), and a manual that covers the LOCAL/REMOTE and PANEL/EXT DIP switches.
- GOLD SAP4025D datasheet: zero-cross or random turn-on?

### 5. Later / optional

- Try to read the card firmware through the 4-pin ISP header (Megawin MA807AD44). Check first whether it is read-locked.
