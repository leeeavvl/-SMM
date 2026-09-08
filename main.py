"""Контент-завод — точка входа приложения.

Запускает FastAPI-сервер и открывает веб-интерфейс в браузере.
"""
import threading
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8000


def open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    threading.Timer(1.0, open_browser).start()
    uvicorn.run("app.app:app", host=HOST, port=PORT, reload=False)
