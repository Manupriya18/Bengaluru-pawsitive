import os
import time
import random
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from geopy.geocoders import Nominatim
from flask_socketio import SocketIO, send
from jinja2 import DictLoader
from textblob import TextBlob
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

# --- Sentry Integration (optional) ---
sentry_sdk.init(
    dsn=os.environ.get('SENTRY_DSN', ''),  # Set SENTRY_DSN in your environment if using Sentry
    integrations=[FlaskIntegration()],
    traces_sample_rate=1.0
)

# --- Configuration ---
app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
DATABASE_URI = 'sqlite:///' + os.path.join(basedir, 'database.db')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "dev")
UPLOAD_FOLDER = os.path.join(basedir, 'static/uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- Logging Setup ---
if not os.path.exists('logs'):
    os.mkdir('logs')
file_handler = RotatingFileHandler('logs/strays.log', maxBytes=10240, backupCount=10)
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('Strays Bengaluru startup')

# --- Inject current year into templates (fixes datetime undefined error) ---
@app.context_processor
def inject_current_year():
    return dict(current_year=datetime.utcnow().year)

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
socketio = SocketIO(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Initialize geolocator
geolocator = Nominatim(user_agent="strays-bengaluru_app")

# --- Models ---
event_participants = db.Table(
    'event_participants',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id', name='fk_event_participants_user_id'), primary_key=True),
    db.Column('event_id', db.Integer, db.ForeignKey('event.id', name='fk_event_participants_event_id'), primary_key=True)
)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    role = db.Column(db.String(20), default='donor')
    points = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<User {self.username}>'

class Donation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    food_type = db.Column(db.String(50))
    quantity = db.Column(db.Integer)
    pickup_location = db.Column(db.String(200))
    pickup_time = db.Column(db.DateTime)
    donor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    donor = db.relationship('User', backref=db.backref('donations', lazy=True))
    pickup_latitude = db.Column(db.Float, nullable=True)
    pickup_longitude = db.Column(db.Float, nullable=True)

class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    animal_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(200), nullable=False)
    contact = db.Column(db.String(100), nullable=False)
    report_time = db.Column(db.DateTime(timezone=True), server_default=func.now())
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reporter = db.relationship('User', backref=db.backref('reports', lazy=True))
    image_filename = db.Column(db.String(100))
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300))
    event_time = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(200), nullable=False)
    participants = db.relationship('User', secondary=event_participants, backref=db.backref('events', lazy='dynamic'))

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    message = db.Column(db.String(500))
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('feedbacks', lazy=True))

# --- Helper Functions ---
def send_notification(subject, recipients, body):
    app.logger.info(f"Notification: {subject} -> {', '.join(recipients)}: {body}")

def post_to_twitter(message):
    app.logger.info(f"Twitter: {message}")

def check_proximity(report):
    app.logger.info(f"Proximity Alert: New report near you: {report.location}")

