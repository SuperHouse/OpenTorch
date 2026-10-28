# Serial capture session: checklist

Goal: one session that records all traffic on the oven's RS-485 bus while the vendor software runs, including the VFD traffic at the other baud rate. The capture is a **passive hardware tap**: a Saleae logic analyser on a receive-only RS-485 module, recorded on a MacBook. (Software port monitors on the Win7 PC broke the TN software's comms.) Allow about 1.5 hours, most of it waiting for the oven to heat.

Keep a **written log with the MacBook clock time** for every step (e.g. `14:05 clicked Start Heating`). It is the only way to match the capture to what you did. Also note what you see on the oven: stack light colour, buzzer, relays 1–3, conveyor movement, card LEDs (OUT / AL1 / AL2 / ERROR).

## 1. Preparation

### The RS-485 module (on the bench, oven off)

- [ ] **Remove the termination resistor.** Most RS-485–TTL modules have a 120 Ω resistor across A/B (on the common blue MAX485 board it is R7, marked `121`). With the module unpowered, measure A to B: about 120 Ω means it is fitted. Desolder it or cut one end. The bus is driven by a port-powered converter, and an extra 120 Ω load could break the TN software's comms.
- [ ] **Make it receive-only:**
  - Module with **RO / RE / DE / DI** pins (bare MAX485): tie **DE and RE to GND**. Leave DI unconnected. The output is **RO**.
  - Module with **TXD / RXD** pins only (auto-direction): tie the **TX input to VCC** so it can never transmit. The output is the RX pin (the labels vary; it is the pin that toggles in step "Check the signal" below).
- [ ] **Power:** 5 V for a MAX485, 3.3 V or 5 V for a MAX3485 module. The Saleae has no power output, so use a USB breakout from the MacBook or a charger, or a Pi's 5 V pin.
- [ ] Wiring to the Saleae: module output → **Channel 0**, module GND → Saleae **GND**.

### Connect to the oven (oven off)

- [ ] At the **HEXIN adapter's 3-way screw terminal**: module **A** and **B** to the two terminals that have doubled wires (probably 485+ = A, 485− = B), module **GND** to the terminal with the single black wire. Add the wires into the existing ferrules or terminals without removing the oven's wires.
- [ ] Optional: Saleae Channel 1 to bus A and Channel 2 to bus B directly (they share the GND). If your Saleae model has analog, enable analog on these channels. This shows the real bus levels if decoding fails.

### Logic 2 set-up (MacBook)

- [ ] Logic 2 settings: digital **sample rate 1–2 MS/s** (more than enough for 9600 baud). **Threshold voltage 3.3 V**. Capture mode **Timer, 120 minutes** (or manual stop).
- [ ] Add two **Async Serial** analysers on Channel 0, for live checks only:
  - 2400 baud, 8 bits, 1 stop bit, no parity (zone cards)
  - 9600 baud, 7 bits, 2 stop bits, no parity (VFD, Modbus ASCII)

### Check the signal (before the session)

- [ ] Oven: mains on, E-stop released, press the green **Start** (the cards have no power until Start). Connect the PC and dongle as normal.
- [ ] Start a short capture. In the TN software click **Open**, then **Start Inspection** (patrol) for 30 seconds.
- [ ] The 2400 analyser should show clean bytes starting `33 03 10 00 ...` with no framing errors. If you see only errors at the right baud, **swap A and B**. If the TN software now reports comms errors, disconnect the tap and check the termination resistor.
- [ ] Stop the TN patrol and Close Port, then start the real capture (next step).

### Start the real capture

- [ ] Start the Logic 2 capture and **write down the MacBook clock time** at the moment you start it.
- [ ] Close the TN software if it is open, so that section 2 starts from a fresh launch.

## 2. Card parameters (oven cold) - DONE in Session 8

