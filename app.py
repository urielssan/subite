# -*- coding: utf-8 -*-
import os
from datetime import datetime, date, timedelta as dtime
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

DESTINOS_RIO_CUARTO = [
    "Plaza General Paz - Rotonda Moretti, Río Cuarto, Córdoba, Argentina",
    "Baigorria 26, Río Cuarto, Córdoba, Argentina",
    "Parque Sarmiento, Río Cuarto, Córdoba, Argentina",
    "Seminario Mayor Jesús Buen Pastor, Río Cuarto, Córdoba, Argentina",
    "Constitución, X5800 Río Cuarto, Córdoba, Argentina"
]
DESTINOS_CORDOBA_CAPITAL = [
    "Av. Vélez Sarsfield & San Luis, Córdoba, Argentina",
    "Plaza de las Américas, Córdoba, Argentina",
    "Rotonda Almirante Guillermo Brown (Barrio Las Flores), Córdoba, Argentina",
    "Plaza España, Córdoba, Argentina"
]

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
    final_address = db.Column(db.String(200), nullable=True)
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

    # Agregado: direcciones de retiro y entrega para encomiendas
    pickup_address = db.Column(db.String(200), nullable=True)
    final_address = db.Column(db.String(200), nullable=True)

class AirportExclusive(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(5), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    pickup_address = db.Column(db.String(200), nullable=False)
    final_address = db.Column(db.String(200), nullable=False)
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
    final_address = db.Column(db.String(200), nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AnywhereBooking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(5), nullable=False)
    origin_city = db.Column(db.String(200), nullable=False)
    origin_street = db.Column(db.String(200), nullable=False)
    destination_street = db.Column(db.String(200), nullable=False)
    destination_city = db.Column(db.String(200), nullable=False)
    km_estimate = db.Column(db.Float, nullable=True)
    name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    total_price = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- Helpers ---
def obtener_precio(ciudad, llegada, precio_km):
    url = "https://api.refreshagency.duckdns.org/precio"
    payload = {"ciudad": ciudad, "llegada": llegada, "precio_km": precio_km}  # usar 'llegada' en lugar de 'destino'
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, data=json.dumps(payload), headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get("precio")
    except requests.RequestException as e:
        print(f"Error al llamar a la API: {e}")
        return None

def obtener_precio_larga_distancia(ciudad_origen, calle_origen, ciudad_destino, calle_destino, precio_km):
    url = "https://api.refreshagency.duckdns.org/precio_general"
    payload = {"ciudad_origen": ciudad_origen, "calle_origen": calle_origen, "ciudad_destino": ciudad_destino, "calle_destino": calle_destino, "precio_km": precio_km}  # usar 'llegada' en lugar de 'destino'
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, data=json.dumps(payload), headers=headers)
        response.raise_for_status()
        data = response.json()
        return (data.get("precio"), data.get("km"))
    except requests.RequestException as e:
        print(f"Error al llamar a la API: {e}")
        return None

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

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return wrapper

def now_hhmm():
    return datetime.now().strftime('%H:%M')

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
        # List availability: si la fecha es hoy filtrar horarios pasados
        if on_date == date.today():
            now = now_hhmm()
            schedules = TripSchedule.query.filter_by(route=route, date=on_date).filter(TripSchedule.time >= now).order_by(TripSchedule.time.asc()).all()
        else:
            schedules = TripSchedule.query.filter_by(route=route, date=on_date).order_by(TripSchedule.time.asc()).all()

        availability = []
        for s in schedules:
            taken = booked_seats(s.id)
            free = max(0, s.capacity - taken)
            availability.append((s, free, free >= passengers))
        return render_template('shared_slots.html', route=route, on_date=on_date, passengers=passengers, availability=availability)

    # GET inicial
    today = date.today()
    return render_template('shared.html', today=today.isoformat(), form_data={})

