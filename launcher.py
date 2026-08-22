"""Start the local API and open the web interface after it is ready."""
import threading
import time
import urllib.request
import webbrowser
from urllib.error import URLError

import uvicorn


APP_URL = "http://127.0.0.1:8000/"
META_URL = APP_URL + "api/v3/meta"


def open_when_ready():
    """Wait for FastAPI startup and then open the page once."""
    for _ in range(120):
        try:
            with urllib.request.urlopen(META_URL, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(APP_URL)
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.5)


if __name__ == "__main__":
    threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("api:app", host="127.0.0.1", port=8000)
