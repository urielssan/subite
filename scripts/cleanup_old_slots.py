import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app, db, TripSchedule, SLOTS_WEEKDAY, SLOTS_SUNDAY
from datetime import date, timedelta as dtime

DRY_RUN = False  # cambiar a False para ejecutar la eliminación
RANGE_DAYS = 60  # rango desde hoy a revisar

with app.app_context():
    today = date.today()
    end = today + dtime(days=RANGE_DAYS)
    candidates = TripSchedule.query.filter(TripSchedule.date.between(today, end)).order_by(TripSchedule.date, TripSchedule.route, TripSchedule.time).all()

    to_delete = []
    for s in candidates:
        allowed = SLOTS_SUNDAY[s.route] if s.date.weekday() == 6 else SLOTS_WEEKDAY[s.route]
        if s.time not in allowed:
            # no borrar si tiene bookings
            if getattr(s, 'bookings', None) and len(s.bookings) > 0:
                print(f"KEEP (has bookings): {s.id} {s.date} {s.route} {s.time} bookings={len(s.bookings)}")
            else:
                to_delete.append(s)
                print(f"MARK DELETE: {s.id} {s.date} {s.route} {s.time}")

    print(f"\nCandidates to delete: {len(to_delete)} (range: {today} -> {end})")
    if DRY_RUN:
        print("DRY_RUN=True -> no se borró nada. Cambia DRY_RUN = False para ejecutar.")
    else:
        for s in to_delete:
            db.session.delete(s)
        db.session.commit()
        print("Eliminados:", len(to_delete))