# --- Templates ---
# Using Bootstrap for a modern look and responsive design.
templates = {
    "base.html": '''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Strays Bengaluru – A Sanctuary for the Homeless</title>
  <!-- Bootstrap CSS -->
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <link rel="manifest" href="{{ url_for('manifest_json') }}">
  <!-- Leaflet CSS -->
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.3/dist/leaflet.css" crossorigin=""/>
  <style>
    body { background: #fdfdfd; }
    a { text-decoration: none !important; }
    footer { margin-top: 2rem; padding: 1rem 0; text-align: center; background: #f8f9fa; }
  </style>
  <script>
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', function() {
        navigator.serviceWorker.register('{{ url_for("sw_js_route") }}').then(function(registration) {
          console.log('ServiceWorker registered with scope:', registration.scope);
        }, function(err) {
          console.log('ServiceWorker registration failed:', err);
        });
      });
    }
  </script>
</head>
<body>
  <nav class="navbar navbar-expand-lg navbar-dark" style="background: linear-gradient(135deg, #2C3E50, #4CA1AF);">
    <div class="container">
      <a class="navbar-brand" href="{{ url_for('index') }}">Strays Bengaluru</a>
      <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
        <span class="navbar-toggler-icon"></span>
      </button>
      <div class="collapse navbar-collapse" id="navbarNav">
        <ul class="navbar-nav ms-auto">
          <li class="nav-item"><a class="nav-link" href="{{ url_for('index') }}">Home</a></li>
          {% if current_user.is_authenticated %}
            <li class="nav-item"><a class="nav-link" href="{{ url_for('dashboard') }}">Dashboard</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('map_view') }}">Reports Map</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('donation_map') }}">Donation Map</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('volunteer_chat') }}">Volunteer Chat</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('feedback') }}">Feedback</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('feedback_sentiment_unique') }}">Feedback Sentiment</a></li>
            {% if current_user.role == 'admin' %}
              <li class="nav-item"><a class="nav-link" href="{{ url_for('stats') }}">Analytics</a></li>
              <li class="nav-item"><a class="nav-link" href="{{ url_for('leaderboard') }}">Leaderboard</a></li>
            {% endif %}
            <li class="nav-item"><a class="nav-link" href="{{ url_for('logout') }}">Logout</a></li>
          {% else %}
            <li class="nav-item"><a class="nav-link" href="{{ url_for('login') }}">Login</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('register') }}">Register</a></li>
          {% endif %}
        </ul>
      </div>
    </div>
  </nav>
  <div class="container mt-4">
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for message in messages %}
          <div class="alert alert-success flash-message">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    {% block content %}{% endblock %}
  </div>
  <footer>
    <div class="container">
      <p>&copy; {{ current_year }} Strays Bengaluru. All rights reserved.</p>
    </div>
  </footer>
  <!-- Bootstrap JS Bundle -->
  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
''',

    "index.html": '''
{% extends "base.html" %}
{% block content %}
  <div class="text-center">
    <h1>Welcome to Strays Bengaluru – A Sanctuary for the Homeless</h1>
    <p class="lead">Our mission is to care for stray animals through generous donations and community volunteerism. Experience an elegant blend of technology and compassion.</p>
  </div>
{% endblock %}
''',

    "register.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Enlist Your Persona</h1>
  <form method="POST">
    <div class="mb-3">
      <label class="form-label">Username:</label>
      <input type="text" name="username" class="form-control" placeholder="Enter your distinguished name" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Password:</label>
      <input type="password" name="password" class="form-control" placeholder="Choose a secure password" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Email:</label>
      <input type="email" name="email" class="form-control" placeholder="you@example.com" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Role:</label>
      <select name="role" class="form-select">
        <option value="donor">Philanthropist (Donor)</option>
        <option value="volunteer">Volunteer</option>
        <option value="admin">Administrator</option>
      </select>
    </div>
    <button type="submit" class="btn btn-primary">Register Now</button>
  </form>
{% endblock %}
''',

    "login.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Access Portal</h1>
  <form method="POST">
    <div class="mb-3">
      <label class="form-label">Username:</label>
      <input type="text" name="username" class="form-control" placeholder="Your esteemed username" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Password:</label>
      <input type="password" name="password" class="form-control" placeholder="Your secret key" required>
    </div>
    <button type="submit" class="btn btn-primary">Enter</button>
  </form>
{% endblock %}
''',

    "dashboard.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Executive Dashboard</h1>
  <p>Greetings, {{ current_user.username }}! Explore your personalized control center.</p>
  <ul class="list-group">
    <li class="list-group-item"><a href="{{ url_for('profile') }}">View & Edit Profile</a></li>
    <li class="list-group-item"><a href="{{ url_for('add_donation') }}">Contribute a Donation</a></li>
    <li class="list-group-item"><a href="{{ url_for('donations') }}">Examine Donations</a></li>
    <li class="list-group-item"><a href="{{ url_for('report_animal') }}">Report a Stray Animal</a></li>
    <li class="list-group-item"><a href="{{ url_for('reports') }}">Review Stray Reports</a></li>
    <li class="list-group-item"><a href="{{ url_for('events') }}">Peruse Upcoming Events</a></li>
    {% if current_user.role == 'admin' %}
      <li class="list-group-item"><a href="{{ url_for('create_event') }}">Orchestrate an Event</a></li>
      <li class="list-group-item"><a href="{{ url_for('stats') }}">Analytics & Insights</a></li>
      <li class="list-group-item"><a href="{{ url_for('leaderboard') }}">Community Leaderboard</a></li>
    {% endif %}
  </ul>
{% endblock %}
''',

    "profile.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">My Profile</h1>
  <form method="POST">
    <div class="mb-3">
      <label class="form-label">Username:</label>
      <input type="text" name="username" class="form-control" value="{{ current_user.username }}" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Email:</label>
      <input type="email" name="email" class="form-control" value="{{ current_user.email }}" required>
    </div>
    <button type="submit" class="btn btn-primary">Update Profile</button>
  </form>
{% endblock %}
''',

    "add_donation.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Offer a Donation</h1>
  <form method="POST">
    <div class="mb-3">
      <label class="form-label">Description:</label>
      <input type="text" name="description" class="form-control" placeholder="Describe your generous contribution" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Food Type:</label>
      <input type="text" name="food_type" class="form-control" placeholder="E.g., Freshly cooked, packaged snacks">
    </div>
    <div class="mb-3">
      <label class="form-label">Quantity:</label>
      <input type="number" name="quantity" class="form-control" placeholder="E.g., 5">
    </div>
    <div class="mb-3">
      <label class="form-label">Pickup Location:</label>
      <input type="text" name="pickup_location" class="form-control" placeholder="Your preferred pickup address" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Pickup Time:</label>
      <input type="datetime-local" name="pickup_time" class="form-control" required>
    </div>
    <button type="submit" class="btn btn-primary">Donate Now</button>
  </form>
{% endblock %}
''',

    "donations.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Donations Archive</h1>
  <ul class="list-group">
    {% for donation in donations %}
      <li class="list-group-item">
        {{ donation.description }} - {{ donation.food_type }} ({{ donation.quantity }}) -
        {% if donation.donor_id == current_user.id %}contributed by you{% else %}contributed by {{ donation.donor.username }}{% endif %}
      </li>
    {% endfor %}
  </ul>
{% endblock %}
''',

    "report_animal.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Stray Animal Report</h1>
  <form method="POST" enctype="multipart/form-data">
    <div class="mb-3">
      <label class="form-label">Animal Type:</label>
      <input type="text" name="animal_type" class="form-control" placeholder="E.g., Cat, Dog" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Description:</label>
      <input type="text" name="description" class="form-control" placeholder="Describe the situation" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Location (address):</label>
      <input type="text" name="location" class="form-control" placeholder="Where did you see the animal?" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Contact Info:</label>
      <input type="text" name="contact" class="form-control" placeholder="Your contact details" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Image (optional):</label>
      <input type="file" name="image" class="form-control">
    </div>
    <button type="submit" class="btn btn-primary">Submit Report</button>
  </form>
{% endblock %}
''',

    "reports.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Stray Animal Reports Archive</h1>
  <ul class="list-group">
    {% for report in reports %}
      <li class="list-group-item">
        {{ report.animal_type }}: {{ report.description }} at {{ report.location }}
        (<a href="{{ url_for('report_details', report_id=report.id) }}" target="_blank">View Details</a>)
      </li>
    {% endfor %}
  </ul>
{% endblock %}
''',

    "events.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Upcoming Community Events</h1>
  <ul class="list-group">
    {% for event in events %}
      <li class="list-group-item">
        <strong>{{ event.title }}</strong> - {{ event.event_time.strftime('%Y-%m-%d %H:%M') }} at {{ event.location }}<br>
        {{ event.description }}<br>
        Participants: {{ event.participants|length }}<br>
        {% if current_user not in event.participants %}
          <form action="{{ url_for('signup_event', event_id=event.id) }}" method="POST" class="d-inline">
            <button type="submit" class="btn btn-sm btn-primary mt-1">Sign Up</button>
          </form>
        {% else %}
          <span class="badge bg-success">Signed Up</span>
        {% endif %}
      </li>
    {% endfor %}
  </ul>
{% endblock %}
''',

    "create_event.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Create a New Event</h1>
  <form method="POST">
    <div class="mb-3">
      <label class="form-label">Event Title:</label>
      <input type="text" name="title" class="form-control" placeholder="Enter event title" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Description:</label>
      <input type="text" name="description" class="form-control" placeholder="Describe the event">
    </div>
    <div class="mb-3">
      <label class="form-label">Event Time:</label>
      <input type="datetime-local" name="event_time" class="form-control" required>
    </div>
    <div class="mb-3">
      <label class="form-label">Location:</label>
      <input type="text" name="location" class="form-control" placeholder="Enter event venue" required>
    </div>
    <button type="submit" class="btn btn-primary">Create Event</button>
  </form>
{% endblock %}
''',

    "stats.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Analytics Dashboard</h1>
  <div class="row">
    <div class="col-md-6">
      <canvas id="donationChart" width="400" height="200"></canvas>
    </div>
    <div class="col-md-6">
      <canvas id="reportChart" width="400" height="200"></canvas>
    </div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script>
    document.addEventListener("DOMContentLoaded", function() {
      var donationCtx = document.getElementById('donationChart').getContext('2d');
      var reportCtx = document.getElementById('reportChart').getContext('2d');
      var donationData = {{ donation_data|tojson }};
      var reportData = {{ report_data|tojson }};
      new Chart(donationCtx, {
          type: 'bar',
          data: {
              labels: donationData.labels,
              datasets: [{
                  label: 'Donations per Month',
                  data: donationData.counts,
                  backgroundColor: 'rgba(44, 62, 80, 0.7)',
                  borderColor: 'rgba(44, 62, 80, 1)',
                  borderWidth: 1
              }]
          },
          options: { scales: { y: { beginAtZero: true } } }
      });
      new Chart(reportCtx, {
          type: 'bar',
          data: {
              labels: reportData.labels,
              datasets: [{
                  label: 'Reports per Month',
                  data: reportData.counts,
                  backgroundColor: 'rgba(231, 76, 60, 0.7)',
                  borderColor: 'rgba(231, 76, 60, 1)',
                  borderWidth: 1
              }]
          },
          options: { scales: { y: { beginAtZero: true } } }
      });
    });
  </script>
{% endblock %}
''',

    "leaderboard.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Community Leaderboard</h1>
  <ul class="list-group">
    {% for user in users %}
      <li class="list-group-item"><strong>{{ user.username }}</strong> - {{ user.points }} points</li>
    {% endfor %}
  </ul>
{% endblock %}
''',

    "volunteer_chat.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Volunteer Chat Room</h1>
  <div id="chatbox" class="border rounded p-3 mb-3" style="height: 400px; overflow-y: auto; background: #fff;"></div>
  <form id="chat-form" class="d-flex">
    <input type="text" id="chat-message" class="form-control me-2" placeholder="Type your message...">
    <button type="submit" class="btn btn-primary">Send</button>
  </form>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.min.js" crossorigin="anonymous"></script>
  <script>
    var socket = io();
    var chatbox = document.getElementById('chatbox');
    var chatForm = document.getElementById('chat-form');
    var chatMessage = document.getElementById('chat-message');
    socket.on('message', function(msg) {
      var p = document.createElement('p');
      p.innerHTML = msg;
      chatbox.appendChild(p);
      chatbox.scrollTop = chatbox.scrollHeight;
    });
    chatForm.addEventListener('submit', function(e) {
      e.preventDefault();
      if(chatMessage.value.trim()){
        socket.emit('message', "{{ current_user.username }}: " + chatMessage.value);
        chatMessage.value = "";
      }
    });
  </script>
{% endblock %}
''',

    "feedback.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Your Valuable Feedback</h1>
  <form method="POST">
    <div class="mb-3">
      <label class="form-label">Your Feedback:</label>
      <textarea name="message" class="form-control" placeholder="Express your thoughts..." style="height: 150px;"></textarea>
    </div>
    <button type="submit" class="btn btn-primary">Submit Feedback</button>
  </form>
  <h2 class="mt-4">Recent Feedback</h2>
  <ul class="list-group">
    {% for fb in feedbacks %}
      <li class="list-group-item"><strong>{{ fb.user.username if fb.user else "Anonymous" }}:</strong> {{ fb.message }} <em>({{ fb.submitted_at.strftime("%Y-%m-%d %H:%M") }})</em></li>
    {% endfor %}
  </ul>
{% endblock %}
''',

    "donation_map.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Donation Map</h1>
  <div id="map" style="width: 100%; height: 500px;"></div>
  <script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js" crossorigin=""></script>
  <script>
    document.addEventListener("DOMContentLoaded", function() {
      var map = L.map('map').setView([12.9716, 77.5946], 13);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors'
      }).addTo(map);
      var markers = {{ markers|tojson }};
      markers.forEach(function(marker) {
        L.marker([marker.lat, marker.lng]).addTo(map).bindPopup(marker.popup);
      });
      if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(function(position) {
          var userLat = position.coords.latitude;
          var userLng = position.coords.longitude;
          L.marker([userLat, userLng]).addTo(map).bindPopup("You are here.").openPopup();
          map.setView([userLat, userLng], 14);
        }, function(error) {
          console.log("Geolocation error: " + error.message);
        });
      } else {
        console.log("Geolocation is not supported by this browser.");
      }
    });
  </script>
{% endblock %}
''',

    "map.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Reports Map</h1>
  <div id="map" style="width: 100%; height: 500px;"></div>
  <script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js" crossorigin=""></script>
  <script>
    document.addEventListener("DOMContentLoaded", function() {
      var map = L.map('map').setView([12.9716, 77.5946], 13);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors'
      }).addTo(map);
      var markers = {{ markers|tojson }};
      markers.forEach(function(marker) {
        L.marker([marker.lat, marker.lng]).addTo(map).bindPopup(marker.popup);
      });
    });
  </script>
{% endblock %}
''',

    "report_details.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Detailed Report</h1>
  <p><strong>Animal Type:</strong> {{ report.animal_type }}</p>
  <p><strong>Description:</strong> {{ report.description }}</p>
  <p><strong>Location:</strong> {{ report.location }}</p>
  <p><strong>Contact:</strong> {{ report.contact }}</p>
  <p><strong>Reported At:</strong> {{ report.report_time.strftime("%Y-%m-%d %H:%M:%S") }}</p>
  {% if report.image_filename %}
    <p><img src="{{ url_for('static', filename='uploads/' ~ report.image_filename) }}" alt="Report Image" class="img-fluid" style="max-width:400px;"></p>
  {% endif %}
  <p><a href="{{ url_for('map_view') }}" class="btn btn-secondary mt-3">Return to Reports Map</a></p>
{% endblock %}
''',

    "feedback_sentiment.html": '''
{% extends "base.html" %}
{% block content %}
  <h1 class="mb-4">Feedback Sentiment Analysis</h1>
  <ul class="list-group">
    {% for result in sentiment_results %}
      <li class="list-group-item">
        <strong>{{ result.feedback.user.username if result.feedback.user else "Anonymous" }}</strong>: 
        "{{ result.feedback.message }}"<br>
        Polarity: {{ result.polarity }}, Subjectivity: {{ result.subjectivity }}<br>
        Submitted at: {{ result.feedback.submitted_at.strftime("%Y-%m-%d %H:%M") }}
      </li>
    {% endfor %}
  </ul>
  <a href="{{ url_for('feedback') }}" class="btn btn-secondary mt-3">Return to Feedback</a>
{% endblock %}
'''
}

app.jinja_loader = DictLoader(templates)

# --- Manifest and Service Worker (PWA Enhancements) ---
manifest_json = '''
{
  "name": "Strays Bengaluru",
  "short_name": "Strays",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#f4f4f4",
  "theme_color": "#2C3E50",
  "icons": [
    {
      "src": "/static/icons/icon-192.png",
      "sizes": "192x192",
      "type": "image/png"
    },
    {
      "src": "/static/icons/icon-512.png",
      "sizes": "512x512",
      "type": "image/png"
    }
  ]
}
'''

service_worker_js = '''
var CACHE_NAME = 'strays-cache-v1';
var urlsToCache = [
  '/',
  '/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

self.addEventListener('install', function(event) {
  console.log('Service Worker installing.');
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(function(cache) {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
  );
});

self.addEventListener('fetch', function(event) {
  event.respondWith(
    caches.match(event.request)
      .then(function(response) {
        return response || fetch(event.request);
      })
  );
});

self.addEventListener('push', function(event) {
  var options = {
    body: event.data ? event.data.text() : 'New notification from Strays Bengaluru!',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png'
  };
  event.waitUntil(
    self.registration.showNotification('Strays Bengaluru', options)
  );
});
'''

@app.route('/manifest.json', endpoint='manifest_json')
def manifest_route():
    return app.response_class(manifest_json, mimetype='application/json')

@app.route('/sw.js', endpoint='sw_js_route')
def service_worker_route():
    return app.response_class(service_worker_js, mimetype='application/javascript')

# --- New AI/ML Enhancement: Feedback Sentiment Analysis ---
@app.route('/feedback/sentiment', endpoint='feedback_sentiment_unique')
@login_required
def feedback_sentiment():
    feedbacks = Feedback.query.order_by(Feedback.submitted_at.desc()).all()
    sentiment_results = []
    for fb in feedbacks:
        analysis = TextBlob(fb.message)
        sentiment_results.append({
            'feedback': fb,
            'polarity': analysis.sentiment.polarity,
            'subjectivity': analysis.sentiment.subjectivity
        })
    return render_template("feedback_sentiment.html", sentiment_results=sentiment_results)

# --- Main Routes ---
@app.route('/')
def index():
    return render_template("index.html")

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form['email']
        role = request.form['role']
        if User.query.filter_by(email=email).first():
            flash('Email already registered. Please use a different email.')
            return redirect(url_for('register'))
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed_password, email=email, role=role)
        db.session.add(new_user)
        db.session.commit()
        flash('Registration successful! Please login.')
        return redirect(url_for('login'))
    return render_template("register.html")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password')
        return redirect(url_for('login'))
    return render_template("login.html")

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template("dashboard.html")

@app.route('/logout')
@login_required
def logout():
    logout_user()
    # Redirect to login page after logout so that "Access Portal" appears in the login header.
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.username = request.form['username']
        current_user.email = request.form['email']
        db.session.commit()
        flash('Profile updated successfully!')
        return redirect(url_for('dashboard'))
    return render_template("profile.html")

@app.route('/add_donation', methods=['GET', 'POST'])
@login_required
def add_donation():
    if request.method == 'POST':
        description = request.form['description']
        food_type = request.form['food_type']
        quantity = request.form['quantity']
        pickup_location = request.form['pickup_location']
        pickup_time_str = request.form['pickup_time']
        pickup_time = datetime.strptime(pickup_time_str, '%Y-%m-%dT%H:%M')
        new_donation = Donation(
            description=description,
            food_type=food_type,
            quantity=quantity,
            pickup_location=pickup_location,
            pickup_time=pickup_time,
            donor_id=current_user.id
        )
        db.session.add(new_donation)
        current_user.points += 10
        db.session.commit()
        flash('Donation added successfully!')
        send_notification("New Donation", [current_user.email], f"Your donation '{description}' has been added.")
        post_to_twitter(f"New donation added: {description}")
        return redirect(url_for('dashboard'))
    return render_template("add_donation.html")

@app.route('/donations')
@login_required
def donations():
    donations_list = Donation.query.all()
    return render_template("donations.html", donations=donations_list)

@app.route('/report_animal', methods=['GET', 'POST'])
@login_required
def report_animal():
    if request.method == 'POST':
        animal_type = request.form['animal_type']
        description = request.form['description']
        location = request.form['location']
        contact = request.form['contact']
        new_report = Report(
            animal_type=animal_type,
            description=description,
            location=location,
            contact=contact,
            reporter_id=current_user.id
        )
        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                new_report.image_filename = filename
        db.session.add(new_report)
        current_user.points += 5
        db.session.commit()
        flash('Report submitted successfully!')
        send_notification("New Report", [current_user.email], f"Your report for {animal_type} has been submitted.")
        check_proximity(new_report)
        return redirect(url_for('dashboard'))
    return render_template("report_animal.html")

@app.route('/reports')
@login_required
def reports():
    reports_list = Report.query.all()
    return render_template("reports.html", reports=reports_list)

@app.route('/events')
@login_required
def events():
    events_list = Event.query.order_by(Event.event_time).all()
    return render_template("events.html", events=events_list)

@app.route('/signup_event/<int:event_id>', methods=['POST'])
@login_required
def signup_event(event_id):
    event = Event.query.get_or_404(event_id)
    if current_user not in event.participants:
        event.participants.append(current_user)
        db.session.commit()
        flash('Signed up for event successfully!')
    else:
        flash('You are already signed up for this event.')
    return redirect(url_for('events'))

@app.route('/create_event', methods=['GET', 'POST'])
@login_required
def create_event():
    if current_user.role != 'admin':
        flash('You are not authorized to create events.')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        event_time_str = request.form['event_time']
        event_time = datetime.strptime(event_time_str, '%Y-%m-%dT%H:%M')
        location = request.form['location']
        new_event = Event(title=title, description=description, event_time=event_time, location=location)
        db.session.add(new_event)
        db.session.commit()
        flash('Event created successfully!')
        return redirect(url_for('events'))
    return render_template("create_event.html")

@app.route('/stats')
@login_required
def stats():
    if current_user.role != 'admin':
        flash('You are not authorized to view analytics.')
        return redirect(url_for('dashboard'))
    total_donations = Donation.query.count()
    total_quantity = db.session.query(db.func.sum(Donation.quantity)).scalar() or 0
    donation_data = {}
    for d in Donation.query.all():
        key = d.pickup_time.strftime("%Y-%m") if d.pickup_time else "Unknown"
        donation_data[key] = donation_data.get(key, 0) + 1
    sorted_donation_keys = sorted(donation_data.keys())
    donation_chart = {'labels': sorted_donation_keys, 'counts': [donation_data[k] for k in sorted_donation_keys]}
    report_data = {}
    for r in Report.query.all():
        key = r.report_time.strftime("%Y-%m")
        report_data[key] = report_data.get(key, 0) + 1
    sorted_report_keys = sorted(report_data.keys())
    report_chart = {'labels': sorted_report_keys, 'counts': [report_data[k] for k in sorted_report_keys]}
    return render_template("stats.html", donation_data=donation_chart, report_data=report_chart, total_donations=total_donations, total_quantity=total_quantity)

@app.route('/leaderboard')
@login_required
def leaderboard():
    users = User.query.order_by(User.points.desc()).limit(10).all()
    return render_template("leaderboard.html", users=users)

@app.route('/map')
@login_required
def map_view():
    animal_filter = request.args.get('animal_type', '')
    if animal_filter:
        reports_all = Report.query.filter(Report.animal_type.ilike(f'%{animal_filter}%')).all()
    else:
        reports_all = Report.query.all()
    distinct_animals = [r[0] for r in db.session.query(Report.animal_type).distinct().all()]
    markers = []
    heat_data = []
    for report in reports_all:
        if report.latitude is not None and report.longitude is not None:
            lat, lng = report.latitude, report.longitude
        else:
            try:
                location_obj = geolocator.geocode(report.location)
                if location_obj:
                    lat = location_obj.latitude
                    lng = location_obj.longitude
                    report.latitude = lat
                    report.longitude = lng
                    db.session.commit()
                    app.logger.info(f"Geocoded '{report.location}' to: {lat}, {lng}")
                else:
                    app.logger.info(f"Could not geocode address: {report.location}. Using default coordinates.")
                    lat, lng = 12.9716, 77.5946
            except Exception as e:
                app.logger.error(f"Error geocoding address '{report.location}': {e}. Using default coordinates.")
                lat, lng = 12.9716, 77.5946
            time.sleep(1)
        details_link = url_for('report_details', report_id=report.id)
        popup_html = f"<strong>{report.animal_type}</strong><br>{report.description}<br><em>{report.location}</em><br><a href='{details_link}' target='_blank'>View Details</a>"
        markers.append({"lat": lat, "lng": lng, "popup": popup_html})
        heat_data.append([lat, lng, 1])
    app.logger.info(f"Markers for reports map: {markers}")
    return render_template("map.html", markers=markers, distinct_animals=distinct_animals, selected_animal=animal_filter, heat_data=heat_data)

@app.route('/donation_map')
@login_required
def donation_map():
    donations_all = Donation.query.all()
    markers = []
    for donation in donations_all:
        if donation.pickup_latitude is not None and donation.pickup_longitude is not None:
            lat, lng = donation.pickup_latitude, donation.pickup_longitude
        else:
            try:
                location_obj = geolocator.geocode(donation.pickup_location)
                if location_obj:
                    lat = location_obj.latitude
                    lng = location_obj.longitude
                    donation.pickup_latitude = lat
                    donation.pickup_longitude = lng
                    db.session.commit()
                    app.logger.info(f"Geocoded donation '{donation.pickup_location}' to: {lat}, {lng}")
                else:
                    app.logger.info(f"Could not geocode donation location: {donation.pickup_location}")
                    continue
                time.sleep(1)
            except Exception as e:
                app.logger.error(f"Error geocoding donation location '{donation.pickup_location}': {e}")
                continue
        markers.append({"lat": lat, "lng": lng, "popup": f"Donation: {donation.description}"})
    return render_template("donation_map.html", markers=markers)

@app.route('/report_details/<int:report_id>')
@login_required
def report_details(report_id):
    report = Report.query.get_or_404(report_id)
    return render_template("report_details.html", report=report)

@app.route('/feedback', methods=['GET', 'POST'])
@login_required
def feedback():
    if request.method == 'POST':
        message = request.form['message']
        new_feedback = Feedback(user_id=current_user.id, message=message)
        db.session.add(new_feedback)
        db.session.commit()
        flash('Thank you for your feedback!')
        return redirect(url_for('feedback'))
    feedbacks = Feedback.query.order_by(Feedback.submitted_at.desc()).limit(10).all()
    return render_template("feedback.html", feedbacks=feedbacks)

@app.route('/volunteer_chat')
@login_required
def volunteer_chat():
    return render_template("volunteer_chat.html")

# --- SocketIO Events ---
@socketio.on('message')
def handle_message(msg):
    app.logger.info('Message: ' + msg)
    send(msg, broadcast=True)

# --- Database Creation ---
with app.app_context():
    db.create_all()

# --- Run the App ---
if __name__ == '__main__':
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)
