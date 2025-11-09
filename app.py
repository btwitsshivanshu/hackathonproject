from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, date
import re  

app = Flask(__name__)
app.secret_key = "super_secret_key"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(300), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    specialty = db.Column(db.String(100))
    availability = db.Column(db.Boolean, default=True)
    available_time = db.Column(db.String(100), default="09:00–17:00")
    max_patients = db.Column(db.Integer, default=5)
    avg_consult_time = db.Column(db.Integer, default=15)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_number = db.Column(db.Integer, nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    doctor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    date = db.Column(db.String(20))
    status = db.Column(db.String(50), default="Pending")
    prescription = db.Column(db.Text)
    pharmacy_status = db.Column(db.String(50), default="Not Processed")
    estimated_time = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship("User", foreign_keys=[patient_id])
    doctor = db.relationship("User", foreign_keys=[doctor_id])


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


with app.app_context():
    db.create_all()


def calculate_estimated_time(doctor, queue_position):
    """Estimate appointment time based on doctor's available_time."""
    if not doctor.available_time:
        return "N/A"
    try:
        start_time_str = doctor.available_time.split("–")[0].strip()
        start_dt = datetime.strptime(start_time_str, "%H:%M")
        estimated_dt = start_dt + timedelta(minutes=(queue_position - 1) * doctor.avg_consult_time)
        return estimated_dt.strftime("%I:%M %p")
    except Exception:
        return "N/A"


def reindex_appointments(doctor_id, date):
    """Reassign appointment numbers after completion."""
    appointments = Appointment.query.filter_by(
        doctor_id=doctor_id, date=date, status="Pending"
    ).order_by(Appointment.created_at).all()
    for i, appt in enumerate(appointments, start=1):
        appt.appointment_number = i
        appt.estimated_time = calculate_estimated_time(appt.doctor, i)
    db.session.commit()


def valid_email(email):
    """Check if email is in valid format."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None


def is_date_available(doctor, selected_date):
    """
    Returns True only if the selected date is today or in the future,
    and within doctor's available working hours if today.
    """
    today = date.today()
    selected = datetime.strptime(selected_date, "%Y-%m-%d").date()

    if selected < today:
        return False

    if selected == today:
        try:
            end_time_str = doctor.available_time.split("–")[-1].strip()
            end_time = datetime.strptime(end_time_str, "%H:%M").time()
            now_time = datetime.now().time()
            return now_time < end_time
        except Exception:
            return True
    return True

@app.route("/")
def index():
    if current_user.is_authenticated:
        if current_user.role == "patient":
            return redirect(url_for("patient_dashboard"))
        elif current_user.role == "doctor":
            return redirect(url_for("doctor_dashboard"))
        elif current_user.role == "pharmacist":
            return redirect(url_for("pharmacist_dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"].lower()
        password = request.form["password"]
        role = request.form["role"].lower()
        specialty = request.form.get("specialty") if role == "doctor" else None

        # --- EMAIL VALIDATION ---
        if not valid_email(email):
            flash("Please enter a valid email address.", "flash-danger")
            return redirect(url_for("register"))

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "flash-danger")
            return redirect(url_for("register"))

        hashed_pw = generate_password_hash(password)
        user = User(name=name, email=email, password_hash=hashed_pw, role=role, specialty=specialty)
        db.session.add(user)
        db.session.commit()
        flash("Registration successful. Please login.", "flash-success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].lower()
        password = request.form["password"]
        role = request.form["role"].lower()

        # --- EMAIL VALIDATION ---
        if not valid_email(email):
            flash("Invalid email format.", "flash-danger")
            return redirect(url_for("login"))

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password) or user.role != role:
            flash("Invalid credentials or role mismatch", "flash-danger")
            return redirect(url_for("login"))

        login_user(user)
        if user.role == "patient":
            return redirect(url_for("patient_dashboard"))
        elif user.role == "doctor":
            return redirect(url_for("doctor_dashboard"))
        elif user.role == "pharmacist":
            return redirect(url_for("pharmacist_dashboard"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully", "flash-info")
    return redirect(url_for("login"))

@app.route("/patient_dashboard", methods=["GET", "POST"])
@login_required
def patient_dashboard():
    if current_user.role != "patient":
        return redirect(url_for("login"))

    if request.method == "POST":
        doctor_id = request.form["doctor_id"]
        date_selected = request.form["date"]

        doctor = User.query.get(doctor_id)
        if not doctor:
            flash("Doctor not found.", "flash-danger")
            return redirect(url_for("patient_dashboard"))

        if not is_date_available(doctor, date_selected):
            flash("You cannot book this date. The selected date/time has passed.", "flash-danger")
            return redirect(url_for("patient_dashboard"))

        existing = Appointment.query.filter_by(
            patient_id=current_user.id, doctor_id=doctor_id, date=date_selected
        ).first()
        if existing:
            flash("You already have an appointment with this doctor that day.", "flash-info")
            return redirect(url_for("patient_dashboard"))

        count = Appointment.query.filter_by(
            doctor_id=doctor_id, date=date_selected, status="Pending"
        ).count()
        if count >= doctor.max_patients:
            flash("Doctor's schedule is full for that day.", "flash-danger")
            return redirect(url_for("patient_dashboard"))

        queue_position = count + 1
        estimated_time = calculate_estimated_time(doctor, queue_position)

        appointment = Appointment(
            appointment_number=queue_position,
            patient_id=current_user.id,
            doctor_id=doctor_id,
            date=date_selected,
            estimated_time=estimated_time,
        )
        db.session.add(appointment)
        db.session.commit()
        flash("Appointment booked successfully!", "flash-success")
        return redirect(url_for("patient_dashboard"))

    doctors = User.query.filter_by(role="doctor", availability=True).all()
    appointments = Appointment.query.filter_by(
        patient_id=current_user.id
    ).order_by(Appointment.date.desc()).all()
    return render_template("patient_dashboard.html", doctors=doctors, appointments=appointments, datetime=datetime)

@app.route("/doctor_dashboard", methods=["GET", "POST"])
@login_required
def doctor_dashboard():
    if current_user.role != "doctor":
        return redirect(url_for("login"))

    if request.method == "POST":
        if "send_prescription" in request.form:
            appointment_id = request.form["appointment_id"]
            prescription = request.form["prescription"]
            appointment = Appointment.query.get(appointment_id)
            if appointment:
                appointment.prescription = prescription
                appointment.status = "Completed"
                db.session.commit()
                reindex_appointments(current_user.id, appointment.date)
                flash("Prescription sent to pharmacist.", "flash-success")

    appointments = Appointment.query.filter_by(
        doctor_id=current_user.id, status="Pending"
    ).order_by(Appointment.appointment_number).all()
    return render_template("doctor_dashboard.html", appointments=appointments)

@app.route("/pharmacist_dashboard", methods=["GET", "POST"])
@login_required
def pharmacist_dashboard():
    if current_user.role != "pharmacist":
        return redirect(url_for("login"))

    if request.method == "POST":
        appointment_id = request.form["appointment_id"]
        status = request.form["status"]
        appointment = Appointment.query.get(appointment_id)
        if appointment:
            appointment.pharmacy_status = status
            db.session.commit()
            flash("Status updated.", "flash-success")

    prescriptions = Appointment.query.filter(
        Appointment.prescription.isnot(None)
    ).order_by(Appointment.date.desc()).all()
    return render_template("pharmacist_dashboard.html", prescriptions=prescriptions)

# Voice features are optional. Wrap imports so app still runs if audio
# libraries or system modules (like `aifc`) are missing on this Python.
voice_enabled = False
try:
    import speech_recognition as sr
    import pyttsx3
    import threading
    import queue

    voice_enabled = True
except Exception as _e:
    # Keep names defined so other code can import this module.
    # We'll disable voice functionality below and fall back to no-op.
    print("Voice modules unavailable or failed to import:", _e)
    sr = None
    pyttsx3 = None
    import threading
    import queue


if voice_enabled:
    engine = pyttsx3.init()
    engine.setProperty("rate", 175)
    speech_queue = queue.Queue()

    def tts_worker():
        """Continuously process speech queue in a single thread"""
        while True:
            text = speech_queue.get()
            if text is None:
                break
            try:
                engine.say(text)
                engine.runAndWait()
            except Exception as e:
                print("TTS error:", e)
            speech_queue.task_done()

    threading.Thread(target=tts_worker, daemon=True).start()

    def speak(text):
        """Thread-safe text-to-speech call"""
        speech_queue.put(text)
else:
    # Fallback speak implementation when voice is disabled
    def speak(text):
        # Print to console so there's still feedback in logs
        print("[speak disabled]", text)


from difflib import SequenceMatcher

from difflib import SequenceMatcher

from difflib import SequenceMatcher

from difflib import SequenceMatcher

@app.route("/voice_book", methods=["GET"])
@login_required
def voice_book():
    """Voice appointment booking for blind patients (robust & context-safe)."""
    if current_user.role != "patient":
        flash("Only patients can use voice booking.", "flash-danger")
        return redirect(url_for("index"))

    # If voice libraries failed to import, disable the feature gracefully.
    if not voice_enabled:
        flash("Voice features are not available on this system.", "flash-danger")
        return redirect(url_for("patient_dashboard"))

    patient_id = current_user.id 

    def normalize_text(text):
        text = text.lower()
        for word in ["doctor", "dr.", "dr", "appointment", "book", "with", "for"]:
            text = text.replace(word, "")
        return "".join(ch for ch in text if ch.isalnum() or ch.isspace()).strip()

    def match_doctor_name(command):
        doctors = User.query.filter_by(role="doctor").all()
        command_clean = normalize_text(command)
        best_match = None
        highest_score = 0

        print("\n Voice matching logs:")
        for d in doctors:
            doc_clean = normalize_text(d.name)
            ratio = SequenceMatcher(None, doc_clean, command_clean).ratio()
            if d.name.lower().split()[0] in command_clean or d.name.lower().split()[-1] in command_clean:
                ratio += 0.4
            print(f"  - Comparing '{d.name}' → score {ratio:.2f}")
            if ratio > highest_score:
                highest_score = ratio
                best_match = d

        if best_match and highest_score >= 0.4:
            print(f"Matched doctor: {best_match.name} (score {highest_score:.2f})")
            return best_match
        print("No close doctor match found.")
        return None

    def recognize_and_book(patient_id):
        with app.app_context():
            r = sr.Recognizer()
            speak("Voice booking started. Please say the doctor's name and date.")

            while True:
                try:
                    with sr.Microphone() as source:
                        r.adjust_for_ambient_noise(source, duration=0.5)
                        print("Listening for booking command...")
                        audio = r.listen(source, phrase_time_limit=8)

                    try:
                        command = r.recognize_google(audio).lower()
                        print("Command:", command)
                    except sr.UnknownValueError:
                        speak("Sorry, I didn’t catch that. Please repeat.")
                        continue

                    # Exit condition
                    if any(word in command for word in ["stop", "exit", "cancel", "close"]):
                        speak("Voice booking stopped.")
                        break

                    # Doctor matching
                    matched_doctor = match_doctor_name(command)
                    if not matched_doctor:
                        speak("I couldn’t find that doctor in the system.")
                        continue

                    # Date parsing
                    today = datetime.today().date()
                    if "tomorrow" in command:
                        date_selected = today + timedelta(days=1)
                    elif "today" in command or "aaj" in command:
                        date_selected = today
                    else:
                        date_selected = today + timedelta(days=1)

                    # Check doctor’s capacity
                    count = Appointment.query.filter_by(
                        doctor_id=matched_doctor.id,
                        date=str(date_selected),
                        status="Pending"
                    ).count()

                    if count >= matched_doctor.max_patients:
                        speak("Doctor’s schedule is full for that day.")
                        continue

                    # Book appointment
                    queue_position = count + 1
                    estimated_time = calculate_estimated_time(matched_doctor, queue_position)

                    appointment = Appointment(
                        appointment_number=queue_position,
                        patient_id=patient_id,
                        doctor_id=matched_doctor.id,
                        date=str(date_selected),
                        estimated_time=estimated_time
                    )
                    db.session.add(appointment)
                    db.session.commit()

                    speak(f"Your appointment with Doctor {matched_doctor.name} on {date_selected.strftime('%A')} is booked.")
                    print(f"Appointment created for {matched_doctor.name} on {date_selected}")
                    break

                except Exception as e:
                    print("Voice error:", e)
                    continue

    # Start the background thread safely (no request context inside)
    threading.Thread(target=lambda: recognize_and_book(patient_id), daemon=True).start()

    flash("Voice booking activated. Please say the doctor's name and date clearly.", "flash-info")
    return redirect(url_for("patient_dashboard"))

if __name__ == "__main__":

    app.run(debug=True)






