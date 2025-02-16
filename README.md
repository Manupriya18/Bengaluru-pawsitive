# Bengaluru-pawsitive
PROFILA platform that connects animal lovers, potential adopters, and rescue organizations. It aims to streamline the process of animal adoption while providing a secure environment for users to interact, also connects volunteers and people who want to donate food for stray animals. Skills: Flask Framework, SQLAlchemy
# Bengaluru pawsitive

Bengaluru pawsitive is a full-featured web application designed to support stray animals through generous donations and community volunteerism. The platform allows users to register, donate food, report stray animal sightings, join community events, and communicate in real time—all wrapped in a modern, responsive, and progressive web app (PWA) experience.

## Features

- **User Authentication & Role Management:**  
  Users can register and log in with roles such as Donor, Volunteer, or Admin. Admins can create events and view analytics.

- **Donation & Reporting System:**  
  Users can contribute donations and report stray animal sightings, including details like location, pickup time, and more.

- **Maps Integration:**  
  The application uses Leaflet with OpenStreetMap tiles and geopy for geocoding to display interactive maps for donations and reports.

- **Real-Time Analytics Dashboard:**  
  Admin users can view interactive charts (powered by Chart.js) that display donation and report statistics.

- **Progressive Web App (PWA) Enhancements:**  
  The app includes a manifest and a service worker for offline caching and push notifications, making it installable as a standalone app.

- **Real-Time Chat:**  
  Volunteer chat functionality is provided via Flask-SocketIO for instant messaging.

- **Feedback & Sentiment Analysis:**  
  Users can submit feedback, which is analyzed using TextBlob to determine sentiment.

- **Robust Logging & Monitoring:**  
  Logging is implemented with Python’s RotatingFileHandler, and Sentry integration is available for error tracking.

- **Automated Testing:**  
  A suite of unit tests is included to ensure code quality and maintainability.

## Technology Stack

- **Backend:** Python, Flask, Flask-SQLAlchemy, Flask-Login, Flask-Migrate, Flask-SocketIO  
- **Frontend:** Bootstrap 5, Leaflet, Chart.js  
- **Geocoding:** geopy (Nominatim)  
- **PWA:** Service Worker, Manifest  
- **Monitoring:** Python Logging, Sentry (optional)  
- **Testing:** unittest

## Installation

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/Manupriya18/Bengaluru-pawsitive.git
   cd Bengaluru-pawsitive
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
pip install -r requirements.txt
python -m textblob.download_corpora
flask db init
flask db migrate -m "Initial migration."
flask db upgrade
python app.py
