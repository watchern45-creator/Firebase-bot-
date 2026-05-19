# main.py — Is file ko run karo (bot + keep_alive dono)
from keep_alive import keep_alive
keep_alive()  # Flask server start (Render ke liye)

from bot import main
main()
