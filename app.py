# -*- coding: utf-8 -*-
import os
from datetime import datetime, date, time as dtime
from pathlib import Path
from functools import wraps
import json
import requests

from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from flask_sqlalchemy import SQLAlchemy

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'cambiame-por-uno-seguro')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + str(DATA_DIR / 'subite.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'subite2025')
PICKUP_API_URL = os.getenv('PICKUP_API_URL')  # Optional, fallback to demo
KM_API_URL = os.getenv('KM_API_URL')  # Optional, fallback to demo

DEFAULT_SLOTS = ["06:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00"]
CAPACITY_PER_TRIP = 4

# --- Models ---
class PriceConfig(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Float, nullable=False)

class TripSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    route = db.Column(db.String(32), nullable=False)  # 'RC-CBA' or 'CBA-RC'
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(5), nullable=False)  # 'HH:MM'
    capacity = db.Column(db.Integer, nullable=False, default=CAPACITY_PER_TRIP)

class SharedBooking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('trip_schedule.id'), nullable=False)
    schedule = db.relationship('TripSchedule', backref=db.backref('bookings', lazy=True))
    passengers = db.Column(db.Integer, nullable=False, default=1)
    name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    pickup_address = db.Column(db.String(200), nullable=True)
    extra_luggage = db.Column(db.Boolean, default=False)
    pet = db.Column(db.Boolean, default=False)
    total_price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ParcelBooking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    route = db.Column(db.String(32), nullable=False)  # 'RC-CBA' or 'CBA-RC'
    date = db.Column(db.Date, nullable=False)
    parcels = db.Column(db.Integer, nullable=False)  # 1 or 2 per booking
    name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    total_price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AirportExclusive(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(5), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    pickup_address = db.Column(db.String(200), nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class CityExclusive(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    route = db.Column(db.String(32), nullable=False)  # 'RC-CBA' or 'CBA-RC'
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(5), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    pickup_address = db.Column(db.String(200), nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AnywhereBooking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(5), nullable=False)
    origin = db.Column(db.String(200), nullable=False)
    destination = db.Column(db.String(200), nullable=False)
    km_estimate = db.Column(db.Float, nullable=True)
    name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    total_price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- Helpers ---
def seed_prices():
    seed_path = DATA_DIR / 'pricing_seed.json'
    if seed_path.exists():
        data = json.loads(seed_path.read_text(encoding='utf-8'))
        for k, v in data.items():
            if not PriceConfig.query.get(k):
                db.session.add(PriceConfig(key=k, value=float(v)))
        db.session.commit()

def price(key, default=0.0):
    pc = PriceConfig.query.get(key)
    return pc.value if pc else default

def ensure_day_slots(route, on_date):
    # Create slots for a route/date if missing
    for hhmm in DEFAULT_SLOTS:
        exists = TripSchedule.query.filter_by(route=route, date=on_date, time=hhmm).first()
        if not exists:
            db.session.add(TripSchedule(route=route, date=on_date, time=hhmm, capacity=CAPACITY_PER_TRIP))
    db.session.commit()

def booked_seats(schedule_id):
    total = db.session.query(db.func.sum(SharedBooking.passengers)).filter(SharedBooking.schedule_id == schedule_id).scalar()
    return int(total or 0)

def pickup_surcharge(address: str) -> float:
    # If external API is configured, try it. Expecting it to return {"surcharge": number}
    if PICKUP_API_URL:
        try:
            resp = requests.post(PICKUP_API_URL, json={"address": address}, timeout=6)
            if resp.ok:
                data = resp.json()
                return float(data.get("surcharge", 0.0))
        except Exception:
            pass
    # Demo fallback
    return 3000.0 if address and address.strip() else 0.0

def anywhere_price_km(km: float) -> float:
    km_price = price("KM_PRICE", 500.0)
    if KM_API_URL:
        try:
            resp = requests.post(KM_API_URL, json={"km": km}, timeout=6)
            if resp.ok:
                data = resp.json()
                return float(data.get("total_price", km * km_price))
        except Exception:
            pass
    return km * km_price

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return wrapper

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

# Shared rides flow
@app.route('/shared', methods=['GET', 'POST'])
def shared():
    if request.method == 'POST':
        route = request.form.get('route')  # RC-CBA or CBA-RC
        date_str = request.form.get('date')
        passengers = max(1, int(request.form.get('passengers', 1)))
        try:
            on_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Fecha inválida', 'error')
            return redirect(url_for('shared'))

        ensure_day_slots(route, on_date)
        # List availability
        schedules = TripSchedule.query.filter_by(route=route, date=on_date).order_by(TripSchedule.time.asc()).all()
        availability = []
        for s in schedules:
            taken = booked_seats(s.id)
            free = max(0, s.capacity - taken)
            availability.append((s, free, free >= passengers))
        return render_template('shared_slots.html', route=route, on_date=on_date, passengers=passengers, availability=availability)
    return render_template('shared.html')

@app.route('/shared/book/<int:schedule_id>', methods=['GET', 'POST'])
def shared_book(schedule_id):
    sch = TripSchedule.query.get_or_404(schedule_id)
    passengers = int(request.args.get('p', 1))
    taken = booked_seats(schedule_id)
    free = sch.capacity - taken
    if passengers > free:
        flash('Ese horario ya no tiene cupo suficiente.', 'error')
        return redirect(url_for('shared'))

    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone')
        email = request.form.get('email')
        pickup_address = request.form.get('pickup_address', '').strip()
        extra_luggage = bool(request.form.get('extra_luggage'))
        pet = bool(request.form.get('pet'))

        base = price('BASE_SHARED_RC_CBA' if sch.route=='RC-CBA' else 'BASE_SHARED_CBA_RC', 9000.0)
        subtotal = base * passengers
        extras = 0.0
        if extra_luggage: extras += price('EXTRA_LUGGAGE', 2000.0)
        if pet: extras += price('PET', 10000.0)
        pickup = pickup_surcharge(pickup_address)

        total = subtotal + extras + pickup

        booking = SharedBooking(schedule_id=sch.id, passengers=passengers, name=name, phone=phone,
                                email=email, pickup_address=pickup_address, extra_luggage=extra_luggage,
                                pet=pet, total_price=total)
        db.session.add(booking)
        db.session.commit()
        return render_template('confirm.html', category='Viaje Compartido', total=total, details={
            'Ruta': 'Río Cuarto → Córdoba' if sch.route=='RC-CBA' else 'Córdoba → Río Cuarto',
            'Fecha': sch.date.isoformat(),
            'Hora': sch.time,
            'Pasajeros': passengers,
            'Retiro a domicilio': 'Sí' if pickup_address else 'No',
            'Valija extra': 'Sí' if extra_luggage else 'No',
            'Mascota': 'Sí' if pet else 'No',
        })
    return render_template('shared_book.html', sch=sch, passengers=passengers, free=free)

# Parcels (Encomiendas)
@app.route('/parcels', methods=['GET', 'POST'])
def parcels():
    if request.method == 'POST':
        route = request.form.get('route')
        date_str = request.form.get('date')
        parcels = min(2, max(1, int(request.form.get('parcels', 1))))  # max 2 per booking
        name = request.form.get('name')
        phone = request.form.get('phone')
        email = request.form.get('email')

        try:
            on_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Fecha inválida', 'error')
            return redirect(url_for('parcels'))

        # Simple pricing: half of base shared per parcel as a demo
        base = price('BASE_SHARED_RC_CBA' if route=='RC-CBA' else 'BASE_SHARED_CBA_RC', 9000.0)
        total = (base * 0.5) * parcels
        booking = ParcelBooking(route=route, date=on_date, parcels=parcels, name=name, phone=phone, email=email, total_price=total)
        db.session.add(booking)
        db.session.commit()
        return render_template('confirm.html', category='Encomienda', total=total, details={
            'Ruta': 'Río Cuarto → Córdoba' if route=='RC-CBA' else 'Córdoba → Río Cuarto',
            'Fecha': on_date.isoformat(),
            'Bultos (máx 2 x reserva)': parcels,
            'Peso por bulto': '5 kg (máx)',
        })
    return render_template('parcels.html')

# Airport exclusive
@app.route('/airport', methods=['GET', 'POST'])
def airport():
    if request.method == 'POST':
        date_str = request.form.get('date')
        time_str = request.form.get('time')
        name = request.form.get('name'); phone = request.form.get('phone'); email = request.form.get('email')
        pickup_address = request.form.get('pickup_address')

        try:
            on_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Fecha inválida', 'error'); return redirect(url_for('airport'))

        total = price('AIRPORT_EXCLUSIVE', 60000.0)
        b = AirportExclusive(date=on_date, time=time_str, name=name, phone=phone, email=email,
                             pickup_address=pickup_address, total_price=total)
        db.session.add(b); db.session.commit()
        return render_template('confirm.html', category='Aeropuerto Exclusivo', total=total, details={
            'Fecha': on_date.isoformat(), 'Hora': time_str, 'Retiro': pickup_address
        })
    return render_template('airport.html')

# City exclusive RC<->CBA
@app.route('/exclusive', methods=['GET', 'POST'])
def exclusive():
    if request.method == 'POST':
        route = request.form.get('route')
        date_str = request.form.get('date')
        time_str = request.form.get('time')
        name = request.form.get('name'); phone = request.form.get('phone'); email = request.form.get('email')
        pickup_address = request.form.get('pickup_address')

        try:
            on_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Fecha inválida', 'error'); return redirect(url_for('exclusive'))

        total = price('CITY_EXCLUSIVE_RC_CBA' if route=='RC-CBA' else 'CITY_EXCLUSIVE_CBA_RC', 45000.0)
        b = CityExclusive(route=route, date=on_date, time=time_str, name=name, phone=phone, email=email,
                          pickup_address=pickup_address, total_price=total)
        db.session.add(b); db.session.commit()
        return render_template('confirm.html', category='Viaje Exclusivo', total=total, details={
            'Ruta': 'Río Cuarto → Córdoba' if route=='RC-CBA' else 'Córdoba → Río Cuarto',
            'Fecha': on_date.isoformat(), 'Hora': time_str, 'Retiro': pickup_address
        })
    return render_template('exclusive.html')

# Anywhere in Argentina (demo km input / API placeholder)
@app.route('/anywhere', methods=['GET', 'POST'])
def anywhere():
    if request.method == 'POST':
        date_str = request.form.get('date'); time_str = request.form.get('time')
        origin = request.form.get('origin'); destination = request.form.get('destination')
        km_str = request.form.get('km_estimate', '').strip()
        name = request.form.get('name'); phone = request.form.get('phone'); email = request.form.get('email')
        try:
            on_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Fecha inválida', 'error'); return redirect(url_for('anywhere'))

        km = float(km_str) if km_str else 0.0
        total = anywhere_price_km(km)
        b = AnywhereBooking(date=on_date, time=time_str, origin=origin, destination=destination,
                            km_estimate=km, name=name, phone=phone, email=email, total_price=total)
        db.session.add(b); db.session.commit()
        return render_template('confirm.html', category='Viaje a cualquier destino', total=total, details={
            'Fecha': on_date.isoformat(), 'Hora': time_str,
            'Origen': origin, 'Destino': destination, 'KM estimados': km
        })
    return render_template('anywhere.html')

# Admin
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        pwd = request.form.get('password', '')
        if pwd == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        flash('Contraseña incorrecta', 'error')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def admin_dashboard():
    counts = {
        'shared': SharedBooking.query.count(),
        'parcels': ParcelBooking.query.count(),
        'airport': AirportExclusive.query.count(),
        'exclusive': CityExclusive.query.count(),
        'anywhere': AnywhereBooking.query.count(),
        'schedules': TripSchedule.query.count(),
    }
    return render_template('admin_dashboard.html', counts=counts)

@app.route('/admin/prices', methods=['GET', 'POST'])
@login_required
def admin_prices():
    if request.method == 'POST':
        for key, val in request.form.items():
            if key.startswith('price_'):
                k = key.replace('price_', '')
                try:
                    v = float(val)
                except:
                    continue
                pc = PriceConfig.query.get(k)
                if pc:
                    pc.value = v
                else:
                    db.session.add(PriceConfig(key=k, value=v))
        db.session.commit()
        flash('Precios actualizados', 'success')
        return redirect(url_for('admin_prices'))

    keys = ['BASE_SHARED_RC_CBA','BASE_SHARED_CBA_RC','AIRPORT_EXCLUSIVE',
            'CITY_EXCLUSIVE_RC_CBA','CITY_EXCLUSIVE_CBA_RC','KM_PRICE','EXTRA_LUGGAGE','PET']
    items = [(k, price(k)) for k in keys]
    return render_template('admin_prices.html', items=items)

@app.route('/admin/schedules', methods=['GET', 'POST'])
@login_required
def admin_schedules():
    if request.method == 'POST':
        route = request.form.get('route')
        date_str = request.form.get('date')
        time_str = request.form.get('time')
        cap = int(request.form.get('capacity', CAPACITY_PER_TRIP))
        try:
            on_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Fecha inválida', 'error')
            return redirect(url_for('admin_schedules'))
        exists = TripSchedule.query.filter_by(route=route, date=on_date, time=time_str).first()
        if exists:
            exists.capacity = cap
        else:
            db.session.add(TripSchedule(route=route, date=on_date, time=time_str, capacity=cap))
        db.session.commit()
        flash('Horario guardado', 'success')
        return redirect(url_for('admin_schedules'))
    # list upcoming (next 60 days) simple
    today = date.today()
    scheds = TripSchedule.query.filter(TripSchedule.date >= today).order_by(TripSchedule.date.asc(), TripSchedule.time.asc()).all()
    return render_template('admin_schedules.html', scheds=scheds, booked_seats=booked_seats)

@app.route('/admin/bookings')
@login_required
def admin_bookings():
    shared = SharedBooking.query.order_by(SharedBooking.created_at.desc()).all()
    parcels = ParcelBooking.query.order_by(ParcelBooking.created_at.desc()).all()
    airport = AirportExclusive.query.order_by(AirportExclusive.created_at.desc()).all()
    exclusive = CityExclusive.query.order_by(CityExclusive.created_at.desc()).all()
    anywhere = AnywhereBooking.query.order_by(AnywhereBooking.created_at.desc()).all()
    return render_template('admin_bookings.html', shared=shared, parcels=parcels, airport=airport, exclusive=exclusive, anywhere=anywhere)

# CLI init
@app.cli.command('initdb')
def initdb():
    db.create_all()
    seed_prices()
    print('DB initialized & prices seeded.')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_prices()
    app.run(host='0.0.0.0', port=5000, debug=True)