@app.route('/shared/book/<int:schedule_id>', methods=['GET', 'POST'])
def shared_book(schedule_id):
    sch = TripSchedule.query.get_or_404(schedule_id)

    # evitar reservar un horario que ya pasó si es hoy
    if sch.date == date.today():
        if sch.time < now_hhmm():
            flash('Ese horario ya pasó y no puede reservarse.', 'error')
            return redirect(url_for('shared'))

    passengers = int(request.args.get('p', 1))
    taken = booked_seats(schedule_id)
    free = sch.capacity - taken
    if passengers > free:
        flash('Ese horario ya no tiene cupo suficiente.', 'error')
        return redirect(url_for('shared'))

    # Determinar destinos según ruta
    if sch.route == 'RC-CBA':
        llegada_options = DESTINOS_CORDOBA_CAPITAL
        retiro_options = DESTINOS_RIO_CUARTO
        llegada_ciudad = "Córdoba"
    else:
        llegada_options = DESTINOS_RIO_CUARTO
        retiro_options = DESTINOS_CORDOBA_CAPITAL
        llegada_ciudad = "Río Cuarto"

    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone')
        email = request.form.get('email')
        pickup_address = request.form.get('pickup_address', '').strip()
        pickup_address_custom = request.form.get('pickup_address_custom', '').strip()
        final_address = request.form.get('final_address_select')
        final_address_custom = request.form.get('final_address_custom', '').strip()
        extra_luggage = bool(request.form.get('extra_luggage'))
        pet = bool(request.form.get('pet'))

        surcharge = 0.0
        # Si seleccionó "Otro", usamos la dirección personalizada
        if final_address == "otro" and final_address_custom:
            final_address = final_address_custom
            # Costo adicional si la ciudad es Córdoba
            if sch.route == 'RC-CBA':
                km_price = price("KM_PRICE")
                surcharge += obtener_precio("cordoba", final_address, km_price)
        
        if pickup_address == "otro" and pickup_address_custom:
            pickup_address = pickup_address_custom
            # Costo adicional si la ciudad es Córdoba
            if sch.route == 'CBA-RC':
                km_price = price("KM_PRICE")
                surcharge += obtener_precio("cordoba", pickup_address, km_price)

        base = price('BASE_SHARED_RC_CBA' if sch.route=='RC-CBA' else 'BASE_SHARED_CBA_RC', 9000.0)
        subtotal = base * passengers
        extras = 0.0
        if extra_luggage: extras += price('EXTRA_LUGGAGE', 2000.0)
        if pet: extras += price('PET', 10000.0)
        total = subtotal + extras + surcharge

        booking = SharedBooking(
            schedule_id=sch.id,
            passengers=passengers,
            name=name,
            phone=phone,
            email=email,
            pickup_address=pickup_address,
            final_address=final_address,
            extra_luggage=extra_luggage,
            pet=pet,
            total_price=total
        )
        db.session.add(booking)
        db.session.commit()

        return render_template('confirm.html', category='Viaje Compartido', total=total, details={
            'Ruta': 'Río Cuarto → Córdoba' if sch.route=='RC-CBA' else 'Córdoba → Río Cuarto',
            'Fecha': sch.date.isoformat(),
            'Hora': sch.time,
            'Pasajeros': passengers,
            'Dirección de retiro': pickup_address,
            'Dirección de llegada': final_address,
            'Valija extra': 'Sí' if extra_luggage else 'No',
            'Mascota': 'Sí' if pet else 'No',
            'Costo adicional': f"${surcharge:.0f}" if surcharge else "Ninguno"
        })

    # GET
    tomorrow = date.today() + dtime(days=1)
    return render_template('shared_book.html', sch=sch, passengers=passengers, free=free,
                           llegada_options=llegada_options, retiro_options=retiro_options,
                           tomorrow=tomorrow, config_price = price('EXTRA_LUGGAGE'),
                           config_pet = price('PET'))


