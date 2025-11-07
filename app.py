from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, time, timedelta, date
from dateutil.relativedelta import relativedelta
import os
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change_this_secret_key_in_prod'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///salon.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# -------------------
# Models
# -------------------
class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    service = db.Column(db.String(50), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    price = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='Pending')  # Pending, Approved, Rejected, Paid
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'phone': self.phone,
            'service': self.service,
            'date': self.date.isoformat(),
            'start_time': self.start_time.strftime("%H:%M"),
            'end_time': self.end_time.strftime("%H:%M"),
            'price': self.price,
            'status': self.status
        }

# -------------------
# Salon settings
# -------------------
OPEN_TIME = time(hour=9, minute=0)
CLOSE_TIME = time(hour=18, minute=30)
BREAKS = [
    (time(hour=11, minute=0), time(hour=11, minute=30)),
    (time(hour=13, minute=0), time(hour=14, minute=0)),
]
# service durations in minutes
SERVICES = {
    'Hair cutting': 20,
    'Trimming': 10,
    'Hair cutting + Trimming': 30
}
PRICES = {
    'Hair cutting': 120,
    'Trimming': 50,
    'Hair cutting + Trimming': 150,
    # 6 special styles use 200
}
SPECIAL_STYLES = ['Pompadour', 'Fade haircut styles', 'Classic side part', 'Drop fade', 'High fade', 'Undercut']
SPECIAL_PRICE = 200

# buffer between appointments in minutes
BUFFER_MIN = 5

# -------------------
# Utilities
# -------------------
def time_add(t: time, minutes: int) -> time:
    dt = datetime.combine(date.today(), t) + timedelta(minutes=minutes)
    return dt.time()

def overlaps(start1, end1, start2, end2):
    return not (end1 <= start2 or end2 <= start1)

def is_in_breaks(start: time, end: time):
    for bstart, bend in BREAKS:
        if overlaps(start, end, bstart, bend):
            return True
    return False

def get_existing_reservations_for_date(d: date):
    return Appointment.query.filter_by(date=d).all()

def available_slots_for_date(d: date, service_name: str):
    """Return list of available start times as strings HH:MM"""
    duration = SERVICES.get(service_name, 0)
    # special styles are treated as Hair cutting with SPECIAL_PRICE if service_name == 'Haircut_style' - handled in booking
    slots = []
    # start from OPEN_TIME to last possible start
    # compute latest start so appointment ends <= CLOSE_TIME
    earliest = datetime.combine(d, OPEN_TIME)
    latest_start_dt = datetime.combine(d, CLOSE_TIME) - timedelta(minutes=duration)
    # candidate start in 5-minute increments
    current = earliest
    existing = get_existing_reservations_for_date(d)
    # prepare extended intervals for existing (including buffer after each)
    existing_intervals = []
    for ap in existing:
        s = datetime.combine(d, ap.start_time)
        e = datetime.combine(d, ap.end_time) + timedelta(minutes=BUFFER_MIN)  # buffer after
        existing_intervals.append((s.time(), e.time()))
    while current <= latest_start_dt:
        start_t = current.time()
        end_t = time_add(start_t, duration)
        # check breaks
        if is_in_breaks(start_t, end_t):
            current += timedelta(minutes=5)
            continue
        # check within open/close
        if start_t < OPEN_TIME or end_t > CLOSE_TIME:
            current += timedelta(minutes=5)
            continue
        # check overlap with existing
        conflict = False
        for es, ee in existing_intervals:
            if overlaps(start_t, end_t, es, ee):
                conflict = True
                break
        if not conflict:
            slots.append(start_t.strftime("%H:%M"))
        current += timedelta(minutes=5)
    return slots

def ensure_db():
    if not os.path.exists('salon.db'):
        db.create_all()

# -------------------
# Routes
# -------------------
@app.route('/')
def index():
    # show homepage with special styles
    return render_template('index.html', special_styles=SPECIAL_STYLES)

