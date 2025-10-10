import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app, db, SharedBooking, TripSchedule, SLOTS_WEEKDAY, SLOTS_SUNDAY
from datetime import datetime

def nearest_slot_for(route, on_date, old_time):
    # elegir lista segun domingo o no
    slots = SLOTS_SUNDAY[route] if on_date.weekday() == 6 else SLOTS_WEEKDAY[route]
    # convertir a minutos
    def mm(t): return int(t[:2])*60 + int(t[3:5])
    old_mm = mm(old_time)
    best = None
    best_diff = None
    for s in slots:
        diff = abs(mm(s) - old_mm)
        if best is None or diff < best_diff:
            best = s; best_diff = diff
    return best

with app.app_context():
    moved = 0
    failed = []
    for b in SharedBooking.query.order_by(SharedBooking.id).all():
        sched = TripSchedule.query.get(b.schedule_id)
        if not sched:
            # no podemos saber ruta/fecha/hora -> revisar manualmente
            failed.append(("orphan", b.id, b.schedule_id))
            continue
        # buscar slot objetivo
        target_time = nearest_slot_for(sched.route, sched.date, sched.time)
        if not target_time:
            failed.append(("no_slots", b.id, sched.route, sched.date, sched.time))
            continue
        # asegurar que exista TripSchedule para ese target_time (crear si falta)
        tgt = TripSchedule.query.filter_by(route=sched.route, date=sched.date, time=target_time).first()
        if not tgt:
            tgt = TripSchedule(route=sched.route, date=sched.date, time=target_time, capacity=sched.capacity)
            db.session.add(tgt)
            db.session.flush()  # obtiene id
        # reasignar
        b.schedule_id = tgt.id
        moved += 1
    db.session.commit()
    print(f"Reservas reasignadas: {moved}")
    if failed:
        print("Revisar manualmente los siguientes casos:")
        for f in failed:
            print(f)