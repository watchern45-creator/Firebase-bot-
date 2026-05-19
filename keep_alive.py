"""
keep_alive.py
Render free tier ke liye Flask server.
CronJob is URL ko ping karega bot ko jaagta rakhne ke liye.
"""
from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive!", 200

@app.route("/health")
def health():
    return {"status": "ok"}, 200

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