@app.route('/book')
def book():
    # booking page
    return render_template('book.html', services=list(SERVICES.keys()), special_styles=SPECIAL_STYLES, prices=PRICES, special_price=SPECIAL_PRICE)

@app.route('/slots', methods=['POST'])
def slots():
    data = request.json
    date_str = data.get('date')
    service = data.get('service')
    # parse date
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return jsonify({'error': 'Invalid date'}), 400
    # handle special style mapping: if service starts with 'style:' treat as Hair cutting duration but price SPECIAL_PRICE
    if service and service.startswith('style:'):
        service_name = 'Hair cutting'
    else:
        service_name = service
    slots = available_slots_for_date(d, service_name)
    return jsonify({'slots': slots})

@app.route('/confirm', methods=['POST'])
def confirm():
    # create appointment (status Pending) and return flash card HTML and show owner's QR client side
    form = request.form
    username = form.get('username', '').strip()
    phone = form.get('phone', '').strip()
    service = form.get('service')
    date_str = form.get('date')
    start_time_str = form.get('slot')
    if not (username and phone and service and date_str and start_time_str):
        flash("Missing information", "danger")
        return redirect(url_for('book'))
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    start_time = datetime.strptime(start_time_str, "%H:%M").time()
    # determine duration and price
    if service.startswith('style:'):
        # e.g. style:pompadour
        duration = SERVICES['Hair cutting']
        price = SPECIAL_PRICE
        service_readable = service.split(':',1)[1]
    else:
        duration = SERVICES.get(service, 0)
        price = PRICES.get(service, 0)
        service_readable = service
    # fallback price if missing
    if price == 0:
        price = PRICES.get(service_readable, SPECIAL_PRICE)
    end_time = time_add(start_time, duration)
    # create appointment record
    ap = Appointment(
        username=username,
        phone=phone,
        service=service_readable,
        date=d,
        start_time=start_time,
        end_time=end_time,
        price=price,
        status='Pending'
    )
    db.session.add(ap)
    db.session.commit()
    # return pop-up card HTML to embed on client
    ap_html = render_template('flash_card.html', ap=ap)
    # IMPORTANT: booking must update admin panel immediately (it is saved in DB). The UI will display owner's QR client side.
    return jsonify({'success': True, 'html': ap_html, 'owner_qr': url_for('static', filename='images/owner_qr.png')})

@app.route('/download/appointment/<int:ap_id>')
def download_appointment(ap_id):
    ap = Appointment.query.get_or_404(ap_id)
    # create PDF in memory
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    text = c.beginText(40, 800)
    text.setFont("Helvetica-Bold", 14)
    text.textLine("Bright Look T In J Saloon")
    text.setFont("Helvetica", 11)
    text.textLine("")
    text.textLine(f"Appointment Detail")
    text.textLine("")
    text.textLine(f"Name: {ap.username}")
    text.textLine(f"Phone: {ap.phone}")
    text.textLine(f"Service: {ap.service}")
    text.textLine(f"Date: {ap.date.strftime('%Y-%m-%d')}")
    text.textLine(f"Time: {ap.start_time.strftime('%H:%M')} - {ap.end_time.strftime('%H:%M')}")
    text.textLine(f"Price: Rs.{ap.price} Only!")
    text.textLine(f"Status: {ap.status}")
    text.textLine("")
    text.textLine("Thank you for choosing Bright Look!")
    c.drawText(text)
    c.showPage()
    c.save()
    buf.seek(0)
    filename = f"{ap.username}_appointment.pdf"
    return send_file(buf, download_name=filename, as_attachment=True, mimetype='application/pdf')

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        # default creds admin / 12345678, but we will allow storing custom in session for runtime (not persistent)
        saved_user = session.get('admin_user', 'admin')
        saved_pass = session.get('admin_pass', '12345678')
        if username == saved_user and password == saved_pass:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash("Invalid credentials", "danger")
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/reset_credentials', methods=['POST'])
def admin_reset_credentials():
    # allow admin to change runtime username/password (not persistent beyond session)
    new_user = request.form.get('new_user')
    new_pass = request.form.get('new_pass')
    if not new_user or not new_pass:
        flash("Both fields required", "warning")
    else:
        session['admin_user'] = new_user
        session['admin_pass'] = new_pass
        flash("Admin credentials updated for this session", "success")
    return redirect(url_for('admin_dashboard'))

