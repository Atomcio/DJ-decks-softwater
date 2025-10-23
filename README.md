# DJLite – Tempo+Phase Sync DJ Decks (Beta)

Lightweight, experimental DJ deck engine with Tempo+Phase Sync, adaptive PLL, and deterministic time-stretching. Built for research and prototyping of pro-grade sync behavior.

## Status
- Beta quality: core sync works, but several features are under active development.
- Known limitation: Nudge-based phase follow is not fully functional yet.
- Expect breaking changes until the API stabilizes.

## Features
- Tempo+Phase Sync between decks using a shared `MasterClock`.
- Adaptive PLL (PID) for robust phase lock with anti-windup and filtered derivative.
- Deterministic `TimeStretchEngine` with buffering and Rubber Band configuration.
- Tempo correction with hysteresis and adaptive limits (±0.5%).
- Real-time sync quality estimation and state reporting.
- Basic UI stubs and audio pipeline primitives for future integration.

## Project Structure
- `djlite/audio/deck.py` — deck logic, playback, sync integration.
- `djlite/audio/tempo_phase_sync.py` — Tempo+Phase Sync engine (PLL, hysteresis).
- `djlite/audio/time_stretch.py` — time-stretching/pitch engine with deterministic mode.
- `djlite/audio/master_clock.py` — high-precision clock shared across components.
- `djlite/tests/` — demo and stability tests for the sync system.

## Requirements
- Python 3.10+
- See `requirements.txt` for dependencies.
- Windows is the primary dev OS for this repo; Linux/macOS may work but are untested.

## Installation
```bash
# Clone the repository
git clone https://github.com/<your-username>/p_dj_decks.git
cd p_dj_decks

# (Optional) create and activate virtualenv
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

## Quick Start
```bash
# Run the demo (30s) to observe sync behavior
python djlite/tests/test_tempo_phase_sync_demo.py

# Run the 5-minute stability test
python djlite/tests/test_tempo_phase_sync_stability.py

# Launch the app (if applicable)
python djlite/app.py
# or using scripts
scripts\run_dev.bat
```

## Usage (Programming)
Enable Tempo+Phase Sync for a target deck against a master deck:
```python
from djlite.audio.deck import Deck

master = Deck(1)
slave = Deck(2)

# Load audio and start playback (omitted here)
# master.play(); slave.play()

# Enable advanced sync
slave.enable_tempo_phase_sync(master, enabled=True)

# Query sync state
state = slave.get_tempo_phase_sync_state()
print(state)
```

Key APIs:
- `TempoPhaseSync.set_decks(target, master)` — assign decks for synchronization.
- `TempoPhaseSync.enable_sync(True/False)` — toggle sync.
- `TempoPhaseSync.update_sync()` — step sync loop; typically called periodically.
- `TempoPhaseSync.get_sync_state()` — returns `phase_offset_beats`, `tempo_correction_factor`, `sync_quality`, etc.
- `Deck.enable_tempo_phase_sync(master, enabled=True)` — integration helper.

## Known Issues & Limitations
- Nudge-based phase follow (legacy `phase_follow_tick`) is deprecated and not fully working.
- Demo test uses simplified mocks; real decks must provide precise clocks and BPM for high sync quality.
- Rubber Band high-quality mode requires proper installation; otherwise simple resampling is used.
- UI elements are placeholders; visual sync indicators are planned.

## Roadmap
- Finish nudge and micro-seek phase correction for surgical alignment.
- Improve sync quality scoring and exposure in UI.
- Expand tests and add automation for jitter/latency scenarios.
- Cross-platform audio backend and packaging.

## Contributing
- Issues and PRs are welcome during the beta phase.
- Please include reproducible steps and test coverage for sync-related changes.

## License
- To be determined. Please do not reuse commercially until the license is set.

## Repository Description (GitHub)
A compact, experimental DJ deck engine featuring Tempo+Phase Sync with adaptive PLL and deterministic time-stretching. Beta quality; nudge-based phase follow is not fully functional yet.