@app.route('/airport_shared/book/<int:schedule_id>', methods=['GET', 'POST'])
def airport_book(schedule_id):
    sch = TripSchedule.query.get_or_404(schedule_id)

    # evitar reservar un horario que ya pasó si es hoy
    if sch.date == date.today():
        if sch.time < now_hhmm():
            flash('Ese horario ya pasó y no puede reservarse.', 'error')
            return redirect(url_for('airport_shared'))

    passengers = int(request.args.get('p', 1))
    taken = booked_seats(schedule_id)
    free = sch.capacity - taken
    if passengers > free:
        flash('Ese horario ya no tiene cupo suficiente.', 'error')
        return redirect(url_for('shared'))

    # Determinar destinos según ruta
    if sch.route == 'RC-CBA':
        llegada_options = DESTINOS_CORDOBA_CAPITAL
        retiro_options = DESTINOS_RIO_CUARTO
        llegada_ciudad = "Córdoba"
    else:
        llegada_options = DESTINOS_RIO_CUARTO
        retiro_options = DESTINOS_CORDOBA_CAPITAL
        llegada_ciudad = "Río Cuarto"

    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone')
        email = request.form.get('email')
        pickup_address = request.form.get('pickup_address', '').strip()
        pickup_address_custom = request.form.get('pickup_address_custom', '').strip()
        final_address = request.form.get('final_address_select')
        final_address_custom = request.form.get('final_address_custom', '').strip()
        extra_luggage = bool(request.form.get('extra_luggage'))
        pet = bool(request.form.get('pet'))

        surcharge = 0.0
        # Si seleccionó "Otro", usamos la dirección personalizada
        if final_address == "otro" and final_address_custom:
            final_address = final_address_custom
            # Costo adicional si la ciudad es Córdoba
            if sch.route == 'RC-CBA':
                km_price = price("KM_PRICE")
                surcharge += obtener_precio("cordoba", final_address, km_price)
        
        if pickup_address == "otro" and pickup_address_custom:
            pickup_address = pickup_address_custom
            # Costo adicional si la ciudad es Córdoba
            if sch.route == 'CBA-RC':
                km_price = price("KM_PRICE")
                surcharge += obtener_precio("cordoba", pickup_address, km_price)

        base = price("BASE_SHARED_AIRPORT")
        subtotal = base * passengers
        extras = 0.0
        if extra_luggage: extras += price('EXTRA_LUGGAGE', 2000.0)
        if pet: extras += price('PET', 10000.0)
        total = subtotal + extras + surcharge

        booking = SharedBooking(
            schedule_id=sch.id,
            passengers=passengers,
            name=name,
            phone=phone,
            email=email,
            pickup_address=pickup_address,
            final_address=final_address,
            extra_luggage=extra_luggage,
            pet=pet,
            total_price=total
        )
        db.session.add(booking)
        db.session.commit()

        return render_template('confirm.html', category='Viaje Compartido', total=total, details={
            'Ruta': 'Río Cuarto → Córdoba' if sch.route=='RC-CBA' else 'Córdoba → Río Cuarto',
            'Fecha': sch.date.isoformat(),
            'Hora': sch.time,
            'Pasajeros': passengers,
            'Dirección de retiro': pickup_address,
            'Dirección de llegada': final_address,
            'Valija extra': 'Sí' if extra_luggage else 'No',
            'Mascota': 'Sí' if pet else 'No',
            'Costo adicional': f"${surcharge:.0f}" if surcharge else "Ninguno"
        })

    # GET
    tomorrow = date.today() + dtime(days=1)
    return render_template('airport_book.html', sch=sch, passengers=passengers, free=free,
                           llegada_options=llegada_options, retiro_options=retiro_options,
                           tomorrow=tomorrow, config_price = price('EXTRA_LUGGAGE'),
                           config_pet = price('PET'))
# Parcels (Encomiendas)
@app.route('/parcels', methods=['GET', 'POST'])
def parcels():
    if request.method == 'POST':
        route = request.form.get('route')
        date_str = request.form.get('date')
        parcels_n = min(2, max(1, int(request.form.get('parcels', 1))))  # max 2 per booking
        name = request.form.get('name')
        phone = request.form.get('phone')
        email = request.form.get('email')
        pickup_address = request.form.get('pickup_address', '').strip()
        final_address = request.form.get('final_address', '').strip()

        try:
            on_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Fecha inválida', 'error')
            return redirect(url_for('parcels'))

        # Validación: no permitir fechas pasadas
        if on_date < date.today():
            flash('La fecha no puede ser anterior a hoy.', 'error')
            return redirect(url_for('parcels'))

        # Simple pricing: half of base shared per parcel as a demo
        base = price('BASE_SHARED_RC_CBA' if route=='RC-CBA' else 'BASE_SHARED_CBA_RC', 9000.0)
        total = (base * 0.5) * parcels_n
        booking = ParcelBooking(
            route=route,
            date=on_date,
            parcels=parcels_n,
            name=name,
            phone=phone,
            email=email,
            pickup_address=pickup_address,
            final_address=final_address,
            total_price=total
        )
        db.session.add(booking)
        db.session.commit()
        return render_template('confirm.html', category='Encomienda', total=total, details={
            'Ruta': 'Río Cuarto → Córdoba' if route=='RC-CBA' else 'Córdoba → Río Cuarto',
            'Fecha': on_date.isoformat(),
            'Bultos (máx 2 x reserva)': parcels_n,
            'Peso por bulto': '5 kg (máx)',
            'Dirección de retiro': pickup_address or 'No indicada',
            'Dirección de entrega': final_address or 'No indicada'
        })
    # GET: pasar fecha mínima al template
    return render_template('parcels.html', today=date.today().isoformat())

