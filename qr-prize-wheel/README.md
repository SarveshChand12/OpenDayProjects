# QR Scavenger Hunt & Prize Wheel

A single, self-contained HTML page (`index.html`) for a student QR-code
scavenger hunt with a prize wheel draw. No build step, no backend — just
open the file or serve it as static HTML.

## Flow

1. **Enter name** — a student types their name to start.
2. **Find & scan 5 QR codes** — codes are printed/placed around the venue.
   Each one encodes a link back to this page (`?qr=1` ... `?qr=5`). Opening
   it with a phone camera marks that code found and updates the student's
   progress bar. A "Simulate next scan" button is included for testing
   without printed codes or a camera.
3. **Entered into the draw** — once a student finds all 5 codes they're
   automatically marked as an entrant.
4. **Host view** (link at the bottom of the start screen, passcode `1234`
   by default) — lists every student and their progress, and has a
   "Draw Random Winner" button that picks a random fully-entered student
   and opens the spin wheel. Click **SPIN** to reveal which prize
   (Prize 1, Prize 2, ...) they won.
5. **Printable QR codes** — the "Print the 5 QR codes" link generates the 5
   QR images (pointing at wherever the page is currently hosted) so they
   can be printed or displayed for students to scan.

## Running it

Any static file server works, e.g.:

```bash
cd qr-prize-wheel
python3 -m http.server 8080
```

Then open `http://localhost:8080`. Progress is stored in the browser's
`localStorage`, so for a real multi-student event this page needs to be
hosted somewhere reachable (e.g. GitHub Pages) and used from a shared
host device for the admin view, since each student's own phone only
sees its own local storage.

## Customizing

Edit the constants at the top of the `<script>` in `index.html`:

- `TOTAL_QR` — number of QR codes required (default 5).
- `PRIZES` — array of prize labels shown on the wheel.
- `ADMIN_PASSCODE` — simple deterrent for the host view (not real security).