- [ ] Launch TN_1.1.5.7.exe and log in **as an admin user** (the card parameter window is admin-only; see `R350 screen grabs/User details.PNG` for the admin account). (Shows whether it touches the port at launch.)
- [ ] **Do not click Open on the console in this part.** The parameter window opens the port itself, and clicking Open as well crashes the TN software. Do sections 3–5 in a fresh TN session (restart the software, or Windows if it crashed).
- [ ] Open the **Control card parameter setting** window. It is a separate window, not the "Control parameters" box on the main screen. Open it from the **TheMachineSet** menu at the top left of the main window (the exact menu item name is not known; the window looks like `R350 screen grabs/Control Card-1.PNG`). In that window:
  - [ ] Click **Online** (bottom left). This enables the Write / Read / SelfTuning / Save buttons.
  - [ ] In the drop-down at the top left, select **TempZone1**, then click **Read**. Write down the values shown.
  - [ ] Select **TempZone2**, **Read**. Then one more zone (e.g. TempZone12).
  - [ ] **Start Scanning** (right side, next to "Card Address") with all cards in place. Let it finish and note the result.
  - [ ] **Do not** click Write, Modify Address, SelfTuning or Save. Do not change any values.
  - [ ] **Back**.

## 3. Conveyor (new TN session)

- [ ] Launch the TN software (do **not** open the parameter window in this session). Console tab: **Open**. Wait 10 s.
- [ ] Click **SerialData** and look at the viewer. If it shows received data, take a photo or screenshot.

- [ ] Click **Start Transport** once. Note whether the belt moves.
- [ ] **Do not change T or F** in this session. Changing the speed made the PC's USB-RS232 adapter fail ("A device attached to the system is not functioning", then a crash). The conveyor frames are already captured (run, and frequency writes in Sessions 3 and 8), so the speed change is not needed.
- [ ] Leave the conveyor running (the manual says to keep it moving while hot).

## 4. Heating and patrol

- [ ] The conveyor must already be running from section 3 (**Start Inspection** stays disabled until Start Transport has been clicked).
- [ ] **StatusAndParameters** tab: **Read Engineering** and load the normal project. Note the SVs. Lower them to about **150 °C** for a faster, safer test if you want to, then **Write Data**.
- [ ] Console tab: **Start Heating**. Watch the stack light, relays and card LEDs.
- [ ] **Start Inspection**. Let it run **5 minutes**.
- [ ] Toggle one zone's **ON/OFF switch** off and on again (e.g. zone 5). Then untick and re-tick its **enable checkbox**. Note the times.
- [ ] Optional, if you have a type K probe: plug it into Temp test port 1, open the **Curve → SensorInspection** tab, select 1 channel, **Start**, run 1 minute, then **Stop**. (Captures the sensor card replies.)
- [ ] Wait until the zones are close to their setpoint (this can take a long time; 15–20 minutes of data is enough even if they are not there).
- [ ] **Over-temperature test:** pick one hot zone (e.g. zone 3). Lower its SV by about **30 °C** below its current temperature and **Write Data**. Watch the screen colour (yellow/red), the stack light and the buzzer for 2–3 minutes. Then put the SV back and **Write Data**. (Shows how the alarm path works, including the "Alarm Area" card, zone 2.)

## 5. Shut down

- [ ] **Stop Heating**. Note the stack light and relays.
- [ ] **Stop Transport**.
- [ ] **Do not use ExitDelay or delayed shutdown.** It can shut down Windows.
- [ ] Optional (only if you accept the risk): on a **cold** zone in the parameter form, click **SelfTuning**, then immediately **Cancel**. It captures the self-tune command. The manual warns that losing power during tuning can corrupt a card, so skip this if unsure.
- [ ] **Close Port**, then **Exit** the TN software.
- [ ] Stop the capture.

## 6. Save

- [ ] Stop the Logic 2 capture. **Save the capture** (`.sal`) so the raw data is kept.
- [ ] **File → Export Data** → CSV, **Channel 0 only**, tick **Use ISO8601 timestamps**. (Without ISO timestamps, give the decoder the capture start time with `--start HH:MM:SS`.)
- [ ] Put in a new folder `captures/<date>/` in the project folder:
  - [ ] The `.sal` and the exported `.csv`
  - [ ] Your written log (or a photo of it)
  - [ ] `TNDatabase.mdb` from the TN install folder on the Win7 PC (the live copy; the project copy is corrupt)
  - [ ] Today's log from the TN `txt\` folder
  - [ ] Screenshots or photos (SerialData viewer, parameter form, alarm screens)
- [ ] Decode: `python3 tools/decode_capture.py captures/<date>/capture.csv --csv captures/<date>/decoded.csv > captures/<date>/decoded.txt`