# Airport exclusive
@app.route('/airport', methods=['GET', 'POST'])
def airport():
    if request.method == 'POST':
        date_str = request.form.get('date')
        time_str = request.form.get('time')
        name = request.form.get('name')
        phone = request.form.get('phone')
        email = request.form.get('email')
        pickup_address = request.form.get('pickup_address')
        final_address = request.form.get('final_address')

        # Validar fecha
        try:
            on_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Fecha inválida', 'error')
            return render_template('airport.html', today=date.today().isoformat(), hour="00:00", form_data=request.form)

        # Validar hora
        try:
            input_time = datetime.strptime(time_str, '%H:%M').time()
        except Exception:
            flash('Hora inválida', 'error')
            return render_template('airport.html', today=date.today().isoformat(), hour="00:00", form_data=request.form)

        # Validación hora mínima si es hoy
        if on_date == date.today():
            limit = (datetime.now() + dtime(hours=2)).time()
            if input_time < limit:
                flash(f"La hora mínima para hoy es {limit.strftime('%H:%M')}", 'error')
                return render_template(
                    'airport.html',
                    today=date.today().isoformat(),
                    hour=f"{limit.hour:02d}:{limit.minute:02d}",
                    form_data=request.form
                )

        # Calcular precio
        total = price('AIRPORT_EXCLUSIVE', 60000.0)

        # Guardar reserva
        b = AirportExclusive(
            date=on_date,
            time=time_str,
            name=name,
            phone=phone,
            email=email,
            pickup_address=pickup_address,
            final_address=final_address,
            total_price=total
        )
        db.session.add(b)
        db.session.commit()

        return render_template('confirm.html', category='Aeropuerto Exclusivo', total=total, details={
            'Fecha': on_date.isoformat(),
            'Hora': time_str,
            'Dirección de retiro': pickup_address,
            'Dirección de llegada': final_address
        })

    # GET inicial
    today = date.today()
    now = datetime.now()
    hour_limit = now + dtime(hours=2)
    if hour_limit.hour >= 23:
        hour_str = "23:59"
    else:
        hour_str = f"{hour_limit.hour:02d}:{hour_limit.minute:02d}"

    return render_template('airport.html', today=today.isoformat(), hour=hour_str, form_data={})

# City exclusive RC<->CBA
@app.route('/exclusive', methods=['GET', 'POST'])
def exclusive():
    if request.method == 'POST':
        route = request.form.get('route')
        date_str = request.form.get('date')
        time_str = request.form.get('time')
        name = request.form.get('name')
        phone = request.form.get('phone')
        email = request.form.get('email')
        pickup_address = request.form.get('pickup_address')
        final_address = request.form.get('final_address')

        # Validar fecha
        try:
            on_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Fecha inválida', 'error')
            return render_template('exclusive.html', today=date.today().isoformat(), hour="00:00", form_data=request.form)

        # Validar hora
        try:
            input_time = datetime.strptime(time_str, '%H:%M').time()
        except Exception:
            flash('Hora inválida', 'error')
            return render_template('exclusive.html', today=date.today().isoformat(), hour="00:00", form_data=request.form)

        # Si la fecha es hoy, hora mínima = ahora + 2h
        if on_date == date.today():
            limit = (datetime.now() + dtime(hours=2)).time()
            if input_time < limit:
                flash(f"La hora mínima para hoy es {limit.strftime('%H:%M')}", 'error')
                return render_template(
                    'exclusive.html',
                    today=date.today().isoformat(),
                    hour=f"{limit.hour:02d}:{limit.minute:02d}",
                    form_data=request.form
                )

        # Calcular precio
        total = price('CITY_EXCLUSIVE_RC_CBA' if route=='RC-CBA' else 'CITY_EXCLUSIVE_CBA_RC', 45000.0)

        # Guardar reserva
        b = CityExclusive(
            route=route,
            date=on_date,
            time=time_str,
            name=name,
            phone=phone,
            email=email,
            pickup_address=pickup_address,
            final_address=final_address,
            total_price=total
        )
        db.session.add(b)
        db.session.commit()

        return render_template('confirm.html', category='Viaje Exclusivo', total=total, details={
            'Ruta': 'Río Cuarto → Córdoba' if route=='RC-CBA' else 'Córdoba → Río Cuarto',
            'Fecha': on_date.isoformat(),
            'Hora': time_str,
            'Dirección de retiro': pickup_address,
            'Dirección de llegada': final_address
        })

    # GET inicial
    today = date.today()
    now = datetime.now()
    hour_limit = now + dtime(hours=2)
    if hour_limit.hour >= 23:
        hour_str = "23:59"
    else:
        hour_str = f"{hour_limit.hour:02d}:{hour_limit.minute:02d}"

    return render_template('exclusive.html', today=today.isoformat(), hour=hour_str, form_data={})