def login_required(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return func(*args, **kwargs)
    return wrapper

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    # show all bookings and stats
    # show day/week/month filters via query param
    period = request.args.get('period', 'day')  # day/week/month
    today = date.today()
    if period == 'day':
        start = today
        end = today
    elif period == 'week':
        start = today - timedelta(days=today.weekday())  # monday
        end = start + timedelta(days=6)
    elif period == 'month':
        start = today.replace(day=1)
        end = (start + relativedelta(months=1)) - timedelta(days=1)
    else:
        start = today
        end = today
    appts = Appointment.query.order_by(Appointment.date.desc(), Appointment.start_time).all()
    # stats: count and total collection (Paid)
    total_collection = db.session.query(db.func.sum(Appointment.price)).filter(
        Appointment.status == 'Paid',
        Appointment.date >= start,
        Appointment.date <= end
    ).scalar() or 0
    # filtered appts for period
    appts_filtered = [a for a in appts if start <= a.date <= end]
    return render_template('admin_dashboard.html', appts=appts, appts_filtered=appts_filtered, period=period, total_collection=total_collection)

@app.route('/admin/approve/<int:ap_id>', methods=['POST'])
@login_required
def admin_approve(ap_id):
    ap = Appointment.query.get_or_404(ap_id)
    ap.status = 'Approved'
    db.session.commit()
    flash("Appointment approved", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject/<int:ap_id>', methods=['POST'])
@login_required
def admin_reject(ap_id):
    ap = Appointment.query.get_or_404(ap_id)
    ap.status = 'Rejected'
    db.session.commit()
    flash("Appointment rejected", "warning")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/mark_paid/<int:ap_id>', methods=['POST'])
@login_required
def admin_mark_paid(ap_id):
    ap = Appointment.query.get_or_404(ap_id)
    ap.status = 'Paid'
    db.session.commit()
    # AFTER marking paid create receipt PDF and return it
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    text = c.beginText(40, 800)
    text.setFont("Helvetica-Bold", 14)
    text.textLine("Bright Look T In J Saloon - Receipt")
    text.setFont("Helvetica", 11)
    text.textLine("")
    text.textLine(f"Receipt for: {ap.username}")
    text.textLine(f"Phone: {ap.phone}")
    text.textLine(f"Service: {ap.service}")
    text.textLine(f"Date: {ap.date.strftime('%Y-%m-%d')}")
    text.textLine(f"Time: {ap.start_time.strftime('%H:%M')} - {ap.end_time.strftime('%H:%M')}")
    text.textLine(f"Amount Paid: Rs.{ap.price} Only!")
    text.textLine("")
    text.textLine("Thank you! - Owner")
    c.drawText(text)
    c.showPage()
    c.save()
    buf.seek(0)
    filename = f"{ap.username}_receipt.pdf"
    return send_file(buf, download_name=filename, as_attachment=True, mimetype='application/pdf')

# simple API to get appointment details (for admin UI popups)
@app.route('/appointment/<int:ap_id>')
@login_required
def appointment_detail(ap_id):
    ap = Appointment.query.get_or_404(ap_id)
    return jsonify(ap.to_dict())

# -------------------
# Start
# -------------------
def ensure_db():
    with app.app_context():
        db.create_all()

if __name__ == "__main__":
    ensure_db()
    app.run(debug=True)