# Anywhere in Argentina (demo km input / API placeholder)
@app.route('/anywhere', methods=['GET', 'POST'])
def anywhere():
    if request.method == 'POST':

        km_price = price("KM_PRICE")

        date_str = request.form.get('date')
        time_str = request.form.get('time')
        origin_city = request.form.get('origin')
        destination_city = request.form.get('destination')
        origin_street = request.form.get('origin_street')
        destination_street = request.form.get('destination_street')
        name = request.form.get('name')
        phone = request.form.get('phone')
        email = request.form.get('email')

        try:
            on_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Fecha inválida', 'error')
            return render_template('anywhere.html', today=date.today().isoformat(), hour="00:00", form_data=request.form)

        try:
            input_time = datetime.strptime(time_str, '%H:%M').time()
        except Exception:
            flash('Hora inválida', 'error')
            return render_template('anywhere.html', today=date.today().isoformat(), hour="00:00", form_data=request.form)

        # Validación hora mínima si es hoy
        if on_date == date.today():
            limit = (datetime.now() + dtime(hours=2)).time()
            if input_time < limit:
                flash(f"La hora mínima para hoy es {limit.strftime('%H:%M')}", 'error')
                return render_template(
                    'anywhere.html',
                    today=date.today().isoformat(),
                    hour=f"{limit.hour:02d}:{limit.minute:02d}",
                    form_data=request.form
                )

        # Calcular precio
        total, km = obtener_precio_larga_distancia(
            origin_city, origin_street, destination_city, destination_street, km_price
        )

        # Guardar reserva
        b = AnywhereBooking(
            date=on_date,
            time=time_str,
            origin_city=origin_city,
            destination_city=destination_city,
            origin_street=origin_street,
            destination_street=destination_street,
            km_estimate=km,
            name=name,
            phone=phone,
            email=email,
            total_price=total
        )
        db.session.add(b)
        db.session.commit()

        return render_template('confirm.html', category='Viaje a cualquier destino', total=total, details={
            'Fecha': on_date.isoformat(),
            'Hora': time_str,
            'Ciudad de origen': origin_city,
            'Calle de origen': origin_street,
            'Ciudad de destino': destination_city,
            'Calle de destino': destination_street,
            'Distancia recorrida en km': km
        })

    # GET inicial
    today = date.today()
    now = datetime.now()
    hour_limit = now + dtime(hours=2)
    if hour_limit.hour >= 23:
        hour_str = "23:59"
    else:
        hour_str = f"{hour_limit.hour:02d}:{hour_limit.minute:02d}"

    return render_template('anywhere.html', today=today.isoformat(), hour=hour_str, form_data={})

@app.route('/airport_shared', methods=['GET', 'POST'])
def airport_shared():
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
        # List availability: si la fecha es hoy filtrar horarios pasados
        if on_date == date.today():
            now = now_hhmm()
            schedules = TripSchedule.query.filter_by(route=route, date=on_date).filter(TripSchedule.time >= now).order_by(TripSchedule.time.asc()).all()
        else:
            schedules = TripSchedule.query.filter_by(route=route, date=on_date).order_by(TripSchedule.time.asc()).all()

        availability = []
        for s in schedules:
            taken = booked_seats(s.id)
            free = max(0, s.capacity - taken)
            availability.append((s, free, free >= passengers))
        return render_template('airport_slots.html', route=route, on_date=on_date, passengers=passengers, availability=availability)

    # GET inicial
    today = date.today() + dtime(days=1)
    return render_template('airport_shared.html', today=today.isoformat(), form_data={})

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

    keys = ['BASE_SHARED_RC_CBA','BASE_SHARED_CBA_RC','BASE_SHARED_AIRPORT' , 'AIRPORT_EXCLUSIVE',
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

    today = date.today()

    # Nuevo: asegurar que existan slots para los próximos 7 días (ambas rutas)
    for i in range(0, 7):
        d = today + dtime(days=i)
        ensure_day_slots('RC-CBA', d)
        ensure_day_slots('CBA-RC', d)

    # obtener hora actual en formato 'HH:MM' para comparar con TripSchedule.time
    now = datetime.now().strftime('%H:%M')
    # Mostrar todos los horarios de días futuros y, para el día actual, solo horarios >= hora actual
    scheds = TripSchedule.query.filter(
        (TripSchedule.date > today) |
        ((TripSchedule.date == today) & (TripSchedule.time >= now))
    ).order_by(TripSchedule.date.asc(), TripSchedule.route.asc(), TripSchedule.time.asc()).all()

    # Agrupar por día y dentro de cada día por ruta (ordenado)
    from collections import OrderedDict
    grouped = []
    for s in scheds:
        if not grouped or grouped[-1]['date'] != s.date:
            grouped.append({'date': s.date, 'routes': OrderedDict()})
        routes = grouped[-1]['routes']
        if s.route not in routes:
            routes[s.route] = []
        routes[s.route].append(s)

    # convertir OrderedDict a lista de {route, schedules}
    for g in grouped:
        g['routes'] = [{'route': r, 'schedules': sl} for r, sl in g['routes'].items()]

    return render_template('admin_schedules.html', grouped_schedules=grouped, booked_seats=booked_seats)

@app.route('/admin/bookings')
@login_required
def admin_bookings():
    shared = SharedBooking.query.order_by(SharedBooking.created_at.desc()).all()
    parcels = ParcelBooking.query.order_by(ParcelBooking.created_at.desc()).all()
    airport = AirportExclusive.query.order_by(AirportExclusive.created_at.desc()).all()
    exclusive = CityExclusive.query.order_by(CityExclusive.created_at.desc()).all()
    anywhere = AnywhereBooking.query.order_by(AnywhereBooking.created_at.desc()).all()
    return render_template('admin_bookings.html', shared=shared, parcels=parcels, airport=airport, exclusive=exclusive, anywhere=anywhere)

@app.route('/admin/delete_booking', methods=['POST'])
@login_required
def admin_delete_booking():
    btype = request.form.get('type')
    bid = request.form.get('id')
    if not btype or not bid:
        flash('Parámetros inválidos', 'error')
        return redirect(url_for('admin_bookings'))

    mapping = {
        'shared': SharedBooking,
        'parcels': ParcelBooking,
        'airport': AirportExclusive,
        'exclusive': CityExclusive,
        'anywhere': AnywhereBooking
    }

    Model = mapping.get(btype)
    if not Model:
        flash('Tipo de reserva inválido', 'error')
        return redirect(url_for('admin_bookings'))

    obj = Model.query.get(bid)
    if not obj:
        flash('Reserva no encontrada', 'error')
        return redirect(url_for('admin_bookings'))

    try:
        db.session.delete(obj)
        db.session.commit()
        flash('Reserva eliminada', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error al eliminar la reserva', 'error')

    return redirect(url_for('admin_bookings'))

@app.route('/admin/delete_schedule', methods=['POST'])
@login_required
def admin_delete_schedule():
    sched_id = request.form.get('id')
    if not sched_id:
        flash('ID de horario inválido', 'error')
        return redirect(url_for('admin_schedules'))

    try:
        sched = TripSchedule.query.get(int(sched_id))
    except Exception:
        sched = None

    if not sched:
        flash('Horario no encontrado', 'error')
        return redirect(url_for('admin_schedules'))

    try:
        db.session.delete(sched)
        db.session.commit()
        flash('Horario eliminado correctamente', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error al eliminar horario', 'error')

    return redirect(url_for('admin_schedules'))

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
